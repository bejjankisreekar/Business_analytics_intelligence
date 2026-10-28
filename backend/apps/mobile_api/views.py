import datetime
import json

from collections import defaultdict
from decimal import Decimal

from django.db.models import Sum
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from apps.accounts.forms import LoginForm
from apps.billing import services as billing_services
from apps.finance import services
from apps.finance.forms import ExpenseEntryForm, PurchaseEntryForm, SalesEntryForm
from apps.finance.models import (
    BankAccount, Category, ExpenseEntry, PaymentMode, PurchaseEntry, SalesEntry, Vendor,
)
from apps.finance.periods import PERIOD_CHOICES, resolve_period

from .auth import api_login_required, billing_login_required, make_token

# kind -> (form, model, name of the category field on the model/form, Category.Kind)
ENTRY_TYPES = {
    "sale": (SalesEntryForm, SalesEntry, "channel", Category.Kind.SALES),
    "expense": (ExpenseEntryForm, ExpenseEntry, "category", Category.Kind.EXPENSE),
    "purchase": (PurchaseEntryForm, PurchaseEntry, "category", Category.Kind.PURCHASE),
}

# Fields the phone may send, per kind. Anything else is ignored, so the
# web-only links (admissions, ledgers, customers) can't be set from here.
ALLOWED_FIELDS = {
    "sale": ("date", "subcategory", "quantity", "gross_amount", "discount", "payment_mode", "bank_account", "note"),
    "expense": ("date", "subcategory", "amount", "payment_mode", "bank_account", "note"),
    "purchase": ("date", "subcategory", "vendor", "quantity", "amount", "payment_mode", "bank_account", "note"),
}


def _body(request) -> dict:
    try:
        data = json.loads(request.body or b"{}")
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _parse_date(value) -> datetime.date:
    try:
        return datetime.date.fromisoformat(value)
    except (TypeError, ValueError):
        return datetime.date.today()


def _min_date(user):
    return billing_services.historical_window_start(user.organization)


def _entry_json(entry, kind: str) -> dict:
    category = entry.channel if kind == "sale" else entry.category
    return {
        "id": str(entry.id),
        "type": kind,
        "date": entry.date.isoformat(),
        "category": category.name if category else None,
        "subcategory": entry.subcategory.name if entry.subcategory else None,
        "amount": str(entry.amount),
        "payment_mode": entry.payment_mode,
        "note": entry.note,
    }


def _identity_json(user) -> dict:
    from apps.quote_requests.access import user_has_access

    org = user.organization
    return {
        "user": {"email": user.email, "name": user.get_full_name(), "role": user.role},
        "organization": {"name": org.name if org else ""},
        # Optional add-ons this account may use: the app shows their tabs only when true.
        "features": {"customer_requests": user_has_access(user)},
    }


@csrf_exempt
@require_POST
def login(request):
    data = _body(request)
    form = LoginForm({"email": data.get("email", ""), "password": data.get("password", "")})
    if not form.is_valid():
        message = " ".join(e for errs in form.errors.values() for e in errs) or "Incorrect email or password."
        return JsonResponse({"error": message, "code": "invalid_credentials"}, status=401)
    user = form.cleaned_data["user"]
    return JsonResponse({"token": make_token(user), **_identity_json(user)})


@require_GET
@api_login_required
def me(request):
    """Who the token belongs to; the app calls this on launch to restore a session."""
    return JsonResponse(_identity_json(request.user))


@require_GET
@api_login_required
def entry_options(request):
    """Everything the entry screens need in one call, so the app can cache it."""
    subs = services.subcategory_map()
    categories = {}
    for kind, (_form, _model, _field, cat_kind) in ENTRY_TYPES.items():
        categories[kind] = [
            {
                "id": str(c.id),
                "name": c.name,
                "subcategories": subs.get(str(c.id), []),
            }
            for c in Category.objects.filter(kind=cat_kind, is_active=True).order_by("name")
        ]
    min_date = _min_date(request.user)
    return JsonResponse({
        "categories": categories,
        "payment_modes": [{"value": v, "label": str(label)} for v, label in PaymentMode.choices],
        "bank_accounts": [
            {"id": str(b.id), "name": b.name} for b in BankAccount.objects.filter(is_active=True).order_by("name")
        ],
        "vendors": list(Vendor.objects.filter(is_active=True).order_by("name").values_list("name", flat=True)),
        "min_date": min_date.isoformat() if min_date else None,
    })


