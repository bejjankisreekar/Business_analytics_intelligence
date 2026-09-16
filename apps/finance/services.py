"""Aggregation + financial-statement calculations over the current
tenant's SalesEntry / ExpenseEntry / PurchaseEntry tables.

Every function here assumes the DB connection's search_path has already
been pointed at the right organization schema (TenantSchemaMiddleware
does this for normal requests) — nothing here filters by organization,
because isolation is structural (a different schema per org), not a
column.
"""
import datetime
from decimal import Decimal

from django.db.models import Count, F, Sum
from django.db.models.functions import TruncMonth
from django.utils import timezone

from .models import (
    CashTransfer,
    ExpenseEntry,
    FinanceSettings,
    Partner,
    PartnerTransaction,
    Payable,
    PaymentMode,
    PurchaseEntry,
    Receivable,
    SalesEntry,
    Subcategory,
)
from .periods import Period, previous_period, resolve_period

ZERO = Decimal("0")


def get_finance_settings() -> FinanceSettings:
    settings_row = FinanceSettings.objects.first()
    if settings_row is None:
        settings_row = FinanceSettings.objects.create(
            fy_start_month=4, opening_balance=ZERO, opening_date=datetime.date.today()
        )
    return settings_row


def subcategory_map() -> dict:
    """{category_id: [{"id": subcategory_id, "name": ...}, ...]} for every
    active subcategory — embedded client-side so the quick-add forms can
    narrow the subcategory dropdown to the chosen category without a
    round trip."""
    out: dict[str, list] = {}
    for sub in Subcategory.objects.filter(is_active=True).order_by("name"):
        out.setdefault(str(sub.category_id), []).append({"id": str(sub.id), "name": sub.name})
    return out


def _sum(qs) -> Decimal:
    return qs.aggregate(total=Sum("amount"))["total"] or ZERO


def sales_total(start: datetime.date, end: datetime.date) -> Decimal:
    return _sum(SalesEntry.objects.filter(date__gte=start, date__lte=end))


def expenses_total(start: datetime.date, end: datetime.date) -> Decimal:
    return _sum(ExpenseEntry.objects.filter(date__gte=start, date__lte=end))


def purchases_total(start: datetime.date, end: datetime.date) -> Decimal:
    return _sum(PurchaseEntry.objects.filter(date__gte=start, date__lte=end))


def net_profit_for(start: datetime.date, end: datetime.date) -> Decimal:
    return sales_total(start, end) - expenses_total(start, end) - purchases_total(start, end)


def partner_flow(start: datetime.date, end: datetime.date) -> tuple[Decimal, Decimal]:
    """(total invested, total withdrawn) by partners in the period — real
    cash/bank movement, but never P&L (equity, not revenue or an expense)."""
    invested = _sum(PartnerTransaction.objects.filter(
        date__gte=start, date__lte=end, kind=PartnerTransaction.Kind.INVESTMENT))
    withdrawn = _sum(PartnerTransaction.objects.filter(
        date__gte=start, date__lte=end, kind=PartnerTransaction.Kind.WITHDRAWAL))
    return invested, withdrawn


def total_balance_as_of(as_of: datetime.date) -> Decimal:
    """Cash-in-hand + bank, combined — unaffected by transfers between the
    two, since those just move money from one pool to the other."""
    fs = get_finance_settings()
    if as_of < fs.opening_date:
        return fs.opening_balance + fs.opening_bank_balance
    invested, withdrawn = partner_flow(fs.opening_date, as_of)
    return fs.opening_balance + fs.opening_bank_balance + net_profit_for(fs.opening_date, as_of) + invested - withdrawn


def cash_and_bank_as_of(as_of: datetime.date) -> tuple[Decimal, Decimal]:
    """(cash-in-hand, bank) balances as of `as_of`, accounting for each
    entry's payment mode, every deposit/withdrawal transfer, and every
    partner investment/withdrawal."""
    fs = get_finance_settings()
    if as_of < fs.opening_date:
        return fs.opening_balance, fs.opening_bank_balance

    start = fs.opening_date

    def by_mode(model, mode):
        return _sum(model.objects.filter(date__gte=start, date__lte=as_of, payment_mode=mode))

    def partner_by_mode(kind, mode):
        return _sum(PartnerTransaction.objects.filter(
            date__gte=start, date__lte=as_of, kind=kind, payment_mode=mode))

    cash_in = by_mode(SalesEntry, PaymentMode.CASH) + partner_by_mode(PartnerTransaction.Kind.INVESTMENT, PaymentMode.CASH)
    cash_out = (by_mode(ExpenseEntry, PaymentMode.CASH) + by_mode(PurchaseEntry, PaymentMode.CASH)
                + partner_by_mode(PartnerTransaction.Kind.WITHDRAWAL, PaymentMode.CASH))
    bank_in = by_mode(SalesEntry, PaymentMode.BANK) + partner_by_mode(PartnerTransaction.Kind.INVESTMENT, PaymentMode.BANK)
    bank_out = (by_mode(ExpenseEntry, PaymentMode.BANK) + by_mode(PurchaseEntry, PaymentMode.BANK)
                + partner_by_mode(PartnerTransaction.Kind.WITHDRAWAL, PaymentMode.BANK))

    transfers = CashTransfer.objects.filter(date__gte=start, date__lte=as_of)
    to_bank = _sum(transfers.filter(direction=CashTransfer.Direction.CASH_TO_BANK))
    to_cash = _sum(transfers.filter(direction=CashTransfer.Direction.BANK_TO_CASH))

    cash = fs.opening_balance + cash_in - cash_out - to_bank + to_cash
    bank = fs.opening_bank_balance + bank_in - bank_out + to_bank - to_cash
    return cash, bank