@csrf_exempt
@api_login_required
def entries(request):
    if request.method == "GET":
        return _list_entries(request)
    if request.method == "POST":
        return _create_entry(request)
    return JsonResponse({"error": "Method not allowed.", "code": "method_not_allowed"}, status=405)


def _list_entries(request):
    day = _parse_date(request.GET.get("date"))
    out = []
    for kind, (_form, model, _field, _cat) in ENTRY_TYPES.items():
        qs = model.objects.filter(date=day).order_by("-created_at")
        out.extend(_entry_json(e, kind) for e in qs)
    return JsonResponse({"date": day.isoformat(), "entries": out})


def _create_entry(request):
    data = _body(request)
    kind = data.get("type")
    if kind not in ENTRY_TYPES:
        return JsonResponse({"error": "type must be sale, expense or purchase.", "code": "invalid_type"}, status=400)
    form_cls, _model, category_field, _cat = ENTRY_TYPES[kind]

    post = {key: ("" if data.get(key) is None else str(data[key])) for key in ALLOWED_FIELDS[kind] if key in data}
    post[category_field] = "" if data.get("category") is None else str(data["category"])
    post.setdefault("date", datetime.date.today().isoformat())
    post.setdefault("payment_mode", PaymentMode.CASH)

    form = form_cls(post, min_date=_min_date(request.user))
    if not form.is_valid():
        return JsonResponse(
            {
                "error": "Check the highlighted fields.",
                "code": "validation",
                "fields": {k: list(v) for k, v in form.errors.items()},
            },
            status=400,
        )
    entry = form.save(commit=False)
    entry.created_by_email = request.user.email
    entry.save()
    return JsonResponse({"entry": _entry_json(entry, kind)}, status=201)


@require_GET
@api_login_required
def dashboard(request):
    """Today, this month and the last 7 days at a glance, plus top categories this month."""
    today = datetime.date.today()
    month_start = today.replace(day=1)
    week_start = today - datetime.timedelta(days=6)
    since = min(month_start, week_start)

    zero = Decimal("0")
    totals = {"today": defaultdict(lambda: zero), "month": defaultdict(lambda: zero)}
    daily = defaultdict(lambda: defaultdict(lambda: zero))
    categories = {kind: defaultdict(lambda: zero) for kind in ENTRY_TYPES}

    for kind, (_form, model, _field, _cat) in ENTRY_TYPES.items():
        for entry in model.objects.filter(date__gte=since, date__lte=today):
            if entry.date >= month_start:
                totals["month"][kind] += entry.amount
                category = entry.channel if kind == "sale" else entry.category
                categories[kind][category.name if category else "Uncategorised"] += entry.amount
            if entry.date == today:
                totals["today"][kind] += entry.amount
            if entry.date >= week_start:
                daily[entry.date][kind] += entry.amount

    def summary(bucket):
        sale, expense, purchase = (str(bucket[k]) for k in ("sale", "expense", "purchase"))
        net = bucket["sale"] - bucket["expense"] - bucket["purchase"]
        return {"sale": sale, "expense": expense, "purchase": purchase, "net": str(net)}

    days = []
    for offset in range(7):
        day = week_start + datetime.timedelta(days=offset)
        days.append({"date": day.isoformat(), **summary(daily[day])})

    top = {
        kind: [
            {"name": name, "amount": str(amount)}
            for name, amount in sorted(bucket.items(), key=lambda kv: kv[1], reverse=True)[:5]
        ]
        for kind, bucket in categories.items()
    }
    fs = services.get_finance_settings()
    period = resolve_period("this_month", fs.fy_start_month)
    kpis = services.kpis_for_period(period)
    cash, bank = services.cash_and_bank_as_of(period.end)
    return JsonResponse({
        "today": summary(totals["today"]),
        "month": summary(totals["month"]),
        "last_7_days": days,
        "top_categories": top,
        "period_label": period.label,
        "kpis": _kpis_json(kpis),
        "cash_in_hand": str(cash),
        "bank_balance": str(bank),
        "daily": _series_json(services.daily_series(period.start, period.end)),
        "expense_breakdown": _breakdown_json(services.category_breakdown(ExpenseEntry, period.start, period.end)),
        "recent_entries": [
            {
                "type": e["type"],
                "date": e["date"].isoformat(),
                "label": e["label"],
                "amount": str(e["amount"]),
                "note": e["note"] or "",
            }
            for e in services.recent_entries(8)
        ],
    })


def _pct(value):
    return None if value is None else str(round(value, 1))


def _kpis_json(kpis: dict) -> dict:
    return {
        "sales": str(kpis["sales"]),
        "expenses": str(kpis["expenses"]),
        "purchases": str(kpis["purchases"]),
        "net_profit": str(kpis["net_profit"]),
        "margin_pct": _pct(kpis["margin_pct"]),
        "cash_balance": str(kpis["cash_balance"]),
        "sales_growth_pct": _pct(kpis["sales_growth_pct"]),
        "net_growth_pct": _pct(kpis["net_growth_pct"]),
    }


def _series_json(rows) -> list:
    return [
        {
            "date": r["date"].isoformat(),
            "sale": str(r["sales"]),
            "expense": str(r["expenses"]),
            "purchase": str(r["purchases"]),
            "net": str(r["net"]),
        }
        for r in rows
    ]


def _breakdown_json(rows) -> list:
    return [{"name": r["name"], "amount": str(r["amount"])} for r in rows]


def _breakdown_with_quantity(model, start, end, field: str = "category") -> list:
    """Category totals plus the units sales/purchases carry under each (expenses carry no quantity)."""
    qty = {
        (row[f"{field}__name"] or "Uncategorized"): row["qty"] or 0
        for row in model.objects.filter(date__gte=start, date__lte=end)
        .values(f"{field}__name").annotate(qty=Sum("quantity"))
    }
    return [
        {"name": r["name"], "amount": str(r["amount"]), "quantity": int(qty.get(r["name"], 0))}
        for r in services.category_breakdown(model, start, end, field=field)
    ]


@require_GET
@api_login_required
def daily_performance(request):
    """Day-by-day revenue, expense, purchase and net for a date range (default: this month so far)."""
    today = datetime.date.today()
    start = _parse_date(request.GET.get("from")) if request.GET.get("from") else today.replace(day=1)
    end = _parse_date(request.GET.get("to")) if request.GET.get("to") else today
    if end < start:
        start, end = end, start
    if (end - start).days > 92:
        start = end - datetime.timedelta(days=92)
    days = services.daily_series(start, end)
    totals = {
        key: str(sum((d[key] for d in days), Decimal("0")))
        for key in ("sales", "expenses", "purchases", "net")
    }
    return JsonResponse({
        "from": start.isoformat(),
        "to": end.isoformat(),
        "days": _series_json(reversed(days)),
        "totals": {"sale": totals["sales"], "expense": totals["expenses"],
                   "purchase": totals["purchases"], "net": totals["net"]},
    })


def _activity_groups(rows) -> list:
    """Quantity per sub-category, one group per category, biggest group first."""
    groups: dict = {}
    for r in rows:
        groups.setdefault(r["category"], []).append({"name": r["name"], "quantity": r["quantity"]})
    return sorted(
        ({"category": c, "total": sum(i["quantity"] for i in items), "items": items} for c, items in groups.items()),
        key=lambda g: -g["total"],
    )


def _api_period(request, default: str):
    """The period named by `?period=`; `custom` takes `?from=&to=` (ISO dates, either order)."""
    fs = services.get_finance_settings()
    key = request.GET.get("period", default)
    if key == "custom" and request.GET.get("from") and request.GET.get("to"):
        start, end = _parse_date(request.GET["from"]), _parse_date(request.GET["to"])
        if end < start:
            start, end = end, start
        return resolve_period("custom", fs.fy_start_month, custom_from=start, custom_to=end)
    if key not in {k for k, _ in PERIOD_CHOICES} or key == "custom":
        key = default
    return resolve_period(key, fs.fy_start_month)