def partner_balance_rows() -> list[dict]:
    """Every partner with their all-time invested/withdrawn/net capital,
    for the Cash Position page's Partners table."""
    rows = []
    for p in Partner.objects.all():
        invested = _sum(p.transactions.filter(kind=PartnerTransaction.Kind.INVESTMENT))
        withdrawn = _sum(p.transactions.filter(kind=PartnerTransaction.Kind.WITHDRAWAL))
        rows.append({
            "partner": p,
            "invested": invested,
            "withdrawn": withdrawn,
            "net_capital": invested - withdrawn,
        })
    return rows


def partner_ledger_entries(partner: Partner) -> list[dict]:
    """One partner's full investment/withdrawal history in date order with
    a running balance — investments are debits (capital in), withdrawals
    are credits (capital out), mirroring how vendor/customer ledgers read."""
    entries = []
    balance = ZERO
    txns = partner.transactions.order_by("date", "created_at")
    for t in txns:
        if t.kind == PartnerTransaction.Kind.INVESTMENT:
            balance += t.amount
            debit, credit = t.amount, ZERO
        else:
            balance -= t.amount
            debit, credit = ZERO, t.amount
        entries.append({
            "date": t.date,
            "particular": t.note or t.get_kind_display(),
            "debit": debit,
            "credit": credit,
            "balance": balance,
        })
    return entries


def kpis_for_period(period: Period) -> dict:
    sales = sales_total(period.start, period.end)
    expenses = expenses_total(period.start, period.end)
    purchases = purchases_total(period.start, period.end)
    net = sales - expenses - purchases
    margin = (net / sales * 100) if sales else ZERO

    prev = previous_period(period)
    prev_sales = sales_total(prev.start, prev.end)
    prev_net = prev_sales - expenses_total(prev.start, prev.end) - purchases_total(prev.start, prev.end)

    def growth(curr, prior):
        if not prior:
            return None
        return (curr - prior) / abs(prior) * 100

    return {
        "sales": sales,
        "expenses": expenses,
        "purchases": purchases,
        "net_profit": net,
        "margin_pct": margin,
        "cash_balance": total_balance_as_of(period.end),
        "sales_growth_pct": growth(sales, prev_sales),
        "net_growth_pct": growth(net, prev_net),
    }


def daily_series(start: datetime.date, end: datetime.date) -> list[dict]:
    sales_by_day = {
        row["date"]: row["total"]
        for row in SalesEntry.objects.filter(date__gte=start, date__lte=end)
        .values("date").annotate(total=Sum("amount"))
    }
    expenses_by_day = {
        row["date"]: row["total"]
        for row in ExpenseEntry.objects.filter(date__gte=start, date__lte=end)
        .values("date").annotate(total=Sum("amount"))
    }
    purchases_by_day = {
        row["date"]: row["total"]
        for row in PurchaseEntry.objects.filter(date__gte=start, date__lte=end)
        .values("date").annotate(total=Sum("amount"))
    }

    series = []
    day = start
    while day <= end:
        sales = sales_by_day.get(day, ZERO)
        expenses = expenses_by_day.get(day, ZERO)
        purchases = purchases_by_day.get(day, ZERO)
        series.append({
            "date": day,
            "sales": sales,
            "expenses": expenses,
            "purchases": purchases,
            "net": sales - expenses - purchases,
        })
        day += datetime.timedelta(days=1)
    return series


def weekday_averages(start: datetime.date, end: datetime.date) -> list[dict]:
    """Average sales for each weekday across the period — helps spot best/worst days."""
    totals = [ZERO] * 7
    counts = [0] * 7
    for row in SalesEntry.objects.filter(date__gte=start, date__lte=end).values("date", "amount"):
        idx = row["date"].weekday()
        totals[idx] += row["amount"]
        counts[idx] += 1
    names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    return [
        {"day": names[i], "average": (totals[i] / counts[i]) if counts[i] else ZERO}
        for i in range(7)
    ]


def category_breakdown(model, start: datetime.date, end: datetime.date, field: str = "category") -> list[dict]:
    qs = model.objects.filter(date__gte=start, date__lte=end)
    lookup = f"{field}__name"
    rows = qs.values(lookup).annotate(total=Sum("amount")).order_by("-total")
    return [{"name": row[lookup] or "Uncategorized", "amount": row["total"]} for row in rows]


def product_quantity_breakdown(start: datetime.date, end: datetime.date) -> list[dict]:
    """Units sold per product (SalesEntry.subcategory) in the period, most
    units first. Entries without a quantity recorded are excluded."""
    rows = (
        SalesEntry.objects.filter(date__gte=start, date__lte=end, quantity__isnull=False)
        .values("subcategory__name").annotate(total=Sum("quantity")).order_by("-total")
    )
    return [{"name": row["subcategory__name"] or "Uncategorized", "quantity": row["total"]} for row in rows]