@require_GET
@api_login_required
def intelligence(request):
    """Analytics, revenue and cost insights for a period."""
    period = _api_period(request, "this_month")
    zero = Decimal("0")

    payment = services.payment_mode_breakdown(period.start, period.end)
    return JsonResponse({
        "period": {"key": period.key, "label": period.label,
                   "from": period.start.isoformat(), "to": period.end.isoformat()},
        "period_choices": [{"key": k, "label": str(label)} for k, label in PERIOD_CHOICES if k != "custom"],
        "kpis": _kpis_json(services.kpis_for_period(period)),
        "monthly_trend": [
            {"label": r["month"], "sale": str(r["sales"]), "expense": str(r["expenses"]),
             "purchase": str(r["purchases"]), "net": str(r["net"]), **_month_bounds(r["month"])}
            for r in services.monthly_trend(6)
        ],
        "weekday_averages": [
            {"day": r["day"], "average": str(r["average"])}
            for r in services.weekday_averages(period.start, period.end)
        ],
        "payment_modes": _breakdown_json(payment),
        "revenue_by_channel": _breakdown_with_quantity(SalesEntry, period.start, period.end, field="channel"),
        "expense_by_category": _breakdown_json(
            services.category_breakdown(ExpenseEntry, period.start, period.end)),
        "purchase_by_category": _breakdown_with_quantity(PurchaseEntry, period.start, period.end),
        "activity_by_subcategory": _activity_groups(services.product_quantity_breakdown(period.start, period.end)),
        "gross_discount_net": {k: str(v or zero) for k, v in
                               services.gross_discount_net_totals(period.start, period.end).items()
                               if k in ("gross", "discount", "net")},
    })


def _month_bounds(label: str) -> dict:
    """First and last day (clipped to today) of a "Mon YYYY" label, so the app can drill into the month."""
    first = datetime.datetime.strptime(label, "%b %Y").date()
    last = (first.replace(day=28) + datetime.timedelta(days=4)).replace(day=1) - datetime.timedelta(days=1)
    return {"from": first.isoformat(), "to": min(last, datetime.date.today()).isoformat()}


# kind -> (model, category field), same tabs as the web Monthly Summary.
SUMMARY_KINDS = {
    "sale": (SalesEntry, "channel"),
    "purchase": (PurchaseEntry, "category"),
    "expense": (ExpenseEntry, "category"),
}
SUMMARY_GRANULARITIES = ("daily", "weekly", "monthly", "quarterly", "yearly")


def _cells_json(cells) -> list:
    return [{"value": str(c["value"]), "change": _pct(c["change"])} for c in cells]


@require_GET
@api_login_required
def monthly_summary(request):
    """Category-by-period summary (as on the web Monthly Summary) plus the last 12 months, newest first."""
    kind = request.GET.get("kind", "sale")
    if kind not in SUMMARY_KINDS:
        kind = "sale"
    granularity = request.GET.get("granularity", "monthly")
    if granularity not in SUMMARY_GRANULARITIES:
        granularity = "monthly"
    period = _api_period(request, "this_fy")
    model, field = SUMMARY_KINDS[kind]
    matrix = services.category_period_matrix(model, field, period.start, period.end, granularity)
    yoy = services.year_over_year_comparison(model, period.start, period.end)

    rows = services.monthly_trend(12)
    return JsonResponse({
        "months": [
            {"label": r["month"], "sale": str(r["sales"]), "expense": str(r["expenses"]),
             "purchase": str(r["purchases"]), "net": str(r["net"]), **_month_bounds(r["month"])}
            for r in reversed(rows)
        ],
        "kind": kind,
        "granularity": granularity,
        "period": {"key": period.key, "label": period.label,
                   "from": period.start.isoformat(), "to": period.end.isoformat()},
        "period_choices": [{"key": k, "label": str(label)} for k, label in PERIOD_CHOICES if k != "custom"],
        "labels": matrix["labels"],
        "truncated": matrix["truncated"],
        "grand_total": str(matrix["grand_total"]),
        "column_totals": _cells_json(matrix["column_cells"]),
        "yoy": {
            "current_total": str(yoy["current_total"]),
            "prior_total": str(yoy["prior_total"]),
            "prior_from": yoy["prior_start"].isoformat(),
            "prior_to": yoy["prior_end"].isoformat(),
            "change_pct": _pct(yoy["change_pct"]),
        },
        "rows": [
            {
                "name": r["name"],
                "total": str(r["total"]),
                "cells": _cells_json(r["cells"]),
                "subrows": [
                    {"name": sr["name"], "total": str(sr["total"]), "cells": _cells_json(sr["cells"])}
                    for sr in r["subrows"]
                ],
            }
            for r in matrix["rows"]
        ],
    })


@require_GET
@api_login_required
def cash_position(request):
    """Cash in hand, bank, and what is owed to / by the business."""
    fs = services.get_finance_settings()
    today = datetime.date.today()
    period = resolve_period("this_month", fs.fy_start_month)
    cash, bank = services.cash_and_bank_as_of(today)
    summary = services.cash_position_summary()
    flow = services.cash_flow_statement(period)
    return JsonResponse({
        "cash_in_hand": str(cash),
        "bank_balance": str(bank),
        "total": str(cash + bank),
        "total_receivable": str(summary["total_receivable"]),
        "total_payable": str(summary["total_payable"]),
        "net_position": str(summary["total_receivable"] - summary["total_payable"]),
        "period_label": period.label,
        "cash_flow": {k: str(flow[k]) for k in (
            "opening_cash", "cash_in_from_sales", "cash_out_purchases", "cash_out_expenses",
            "net_change_in_cash", "closing_cash")},
    })


def _period_from_request(request):
    return _api_period(request, "this_month")


@require_GET
@api_login_required
def financial_reports(request):
    """Profit & loss, balance sheet, cash flow and GST for a period (the web Reports page)."""
    period = _period_from_request(request)
    pnl = services.profit_and_loss(period)
    bs = services.balance_sheet(period.end)
    flow = services.cash_flow_statement(period, request.GET.get("flow", "all"))
    gst = services.gst_summary(period)

    def s(value):
        return str(value)

    def gst_rows(rows):
        return [{"rate": s(r["rate"]), "taxable_value": s(r["taxable_value"]), "tax": s(r["tax"])} for r in rows]

    return JsonResponse({
        "period": {"key": period.key, "label": period.label,
                   "from": period.start.isoformat(), "to": period.end.isoformat()},
        "period_choices": [{"key": k, "label": str(label)} for k, label in PERIOD_CHOICES if k != "custom"],
        "pnl": {
            "revenue": s(pnl["revenue"]), "cogs": s(pnl["cogs"]), "gross_profit": s(pnl["gross_profit"]),
            "expenses": _breakdown_json(pnl["opex_rows"]), "total_opex": s(pnl["total_opex"]),
            "net_profit": s(pnl["net_profit"]), "net_margin_pct": _pct(pnl["net_margin_pct"]),
            "prev_revenue": s(pnl["prev_revenue"]), "prev_net_profit": s(pnl["prev_net_profit"]),
        },
        "balance_sheet": {
            "as_of": bs["as_of"].isoformat(),
            **{k: s(bs[k]) for k in (
                "cash", "bank", "total_assets", "total_liabilities", "retained_earnings",
                "partner_capital", "total_equity", "opening_balance")},
            "balances": bs["balances"],
        },
        "cash_flow": {
            **{k: s(flow[k]) for k in (
                "opening_cash", "cash_in_from_sales", "cash_out_purchases", "cash_out_expenses",
                "partner_invested", "partner_withdrawn", "transfer_in", "transfer_out",
                "net_change_in_cash", "closing_cash")},
            "mode": flow["mode"], "mode_label": flow["mode_label"],
        },
        "gst": {
            "sales_rows": gst_rows(gst["sales_rows"]),
            "purchase_rows": gst_rows(gst["purchase_rows"]),
            **{k: s(gst[k]) for k in (
                "taxable_sales", "taxable_purchases", "untaxed_sales", "untaxed_purchases",
                "output_tax", "input_tax", "net_gst_payable")},
        },
    })