def payment_mode_breakdown(start: datetime.date, end: datetime.date, models=None) -> list[dict]:
    """How much of the period's total money movement went through cash vs
    bank — across sales + expenses + purchases by default, or just the
    model(s) passed in."""
    totals = {PaymentMode.CASH: ZERO, PaymentMode.BANK: ZERO}
    for model in models or (SalesEntry, ExpenseEntry, PurchaseEntry):
        for row in (
            model.objects.filter(date__gte=start, date__lte=end)
            .values("payment_mode").annotate(total=Sum("amount"))
        ):
            totals[row["payment_mode"]] = totals.get(row["payment_mode"], ZERO) + row["total"]
    labels = dict(PaymentMode.choices)
    return [{"name": labels[mode], "amount": amount} for mode, amount in totals.items()]


def vendor_breakdown(start: datetime.date, end: datetime.date) -> list[dict]:
    """Purchases grouped by vendor (free-text field) — top vendors by spend."""
    rows = (
        PurchaseEntry.objects.filter(date__gte=start, date__lte=end)
        .values("vendor").annotate(total=Sum("amount")).order_by("-total")
    )
    return [{"name": row["vendor"] or "Unspecified vendor", "amount": row["total"]} for row in rows]


def weeks_since(start: datetime.date, end: datetime.date) -> int:
    """How many weekly buckets weekly_trend needs to reach back to `start`."""
    if start > end:
        return 1
    start_monday = start - datetime.timedelta(days=start.weekday())
    end_monday = end - datetime.timedelta(days=end.weekday())
    return ((end_monday - start_monday).days // 7) + 1


def months_since(start: datetime.date, end: datetime.date) -> int:
    """How many monthly buckets monthly_trend needs to reach back to `start`."""
    if start > end:
        return 1
    return (end.year - start.year) * 12 + (end.month - start.month) + 1


def weekly_trend(weeks: int = 12, *, end: datetime.date | None = None) -> list[dict]:
    """Last `weeks` calendar weeks (Mon-Sun) of sales/expenses/purchases/net, oldest first."""
    end = end or datetime.date.today()
    week_end = end
    week_start = week_end - datetime.timedelta(days=week_end.weekday())
    start = week_start - datetime.timedelta(weeks=weeks - 1)

    rows = daily_series(start, end)
    buckets: dict[str, dict] = {}
    order = []
    for row in rows:
        bucket_start = row["date"] - datetime.timedelta(days=row["date"].weekday())
        key = bucket_start.isoformat()
        if key not in buckets:
            buckets[key] = {
                "label": f"{bucket_start:%d %b}",
                "sales": ZERO, "expenses": ZERO, "purchases": ZERO, "net": ZERO,
            }
            order.append(key)
        buckets[key]["sales"] += row["sales"]
        buckets[key]["expenses"] += row["expenses"]
        buckets[key]["purchases"] += row["purchases"]
        buckets[key]["net"] += row["net"]
    return [buckets[k] for k in order]


def monthly_trend(months: int = 6, *, end: datetime.date | None = None) -> list[dict]:
    """Last `months` calendar months of sales/expenses/purchases/net, oldest first."""
    end = end or datetime.date.today()
    start = end.replace(day=1)
    for _ in range(months - 1):
        start = (start - datetime.timedelta(days=1)).replace(day=1)

    def monthly(model):
        return {
            row["m"].strftime("%Y-%m"): row["total"]
            for row in model.objects.filter(date__gte=start, date__lte=end)
            .annotate(m=TruncMonth("date")).values("m").annotate(total=Sum("amount"))
        }

    sales_m = monthly(SalesEntry)
    expenses_m = monthly(ExpenseEntry)
    purchases_m = monthly(PurchaseEntry)

    out = []
    cursor = start
    while cursor <= end:
        key = cursor.strftime("%Y-%m")
        sales = sales_m.get(key, ZERO)
        expenses = expenses_m.get(key, ZERO)
        purchases = purchases_m.get(key, ZERO)
        out.append({
            "month": cursor.strftime("%b %Y"),
            "sales": sales,
            "expenses": expenses,
            "purchases": purchases,
            "net": sales - expenses - purchases,
        })
        cursor = (cursor.replace(day=28) + datetime.timedelta(days=4)).replace(day=1)
    return out


def _label_with_sub(base: str, subcategory) -> str:
    return f"{base} · {subcategory.name}" if subcategory else base


def recent_entries(limit: int = 10) -> list[dict]:
    entries = []
    for entry in SalesEntry.objects.select_related("channel", "subcategory").order_by("-date", "-created_at")[:limit]:
        entries.append({
            "type": "Sale", "date": entry.date, "amount": entry.amount,
            "label": _label_with_sub(entry.channel.name if entry.channel else "Sales", entry.subcategory),
            "note": entry.note,
        })
    for entry in ExpenseEntry.objects.select_related("category", "subcategory").order_by("-date", "-created_at")[:limit]:
        entries.append({
            "type": "Expense", "date": entry.date, "amount": entry.amount,
            "label": _label_with_sub(entry.category.name if entry.category else "Expense", entry.subcategory),
            "note": entry.note,
        })
    for entry in PurchaseEntry.objects.select_related("category", "subcategory").order_by("-date", "-created_at")[:limit]:
        entries.append({
            "type": "Purchase", "date": entry.date, "amount": entry.amount,
            "label": _label_with_sub(entry.category.name if entry.category else "Purchase", entry.subcategory),
            "note": entry.note,
        })
    entries.sort(key=lambda e: e["date"], reverse=True)
    return entries[:limit]


# ---------------------------------------------------------------------------
# Financial statements
# ---------------------------------------------------------------------------

def profit_and_loss(period: Period) -> dict:
    revenue = sales_total(period.start, period.end)
    cogs = purchases_total(period.start, period.end)
    gross_profit = revenue - cogs
    opex_rows = category_breakdown(ExpenseEntry, period.start, period.end)
    total_opex = sum((row["amount"] for row in opex_rows), ZERO)
    net_profit = gross_profit - total_opex
    net_margin = (net_profit / revenue * 100) if revenue else ZERO

    prev = previous_period(period)
    prev_revenue = sales_total(prev.start, prev.end)
    prev_net = (
        prev_revenue
        - purchases_total(prev.start, prev.end)
        - expenses_total(prev.start, prev.end)
    )

    return {
        "period": period,
        "revenue": revenue,
        "cogs": cogs,
        "gross_profit": gross_profit,
        "opex_rows": opex_rows,
        "total_opex": total_opex,
        "net_profit": net_profit,
        "net_margin_pct": net_margin,
        "prev_revenue": prev_revenue,
        "prev_net_profit": prev_net,
    }


def balance_sheet(as_of: datetime.date) -> dict:
    fs = get_finance_settings()
    cumulative_net = net_profit_for(fs.opening_date, as_of) if as_of >= fs.opening_date else ZERO
    cash, bank = cash_and_bank_as_of(as_of)
    opening_capital = fs.opening_balance + fs.opening_bank_balance
    total_assets = cash + bank
    equity = opening_capital + cumulative_net
    return {
        "as_of": as_of,
        "opening_date": fs.opening_date,
        "opening_balance": opening_capital,
        "cash": cash,
        "bank": bank,
        "total_assets": total_assets,
        "total_liabilities": ZERO,
        "retained_earnings": cumulative_net,
        "total_equity": equity,
        "balances": total_assets == equity,
    }


def _cash_bank_split(entries, label_fn) -> list[dict]:
    """Group entries by `label_fn(entry)` and split each group's total into
    cash vs bank — surfaces cases like the same product sold partly for
    cash and partly to bank, which a flat per-entry list hides."""
    groups: dict[str, dict] = {}
    order: list[str] = []
    for entry in entries:
        label = label_fn(entry)
        if label not in groups:
            groups[label] = {"label": label, "cash": ZERO, "bank": ZERO, "total": ZERO}
            order.append(label)
        row = groups[label]
        if entry.payment_mode == PaymentMode.CASH:
            row["cash"] += entry.amount
        else:
            row["bank"] += entry.amount
        row["total"] += entry.amount
    return [groups[key] for key in order]


def daily_report(day: datetime.date) -> dict:
    """A cashier-style daybook for one date: income on one side, outgoings
    on the other, opening/closing cash — everything logged for that day,
    broken down line by line."""
    opening_cash, opening_bank = cash_and_bank_as_of(day - datetime.timedelta(days=1))

    sales = list(
        SalesEntry.objects.filter(date=day).select_related("channel", "subcategory").order_by("created_at")
    )
    expenses = list(
        ExpenseEntry.objects.filter(date=day).select_related("category", "subcategory").order_by("created_at")
    )
    purchases = list(
        PurchaseEntry.objects.filter(date=day).select_related("category", "subcategory").order_by("created_at")
    )
    transfers = list(CashTransfer.objects.filter(date=day).order_by("created_at"))
    partner_txns = list(
        PartnerTransaction.objects.filter(date=day).select_related("partner").order_by("created_at")
    )

    total_sales = sum((e.amount for e in sales), ZERO)
    total_expenses = sum((e.amount for e in expenses), ZERO)
    total_purchases = sum((e.amount for e in purchases), ZERO)
    total_outgoing = total_expenses + total_purchases
    net = total_sales - total_outgoing

    to_bank = sum((t.amount for t in transfers if t.direction == CashTransfer.Direction.CASH_TO_BANK), ZERO)
    to_cash = sum((t.amount for t in transfers if t.direction == CashTransfer.Direction.BANK_TO_CASH), ZERO)

    invested = [t for t in partner_txns if t.kind == PartnerTransaction.Kind.INVESTMENT]
    withdrawn = [t for t in partner_txns if t.kind == PartnerTransaction.Kind.WITHDRAWAL]
    total_partner_invested = sum((t.amount for t in invested), ZERO)
    total_partner_withdrawn = sum((t.amount for t in withdrawn), ZERO)

    cash_in = (sum((e.amount for e in sales if e.payment_mode == PaymentMode.CASH), ZERO) + to_cash
               + sum((t.amount for t in invested if t.payment_mode == PaymentMode.CASH), ZERO))
    cash_out = (
        sum((e.amount for e in expenses if e.payment_mode == PaymentMode.CASH), ZERO)
        + sum((e.amount for e in purchases if e.payment_mode == PaymentMode.CASH), ZERO)
        + to_bank
        + sum((t.amount for t in withdrawn if t.payment_mode == PaymentMode.CASH), ZERO)
    )
    bank_in = (sum((e.amount for e in sales if e.payment_mode == PaymentMode.BANK), ZERO) + to_bank
               + sum((t.amount for t in invested if t.payment_mode == PaymentMode.BANK), ZERO))
    bank_out = (
        sum((e.amount for e in expenses if e.payment_mode == PaymentMode.BANK), ZERO)
        + sum((e.amount for e in purchases if e.payment_mode == PaymentMode.BANK), ZERO)
        + to_cash
        + sum((t.amount for t in withdrawn if t.payment_mode == PaymentMode.BANK), ZERO)
    )

    closing_cash = opening_cash + cash_in - cash_out
    closing_bank = opening_bank + bank_in - bank_out

    sales_split = _cash_bank_split(
        sales, lambda e: _label_with_sub(e.channel.name if e.channel else "Sales", e.subcategory)
    )

    return {
        "date": day,
        "opening_cash": opening_cash,
        "opening_bank": opening_bank,
        "opening_total": opening_cash + opening_bank,
        "sales": sales,
        "sales_split": sales_split,
        "expenses": expenses,
        "purchases": purchases,
        "transfers": transfers,
        "partner_transactions": partner_txns,
        "total_sales": total_sales,
        "total_expenses": total_expenses,
        "total_purchases": total_purchases,
        "total_outgoing": total_outgoing,
        "total_to_bank": to_bank,
        "total_to_cash": to_cash,
        "total_partner_invested": total_partner_invested,
        "total_partner_withdrawn": total_partner_withdrawn,
        "net": net,
        "closing_cash": closing_cash,
        "closing_bank": closing_bank,
        "closing_total": closing_cash + closing_bank,
        "entry_count": len(sales) + len(expenses) + len(purchases) + len(transfers) + len(partner_txns),
        "outgoing_count": len(expenses) + len(purchases),
    }


def product_category_profit(start: datetime.date, end: datetime.date) -> list[dict]:
    """Real profit per product category — sales revenue tagged with that
    category minus purchases tagged with the same category. Deliberately
    coarser than per-product COGS (impractical for most businesses to log
    unit-by-unit), and doesn't allocate operating expenses, which aren't
    attributable to a single category."""
    revenue = {
        row["product_category__name"]: row["total"]
        for row in SalesEntry.objects.filter(date__gte=start, date__lte=end, product_category__isnull=False)
        .values("product_category__name").annotate(total=Sum("amount"))
    }
    cost = {
        row["product_category__name"]: row["total"]
        for row in PurchaseEntry.objects.filter(date__gte=start, date__lte=end, product_category__isnull=False)
        .values("product_category__name").annotate(total=Sum("amount"))
    }
    rows = []
    for name in sorted(set(revenue) | set(cost)):
        rev = revenue.get(name, ZERO)
        cost_amt = cost.get(name, ZERO)
        profit = rev - cost_amt
        rows.append({
            "name": name,
            "revenue": rev,
            "cost": cost_amt,
            "profit": profit,
            "margin_pct": (profit / rev * 100) if rev else ZERO,
        })
    rows.sort(key=lambda r: r["profit"], reverse=True)
    return rows


def receivables_open():
    return Receivable.objects.select_related("customer", "product_category").exclude(
        amount_received__gte=F("amount")
    ).order_by("due_date")


def payables_open():
    return Payable.objects.select_related("product_category").exclude(
        amount_paid__gte=F("amount")
    ).order_by("due_date")


def receivables_total_outstanding() -> Decimal:
    total = ZERO
    for r in receivables_open():
        total += r.balance
    return total


def payables_total_outstanding() -> Decimal:
    total = ZERO
    for p in payables_open():
        total += p.balance
    return total


def vendor_outstanding_map() -> dict:
    """{vendor name: total outstanding balance} from open payables — matched
    by the free-text vendor name, since Payable.vendor isn't an FK."""
    totals: dict[str, Decimal] = {}
    for p in payables_open():
        totals[p.vendor] = totals.get(p.vendor, ZERO) + p.balance
    return totals


def customer_outstanding_map() -> dict:
    """{customer_id: total outstanding balance} from open receivables."""
    totals: dict = {}
    for r in receivables_open():
        totals[r.customer_id] = totals.get(r.customer_id, ZERO) + r.balance
    return totals


def customer_ledger_entries(customer) -> list[dict]:
    """The running-balance ledger for one customer: every Receivable
    (invoice raised) as a debit, and — for any receivable that's been
    (partly) collected — a matching "Payment received" credit for
    `amount_received`, oldest first, with a running balance.

    Credits are deliberately NOT sourced from SalesEntry: a customer's
    name can be tagged on an ordinary, already-settled sale that was never
    invoiced as a Receivable at all (this app doesn't require one), and
    counting every such sale as a "payment" would net it against a debit
    that was never created — producing a wrong, often deeply negative,
    balance. `Receivable.amount_received` is the one field Cash Position
    and the outstanding-balance total elsewhere actually rely on, so
    building the ledger from it keeps this page in agreement with the
    rest of the app, at the cost of dating each payment on its invoice's
    date rather than the (untracked) date it was actually collected.
    """
    rows = []
    for r in Receivable.objects.filter(customer=customer).order_by("invoice_date", "created_at"):
        rows.append({
            "date": r.invoice_date,
            "created_at": r.created_at,
            "particular": r.note or "Invoice raised",
            "debit": r.amount,
            "credit": ZERO,
        })
        if r.amount_received > 0:
            rows.append({
                "date": r.invoice_date,
                "created_at": r.created_at + datetime.timedelta(microseconds=1),
                "particular": "Payment received",
                "debit": ZERO,
                "credit": r.amount_received,
            })

    rows.sort(key=lambda row: (row["date"], row["created_at"]))

    balance = ZERO
    for row in rows:
        balance += row["debit"] - row["credit"]
        row["balance"] = balance
    return rows


def vendor_ledger_entries(vendor_name: str) -> list[dict]:
    """The running-balance ledger for one vendor, mirroring
    customer_ledger_entries: every Payable (bill received) as a debit,
    and — for any payable that's been (partly) paid — a matching
    "Payment made" credit for `amount_paid`.

    Credits come from Payable.amount_paid, not from matching
    PurchaseEntry rows by vendor name: that free-text field is used for
    every ordinary purchase from that vendor, invoiced or not, so
    treating all of them as "payments" would net them against bills that
    were never raised — this is what produced wildly wrong negative
    balances before the fix (e.g. a vendor with zero open payables
    showing lakhs "owed" to them). amount_paid is the field
    vendor_outstanding_map and Cash Position already trust.
    """
    rows = []
    for p in Payable.objects.filter(vendor=vendor_name).order_by("bill_date", "created_at"):
        rows.append({
            "date": p.bill_date,
            "created_at": p.created_at,
            "particular": p.note or "Bill received",
            "debit": p.amount,
            "credit": ZERO,
        })
        if p.amount_paid > 0:
            rows.append({
                "date": p.bill_date,
                "created_at": p.created_at + datetime.timedelta(microseconds=1),
                "particular": "Payment made",
                "debit": ZERO,
                "credit": p.amount_paid,
            })

    rows.sort(key=lambda row: (row["date"], row["created_at"]))

    balance = ZERO
    for row in rows:
        balance += row["debit"] - row["credit"]
        row["balance"] = balance
    return rows


def account_ledger_entries(account: str) -> list[dict]:
    """The running-balance ledger for the Cash-in-hand or Bank account
    (`account` is a PaymentMode value): opening balance, every sale as a
    debit (money in), every expense/purchase as a credit (money out), and
    every CashTransfer moving money into or out of this account — the same
    events and math cash_and_bank_as_of() totals up, laid out as a ledger
    instead of a single balance."""
    fs = get_finance_settings()
    opening = fs.opening_balance if account == PaymentMode.CASH else fs.opening_bank_balance

    rows = [{
        "date": fs.opening_date,
        "created_at": datetime.datetime(1970, 1, 1, tzinfo=datetime.timezone.utc),
        "particular": "Opening balance",
        "debit": opening if opening >= 0 else ZERO,
        "credit": -opening if opening < 0 else ZERO,
    }]

    for s in SalesEntry.objects.filter(payment_mode=account, date__gte=fs.opening_date).select_related(
        "channel", "subcategory"
    ):
        rows.append({
            "date": s.date,
            "created_at": s.created_at,
            "particular": s.note or (s.channel.name if s.channel else "Sale"),
            "debit": s.amount,
            "credit": ZERO,
        })

    for e in ExpenseEntry.objects.filter(payment_mode=account, date__gte=fs.opening_date).select_related(
        "category", "subcategory"
    ):
        rows.append({
            "date": e.date,
            "created_at": e.created_at,
            "particular": e.note or (e.category.name if e.category else "Expense"),
            "debit": ZERO,
            "credit": e.amount,
        })

    for pu in PurchaseEntry.objects.filter(payment_mode=account, date__gte=fs.opening_date).select_related(
        "category", "subcategory"
    ):
        rows.append({
            "date": pu.date,
            "created_at": pu.created_at,
            "particular": pu.note or (pu.category.name if pu.category else "Purchase"),
            "debit": ZERO,
            "credit": pu.amount,
        })

    in_direction = CashTransfer.Direction.BANK_TO_CASH if account == PaymentMode.CASH else CashTransfer.Direction.CASH_TO_BANK
    out_direction = CashTransfer.Direction.CASH_TO_BANK if account == PaymentMode.CASH else CashTransfer.Direction.BANK_TO_CASH
    for t in CashTransfer.objects.filter(date__gte=fs.opening_date):
        label = t.note or t.get_direction_display()
        if t.direction == in_direction:
            rows.append({"date": t.date, "created_at": t.created_at, "particular": label, "debit": t.amount, "credit": ZERO})
        elif t.direction == out_direction:
            rows.append({"date": t.date, "created_at": t.created_at, "particular": label, "debit": ZERO, "credit": t.amount})

    rows.sort(key=lambda row: (row["date"], row["created_at"]))

    balance = ZERO
    for row in rows:
        balance += row["debit"] - row["credit"]
        row["balance"] = balance
    return rows


def expense_month_comparison(fy_start_month: int) -> dict:
    """Category-wise expense comparison, this calendar month vs last, plus
    an auto-generated insight when one category's growth is outpacing
    sales growth by a wide margin — surfaces the "why" behind a rising
    expense total instead of just the flat number."""
    this_month = resolve_period("this_month", fy_start_month)
    last_month = resolve_period("last_month", fy_start_month)

    this_amounts = {row["name"]: row["amount"] for row in category_breakdown(ExpenseEntry, this_month.start, this_month.end)}
    last_amounts = {row["name"]: row["amount"] for row in category_breakdown(ExpenseEntry, last_month.start, last_month.end)}

    def pct_change(curr, prior):
        if not prior:
            return None
        return (curr - prior) / prior * 100

    rows = []
    for name in sorted(set(this_amounts) | set(last_amounts)):
        curr = this_amounts.get(name, ZERO)
        prior = last_amounts.get(name, ZERO)
        rows.append({
            "name": name,
            "this_month": curr,
            "last_month": prior,
            "change_pct": pct_change(curr, prior),
        })
    rows.sort(key=lambda row: row["this_month"], reverse=True)

    sales_this = sales_total(this_month.start, this_month.end)
    sales_last = sales_total(last_month.start, last_month.end)
    sales_growth_pct = pct_change(sales_this, sales_last)

    insight = None
    spikes = [row for row in rows if row["change_pct"] is not None and row["change_pct"] > 20 and row["this_month"] > 0]
    if spikes:
        top = max(spikes, key=lambda row: row["change_pct"])
        if sales_growth_pct is None or top["change_pct"] > sales_growth_pct + 10:
            if sales_growth_pct is None:
                sales_clause = "sales data isn't available for last month"
            elif sales_growth_pct < 0:
                sales_clause = f"sales fell {abs(sales_growth_pct):.0f}%"
            else:
                sales_clause = f"sales increased only {sales_growth_pct:.0f}%"
            insight = f"{top['name']} expenses increased {top['change_pct']:.0f}%, while {sales_clause}."

    return {
        "rows": rows,
        "this_month_label": this_month.label,
        "last_month_label": last_month.label,
        "sales_growth_pct": sales_growth_pct,
        "insight": insight,
    }


def _vs_comparison(*, current_start, current_end, current_label, prev_start, prev_end, prev_label) -> dict:
    """Shared shape for a "this vs previous" sales comparison: totals, the
    % change, and each side's share of the larger of the two (0-100) so a
    template can size a two-bar visual without doing math itself."""
    current_sales = sales_total(current_start, current_end)
    prev_sales = sales_total(prev_start, prev_end)
    change_pct = float((current_sales - prev_sales) / prev_sales * 100) if prev_sales else None
    biggest = max(current_sales, prev_sales) or Decimal(1)
    return {
        "current_label": current_label,
        "current_sales": current_sales,
        "current_pct": float(current_sales / biggest * 100),
        "prev_label": prev_label,
        "prev_sales": prev_sales,
        "prev_pct": float(prev_sales / biggest * 100),
        "change_pct": change_pct,
    }


def sales_vs_previous_week() -> dict:
    """This calendar week so far (Mon-today) against the same span of
    weekdays last week — apples-to-apples day counts on both sides."""
    today = datetime.date.today()
    week_start = today - datetime.timedelta(days=today.weekday())
    prev_week_start = week_start - datetime.timedelta(days=7)
    prev_week_end = today - datetime.timedelta(days=7)
    return _vs_comparison(
        current_start=week_start, current_end=today,
        current_label=f"{week_start:%d %b} – {today:%d %b}",
        prev_start=prev_week_start, prev_end=prev_week_end,
        prev_label=f"{prev_week_start:%d %b} – {prev_week_end:%d %b}",
    )


def sales_vs_previous_month() -> dict:
    """This calendar month, month-to-date, against the same number of days
    into the previous month — apples-to-apples day counts on both sides
    (unlike comparing a partial current month to a full previous one)."""
    today = datetime.date.today()
    month_start = today.replace(day=1)
    days_elapsed = (today - month_start).days
    prev_month_end_full = month_start - datetime.timedelta(days=1)
    prev_month_start = prev_month_end_full.replace(day=1)
    prev_month_end = min(prev_month_start + datetime.timedelta(days=days_elapsed), prev_month_end_full)
    return _vs_comparison(
        current_start=month_start, current_end=today,
        current_label=f"{month_start:%d %b} – {today:%d %b}",
        prev_start=prev_month_start, prev_end=prev_month_end,
        prev_label=f"{prev_month_start:%d %b} – {prev_month_end:%d %b}",
    )


def sales_performance_trend(months: int = 6) -> list[dict]:
    """Last `months` calendar months of revenue, profit, order count,
    average order value and gross margin % — the fuller performance
    trend beneath a flat sales number. "Orders" = number of SalesEntry
    rows logged that month, since the app doesn't track a separate
    order concept."""
    end = datetime.date.today()
    start = (end.replace(day=1) - datetime.timedelta(days=1)).replace(day=1)
    for _ in range(months - 1):
        start = (start - datetime.timedelta(days=1)).replace(day=1)

    def monthly_sum(model):
        return {
            row["m"].strftime("%Y-%m"): row["total"]
            for row in model.objects.filter(date__gte=start, date__lte=end)
            .annotate(m=TruncMonth("date")).values("m").annotate(total=Sum("amount"))
        }

    sales_m = monthly_sum(SalesEntry)
    purchases_m = monthly_sum(PurchaseEntry)
    expenses_m = monthly_sum(ExpenseEntry)
    orders_m = {
        row["m"].strftime("%Y-%m"): row["n"]
        for row in SalesEntry.objects.filter(date__gte=start, date__lte=end)
        .annotate(m=TruncMonth("date")).values("m").annotate(n=Count("id"))
    }

    out = []
    cursor = start
    while cursor <= end:
        key = cursor.strftime("%Y-%m")
        sales = sales_m.get(key, ZERO)
        purchases = purchases_m.get(key, ZERO)
        expenses = expenses_m.get(key, ZERO)
        orders = orders_m.get(key, 0)
        out.append({
            "month": cursor.strftime("%b %Y"),
            "revenue": sales,
            "profit": sales - purchases - expenses,
            "orders": orders,
            "avg_order_value": (sales / orders) if orders else ZERO,
            "gross_margin_pct": ((sales - purchases) / sales * 100) if sales else ZERO,
        })
        cursor = (cursor.replace(day=28) + datetime.timedelta(days=4)).replace(day=1)
    return out


def sales_insights(fy_start_month: int, weekday_avgs: list[dict]) -> dict:
    """Channel-wise sales comparison, this calendar month vs last, plus a
    short list of auto-generated, plain-language observations — which
    channel has real momentum, how concentrated revenue is, and which
    weekday actually drives sales — instead of a flat breakdown chart."""
    this_month = resolve_period("this_month", fy_start_month)
    last_month = resolve_period("last_month", fy_start_month)

    this_amounts = {
        row["name"]: row["amount"]
        for row in category_breakdown(SalesEntry, this_month.start, this_month.end, field="channel")
    }
    last_amounts = {
        row["name"]: row["amount"]
        for row in category_breakdown(SalesEntry, last_month.start, last_month.end, field="channel")
    }

    def pct_change(curr, prior):
        if not prior:
            return None
        return (curr - prior) / prior * 100

    rows = []
    for name in sorted(set(this_amounts) | set(last_amounts)):
        curr = this_amounts.get(name, ZERO)
        prior = last_amounts.get(name, ZERO)
        rows.append({
            "name": name,
            "this_month": curr,
            "last_month": prior,
            "change_pct": pct_change(curr, prior),
        })
    rows.sort(key=lambda row: row["this_month"], reverse=True)

    notes = []
    movers = [row for row in rows if row["change_pct"] is not None and row["this_month"] > 0]
    if movers:
        gainer = max(movers, key=lambda row: row["change_pct"])
        if gainer["change_pct"] > 15:
            notes.append(f"📈 {gainer['name']} sales are up {gainer['change_pct']:.0f}% vs last month — your strongest-growing channel.")
        loser = min(movers, key=lambda row: row["change_pct"])
        if loser["change_pct"] < -15:
            notes.append(f"📉 {loser['name']} sales are down {abs(loser['change_pct']):.0f}% vs last month — worth a look.")

    total_this = sum((row["this_month"] for row in rows), ZERO)
    if rows and total_this:
        top = rows[0]
        share = top["this_month"] / total_this * 100
        if share >= 40:
            notes.append(f"⚖️ {top['name']} drives {share:.0f}% of this month's sales — revenue is concentrated in one channel.")

    if weekday_avgs:
        best = max(weekday_avgs, key=lambda row: row["average"])
        worst = min(weekday_avgs, key=lambda row: row["average"])
        if worst["average"] and best["day"] != worst["day"]:
            lift = (best["average"] - worst["average"]) / worst["average"] * 100
            notes.append(f"🗓️ {best['day']} is your best day to sell — averaging {lift:.0f}% more than your slowest day, {worst['day']}.")

    return {
        "rows": rows,
        "this_month_label": this_month.label,
        "last_month_label": last_month.label,
        "notes": notes,
    }


def upcoming_payables(days: int = 30):
    horizon = datetime.date.today() + datetime.timedelta(days=days)
    return payables_open().filter(due_date__lte=horizon)


def cash_position_summary() -> dict:
    return {
        "receivables": list(receivables_open()),
        "payables": list(payables_open()),
        "total_receivable": receivables_total_outstanding(),
        "total_payable": payables_total_outstanding(),
        "upcoming_obligations": list(upcoming_payables()),
    }


def cash_flow_statement(period: Period) -> dict:
    inflow = sales_total(period.start, period.end)
    outflow_purchases = purchases_total(period.start, period.end)
    outflow_expenses = expenses_total(period.start, period.end)
    partner_invested, partner_withdrawn = partner_flow(period.start, period.end)
    net_change = inflow - outflow_purchases - outflow_expenses + partner_invested - partner_withdrawn

    opening_date = period.start - datetime.timedelta(days=1)
    opening = total_balance_as_of(opening_date)
    closing = opening + net_change

    return {
        "period": period,
        "cash_in_from_sales": inflow,
        "cash_out_purchases": outflow_purchases,
        "cash_out_expenses": outflow_expenses,
        "partner_invested": partner_invested,
        "partner_withdrawn": partner_withdrawn,
        "net_change_in_cash": net_change,
        "opening_cash": opening,
        "closing_cash": closing,
    }