@require_GET
@api_login_required
def bank_accounts(request):
    """Every bank account with its current balance."""
    rows = services.bank_account_rows()
    return JsonResponse({
        "accounts": [
            {
                "id": str(r["account"].id),
                "name": r["account"].name,
                "bank": r["account"].other_bank_name or r["account"].get_bank_name_display(),
                "account_number": r["account"].account_number,
                "is_active": r["account"].is_active,
                "balance": str(r["balance"]),
            }
            for r in rows
        ],
        "total": str(sum((r["balance"] for r in rows if r["account"].is_active), Decimal("0"))),
    })


@require_GET
@api_login_required
def vendors(request):
    """Supplier directory with what is still owed to each."""
    owed = services.vendor_outstanding_map()
    return JsonResponse({
        "vendors": [
            {
                "id": str(v.id),
                "name": v.name,
                "phone": v.phone,
                "details": v.details,
                "is_active": v.is_active,
                "outstanding": str(owed.get(v.name, Decimal("0"))),
            }
            for v in Vendor.objects.all().order_by("name")
        ],
        "total_outstanding": str(services.payables_total_outstanding()),
    })


@require_GET
@api_login_required
def aging(request):
    """Receivables and payables bucketed by how overdue they are."""

    def report(data):
        return {
            "totals": {k: str(v) for k, v in data["totals"].items()},
            "rows": [{k: str(v) for k, v in row.items()} for row in data["rows"]],
        }

    return JsonResponse({
        "buckets": [{"key": key, "label": str(label)} for key, label in services.AGING_BUCKETS],
        "receivables": report(services.receivables_aging()),
        "payables": report(services.payables_aging()),
    })


def _line_json(e, kind: str) -> dict:
    category = e.channel if kind == "sale" else e.category
    sub = e.subcategory
    sub_name = (f"{sub.parent.name} › {sub.name}" if sub.parent_id else sub.name) if sub else ""
    customer = getattr(e, "customer", None)
    return {
        "id": str(e.id),
        "type": kind,
        "category": category.name if category else "Uncategorised",
        "subcategory": sub_name,
        "party": customer.name if customer else (getattr(e, "vendor", "") or ""),
        "quantity": getattr(e, "quantity", None),
        "amount": str(e.amount),
        "payment_mode": e.get_payment_mode_display(),
        "note": e.note,
    }


@require_GET
@api_login_required
def day_detail(request):
    """Everything recorded on one day, with opening and closing cash and bank."""
    day = _parse_date(request.GET.get("date"))
    report = services.daily_report(day)
    return JsonResponse({
        "date": day.isoformat(),
        "opening_cash": str(report["opening_cash"]),
        "opening_bank": str(report["opening_bank"]),
        "closing_cash": str(report["closing_cash"]),
        "closing_bank": str(report["closing_bank"]),
        "totals": {
            "sale": str(report["total_sales"]), "expense": str(report["total_expenses"]),
            "purchase": str(report["total_purchases"]), "net": str(report["net"]),
        },
        "entries": {
            "sale": [_line_json(e, "sale") for e in report["sales"]],
            "expense": [_line_json(e, "expense") for e in report["expenses"]],
            "purchase": [_line_json(e, "purchase") for e in report["purchases"]],
        },
    })


@require_GET
@api_login_required
def category_detail(request):
    """Line items for one category (by kind and name) over a date range."""
    kind = request.GET.get("kind")
    name = request.GET.get("name", "")
    if kind not in ENTRY_TYPES:
        return JsonResponse({"error": "kind must be sale, expense or purchase.", "code": "invalid_type"}, status=400)
    _form, model, field, cat_kind = ENTRY_TYPES[kind]
    today = datetime.date.today()
    start = _parse_date(request.GET.get("from")) if request.GET.get("from") else today.replace(day=1)
    end = _parse_date(request.GET.get("to")) if request.GET.get("to") else today
    category = Category.objects.filter(kind=cat_kind, name=name).first()
    if category is None:
        # The "Uncategorized" bucket: entries with no category set.
        qs = model.objects.filter(date__gte=start, date__lte=end, **{f"{field}__isnull": True})
        rows = [
            {"date": e.date.isoformat(), "subcategory": e.subcategory.name if e.subcategory else "",
             "party": "", "quantity": getattr(e, "quantity", None), "amount": str(e.amount),
             "payment_mode": e.get_payment_mode_display(), "note": e.note}
            for e in qs.select_related("subcategory").order_by("-date", "-created_at")
        ]
        total = sum((Decimal(r["amount"]) for r in rows), Decimal("0"))
        return JsonResponse({"name": name, "kind": kind, "from": start.isoformat(), "to": end.isoformat(),
                             "total": str(total), "count": len(rows),
                             "total_quantity": sum(r["quantity"] or 0 for r in rows), "entries": rows})
    data = services.category_statement_entries(
        model, field, category.id, request.GET.get("subcategory") or None, start, end)
    return JsonResponse({
        "name": category.name,
        "kind": kind,
        "from": start.isoformat(),
        "to": end.isoformat(),
        "total": str(data["totals"]["amount"]),
        "count": data["totals"]["count"],
        "total_quantity": data["totals"]["quantity"],
        "entries": [
            {
                "date": e["date"].isoformat(),
                "quantity": e["quantity"],
                "subcategory": " › ".join(p for p in (e["subcategory"], e["item"]) if p),
                "party": e["customer"] or e["vendor"],
                "amount": str(e["amount"]),
                "payment_mode": e["payment_mode"],
                "note": e["note"],
            }
            for e in reversed(data["entries"])
        ],
    })


@require_GET
@api_login_required
def ledger(request):
    """Running-balance ledger of a bank account or a vendor (newest first)."""
    kind = request.GET.get("kind")
    ident = request.GET.get("id", "")
    if kind == "bank":
        account = BankAccount.objects.filter(pk=ident).first()
        title, rows = (account.name, services.bank_account_ledger_entries(account)) if account else (None, [])
    elif kind == "vendor":
        vendor = Vendor.objects.filter(pk=ident).first()
        title, rows = (vendor.name, services.vendor_ledger_entries(vendor.name)) if vendor else (None, [])
    else:
        return JsonResponse({"error": "kind must be bank or vendor.", "code": "invalid_type"}, status=400)
    if title is None:
        return JsonResponse({"error": "Not found.", "code": "not_found"}, status=404)
    return JsonResponse({
        "title": title,
        "kind": kind,
        "balance": str(rows[-1]["balance"]) if rows else "0",
        "rows": [
            {"date": r["date"].isoformat(), "particular": r["particular"],
             "debit": str(r["debit"]), "credit": str(r["credit"]), "balance": str(r["balance"])}
            for r in reversed(rows)
        ],
    })


def _date(value):
    return value.isoformat() if value else None


@require_GET
@billing_login_required
def billing(request):
    """Plan, subscription status, invoices and payments, as on the web Billing page (read-only)."""
    from django.conf import settings
    from django.db.models import Sum

    from apps.billing.models import Invoice, Payment

    org = request.user.organization
    lapsed = not billing_services.has_active_access(org)
    if lapsed:
        billing_services.ensure_renewal_invoice(org)
    locked = bool(org.is_payment_hold) or lapsed

    invoices = Invoice.objects.filter(organization_id=org.id).order_by("-invoice_date", "-created_at")
    outstanding = (
        invoices.exclude(status__in=[Invoice.Status.PAID, Invoice.Status.CANCELLED, Invoice.Status.DRAFT])
        .aggregate(t=Sum("amount_due"))["t"] or Decimal("0")
    )
    sub = billing_services.get_current_subscription(org.id)
    subscription = None
    if sub is not None:
        plan = sub.plan
        subscription = {
            "plan": plan.name,
            "features": plan.feature_labels(),
            "user_limit": plan.user_limit,
            "business_limit": plan.business_limit,
            "status": sub.status,
            "status_label": sub.get_status_display(),
            "payment_status": sub.payment_status,
            "payment_status_label": sub.get_payment_status_display(),
            "billing_cycle": sub.billing_cycle,
            "billing_cycle_label": sub.get_billing_cycle_display(),
            "start_date": _date(sub.start_date),
            "end_date": _date(sub.end_date),
            "trial_end_date": _date(sub.trial_end_date),
            "price": str(sub.price),
            "discount": str(sub.discount),
            "tax": str(sub.tax),
            "final_amount": str(sub.final_amount),
            "auto_renewal": sub.auto_renewal,
            "autopay_enabled": sub.autopay_enabled,
            "is_complimentary": sub.is_complimentary,
        }

    return JsonResponse({
        "organization": org.name,
        "currency": org.currency,
        "locked": locked,
        "payment_hold": bool(org.is_payment_hold),
        "outstanding": str(outstanding),
        "subscription": subscription,
        "invoices": [
            {
                "id": i.id,
                "number": i.invoice_number,
                "invoice_date": _date(i.invoice_date),
                "due_date": _date(i.due_date),
                "period_start": _date(i.service_billing_start_date),
                "period_end": _date(i.service_billing_end_date),
                "subtotal": str(i.subtotal),
                "discount": str(i.discount),
                "tax": str(i.tax),
                "total": str(i.total),
                "amount_paid": str(i.amount_paid),
                "amount_due": str(i.amount_due),
                "status": i.status,
                "status_label": i.get_status_display(),
            }
            for i in invoices[:24]
        ],
        "payments": [
            {
                "id": p.payment_id,
                "amount": str(p.amount),
                "method": p.get_payment_method_display(),
                "gateway": p.get_gateway_display(),
                "date": p.payment_date.date().isoformat(),
                "status": p.status,
                "status_label": p.get_status_display(),
                "invoice": p.invoice.invoice_number if p.invoice_id else "",
            }
            for p in Payment.objects.filter(organization_id=org.id).select_related("invoice").order_by("-payment_date")[:15]
        ],
        "pay_url": settings.SITE_URL.rstrip("/") + "/app/billing/",
    })


@csrf_exempt
@require_POST
@billing_login_required
def billing_pay_order(request, pk):
    """Start an online payment for one of this organization's unpaid invoices: a Razorpay order
    plus what the app's checkout needs to open."""
    from django.conf import settings

    from apps.billing import razorpay_client
    from apps.billing.models import Invoice

    org = request.user.organization
    invoice = Invoice.objects.filter(pk=pk, organization_id=org.id).first()
    if invoice is None:
        return JsonResponse({"error": "Invoice not found.", "code": "not_found"}, status=404)
    if invoice.amount_due <= 0 or invoice.status in (Invoice.Status.PAID, Invoice.Status.CANCELLED):
        return JsonResponse({"error": "This invoice has nothing due.", "code": "nothing_due"}, status=400)
    if not razorpay_client.is_configured():
        return JsonResponse(
            {"error": "Online payment isn't set up yet. Contact support.", "code": "not_configured"}, status=503)

    order = razorpay_client.create_order(invoice)
    return JsonResponse({
        "order_id": order["id"],
        "amount": order["amount"],
        "currency": order["currency"],
        "key_id": settings.RAZORPAY_KEY_ID,
        "invoice_number": invoice.invoice_number,
        "organization_name": org.name,
        "prefill_email": request.user.email,
    })


@csrf_exempt
@require_POST
@billing_login_required
def billing_pay_verify(request, pk):
    """Confirm a checkout the app completed: the signature must match, then the payment is
    recorded against the invoice (idempotent, same as the web flow and the webhook)."""
    from apps.billing import payments as billing_payments, razorpay_client
    from apps.billing.models import Invoice, Payment

    org = request.user.organization
    invoice = Invoice.objects.filter(pk=pk, organization_id=org.id).first()
    if invoice is None:
        return JsonResponse({"error": "Invoice not found.", "code": "not_found"}, status=404)

    data = _body(request)
    order_id = str(data.get("razorpay_order_id", ""))
    payment_id = str(data.get("razorpay_payment_id", ""))
    signature = str(data.get("razorpay_signature", ""))
    if not razorpay_client.verify_payment_signature(order_id=order_id, payment_id=payment_id, signature=signature):
        return JsonResponse({"error": "Payment could not be verified.", "code": "bad_signature"}, status=400)

    billing_payments.record_payment(
        org,
        invoice=invoice,
        amount=invoice.amount_due,
        currency=invoice.currency,
        payment_method=Payment.Method.OTHER,
        gateway=Payment.Gateway.RAZORPAY,
        transaction_id=payment_id,
        status=Payment.Status.SUCCESS,
        raw_response={"order_id": order_id, "payment_id": payment_id, "source": "mobile"},
    )
    return JsonResponse({"ok": True})
