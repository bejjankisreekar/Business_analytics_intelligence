"""Platform analytics for the superadmin console: how the business itself is
doing in one environment — revenue collected, money given back (refunds and
discounts), subscriptions and their recurring revenue, client growth and mix.

Everything is read straight from the billing/organization tables of the
chosen environment's database (`using(env)`); nothing is written.

"Expenses" here means money the platform gave back — refunds and coupon
discounts, the only outflows the app records. A plan's own discount is NOT
one: the plan's gross price is only a list price and the net price is what
it really costs, so that discount is never revenue given up. Running costs
(servers, salaries, ...) aren't tracked anywhere, so they can't appear.

Recurring revenue (MRR/ARR) is counted at the net price — price minus
discount, before tax.
"""
import calendar
import datetime
from collections import defaultdict
from decimal import Decimal

from django.db.models import Count, F, Sum
from django.db.models.functions import TruncMonth
from django.utils import timezone

from apps.billing.models import CouponRedemption, Invoice, Payment, Subscription
from apps.organizations.models import Organization

ZERO = Decimal("0")
# A payment that was collected at some point, even if later refunded.
COLLECTED = [Payment.Status.SUCCESS, Payment.Status.PARTIALLY_REFUNDED, Payment.Status.REFUNDED]
# Invoices that actually count as billed (a draft or cancelled one never went out).
BILLED = [Invoice.Status.ISSUED, Invoice.Status.PAID, Invoice.Status.PARTIALLY_PAID, Invoice.Status.OVERDUE]
OPEN_INVOICE = [Invoice.Status.ISSUED, Invoice.Status.PARTIALLY_PAID, Invoice.Status.OVERDUE]
# Subscriptions that bring in recurring revenue right now.
PAYING = [Subscription.Status.ACTIVE, Subscription.Status.PAYMENT_DUE, Subscription.Status.PAST_DUE]


def month_keys(months: int, today: datetime.date) -> list[tuple[int, int]]:
    keys, y, m = [], today.year, today.month
    for _ in range(months):
        keys.append((y, m))
        m -= 1
        if m == 0:
            m, y = 12, y - 1
    return list(reversed(keys))


def _by_month(qs, date_field, value=None):
    """{(year, month): total or count} for a queryset, grouped on `date_field`."""
    agg = Sum(value) if value else Count("pk")
    out = {}
    for row in qs.annotate(m=TruncMonth(date_field)).values("m").annotate(v=agg):
        if row["m"] is not None:
            out[(row["m"].year, row["m"].month)] = row["v"] or 0
    return out


def _choice_counts(qs, field, choices, annotate_value=None):
    """[(label, count)] for a choices field, skipping zero rows."""
    labels = dict(choices)
    rows = qs.values(field).annotate(n=Count("pk")).order_by("-n")
    return [(str(labels.get(r[field], r[field])), r["n"]) for r in rows if r["n"]]


def monthly_equivalent(sub) -> Decimal:
    """What a subscription brings in per month, at its NET price (price minus
    discount, tax excluded). Custom-length ones can't be normalised, so they
    count as zero here (they still count as subscribers)."""
    amount = (sub.price or ZERO) - (sub.discount or ZERO)
    if sub.billing_cycle == Subscription.BillingCycle.MONTHLY:
        return amount
    if sub.billing_cycle == Subscription.BillingCycle.YEARLY:
        return amount / 12
    return ZERO


def platform_analytics(env: str, months: int = 12) -> dict:
    today = timezone.localdate()
    month_start = today.replace(day=1)
    last_month_end = month_start - datetime.timedelta(days=1)
    last_month_start = last_month_end.replace(day=1)
    keys = month_keys(months, today)
    first_day = datetime.date(keys[0][0], keys[0][1], 1)
    labels = [f"{calendar.month_abbr[m]} {y}" for y, m in keys]

    payments = Payment.objects.using(env)
    invoices = Invoice.objects.using(env)
    subs = Subscription.objects.using(env)
    orgs = Organization.objects.using(env)

    # ---- revenue / refunds / discounts per month ----------------------------
    collected_qs = payments.filter(status__in=COLLECTED)
    gross = _by_month(collected_qs.filter(payment_date__date__gte=first_day), "payment_date", "amount")
    refunds = _by_month(
        payments.filter(refunded_amount__gt=0, payment_date__date__gte=first_day), "payment_date", "refunded_amount"
    )
    redemptions = CouponRedemption.objects.using(env)
    discounts = _by_month(redemptions.filter(redeemed_at__date__gte=first_day), "redeemed_at", "discount_amount")
    new_clients = _by_month(orgs.filter(created_at__date__gte=first_day), "created_at")
    clients_before = orgs.filter(created_at__date__lt=first_day).count()

    series = {"gross": [], "refunds": [], "discounts": [], "net": [], "new_clients": [], "total_clients": []}
    running = clients_before
    for k in keys:
        g, r, d = gross.get(k, ZERO), refunds.get(k, ZERO), discounts.get(k, ZERO)
        series["gross"].append(float(g))
        series["refunds"].append(float(r))
        series["discounts"].append(float(d))
        series["net"].append(float(g - r))
        n = new_clients.get(k, 0)
        running += n
        series["new_clients"].append(n)
        series["total_clients"].append(running)

    def money(qs, field, **flt):
        return qs.filter(**flt).aggregate(t=Sum(field))["t"] or ZERO

    this_gross = money(collected_qs, "amount", payment_date__date__gte=month_start)
    this_refunds = money(payments, "refunded_amount", refunded_amount__gt=0, payment_date__date__gte=month_start)
    last_gross = money(
        collected_qs, "amount", payment_date__date__gte=last_month_start, payment_date__date__lt=month_start
    )
    last_refunds = money(
        payments, "refunded_amount", refunded_amount__gt=0,
        payment_date__date__gte=last_month_start, payment_date__date__lt=month_start,
    )
    this_discounts = money(redemptions, "discount_amount", redeemed_at__date__gte=month_start)
    this_net, last_net = this_gross - this_refunds, last_gross - last_refunds

    def change(now, before):
        if not before:
            return None
        return float((now - before) / before * 100)

    # ---- subscriptions & recurring revenue ---------------------------------
    current = list(subs.filter(is_current=True).select_related("plan", "organization"))
    paying = [s for s in current if s.status in PAYING and not s.is_complimentary]
    mrr = sum((monthly_equivalent(s) for s in paying), ZERO)

    plan_rows = defaultdict(lambda: {"subs": 0, "mrr": ZERO})
    for s in paying:
        plan_rows[s.plan.name]["subs"] += 1
        plan_rows[s.plan.name]["mrr"] += monthly_equivalent(s)
    plans = sorted(
        ({"name": n, "subs": v["subs"], "mrr": float(v["mrr"])} for n, v in plan_rows.items()),
        key=lambda r: -r["mrr"],
    )

    status_counts = defaultdict(int)
    for s in current:
        status_counts["Complimentary" if s.is_complimentary and s.status == Subscription.Status.ACTIVE else s.get_status_display()] += 1

    cancelled_this_month = subs.filter(cancellation_date__gte=month_start).count()
    trials = [s for s in current if s.status == Subscription.Status.TRIAL]
    soon = today + datetime.timedelta(days=14)
    trials_ending = sorted(
        (s for s in trials if s.trial_end_date and today <= s.trial_end_date <= soon), key=lambda s: s.trial_end_date
    )

    # ---- invoices ----------------------------------------------------------
    outstanding = invoices.filter(status__in=OPEN_INVOICE).aggregate(t=Sum("amount_due"))["t"] or ZERO
    overdue_qs = invoices.filter(status__in=OPEN_INVOICE, due_date__lt=today)
    overdue_total = overdue_qs.aggregate(t=Sum("amount_due"))["t"] or ZERO
    overdue_list = list(overdue_qs.select_related("organization").order_by("-amount_due")[:6])

    # ---- top clients by revenue --------------------------------------------
    top_clients = list(
        collected_qs.values(name=F("organization__name"), code=F("organization__organization_code"))
        .annotate(total=Sum("amount"), n=Count("pk")).order_by("-total")[:6]
    )

    return {
        "months": months,
        "labels": labels,
        "series": series,
        "kpis": {
            "gross_this": this_gross, "gross_change": change(this_gross, last_gross),
            "net_this": this_net, "net_change": change(this_net, last_net),
            "refunds_this": this_refunds, "discounts_this": this_discounts,
            "given_back_this": this_refunds + this_discounts,
            "total_revenue": money(collected_qs, "amount") - money(payments, "refunded_amount", refunded_amount__gt=0),
            "mrr": mrr, "arr": mrr * 12,
            "paying": len(paying), "trials": len(trials), "subscribers": len(current),
            "cancelled_this_month": cancelled_this_month,
            "clients": orgs.count(), "new_clients_this": new_clients.get((today.year, today.month), 0),
            "active_services": orgs.filter(service_status=Organization.ServiceStatus.ACTIVE).count(),
            "outstanding": outstanding, "overdue": overdue_total, "overdue_count": overdue_qs.count(),
            "avg_revenue_per_client": (mrr / len(paying)) if paying else ZERO,
        },
        "charts": {
            "labels": labels,
            **series,
            "plans": plans,
            "sub_status": sorted(status_counts.items(), key=lambda kv: -kv[1]),
            "pay_methods": _choice_counts(collected_qs, "payment_method", Payment.Method.choices),
            "pay_status": _choice_counts(payments, "status", Payment.Status.choices),
            "invoice_status": _choice_counts(invoices.exclude(status=Invoice.Status.DRAFT), "status", Invoice.Status.choices),
            "storage": _choice_counts(orgs, "storage_mode", Organization.StorageMode.choices),
            "business_type": _choice_counts(orgs, "business_type", Organization.BusinessType.choices),
            "org_size": _choice_counts(orgs, "size", Organization.OrganizationSize.choices),
        },
        "top_clients": top_clients,
        "trials_ending": trials_ending,
        "overdue_list": overdue_list,
        "today": today,
    }


def empty_platform_analytics(months: int = 12) -> dict:
    """The same shape as platform_analytics(), all zeros — shown when an
    environment's database can't be read, so the page keeps its layout."""
    today = timezone.localdate()
    labels = [f"{calendar.month_abbr[m]} {y}" for y, m in month_keys(months, today)]
    zeros = [0] * months
    return {
        "months": months,
        "labels": labels,
        "series": {},
        "kpis": {
            "gross_this": ZERO, "gross_change": None, "net_this": ZERO, "net_change": None,
            "refunds_this": ZERO, "discounts_this": ZERO, "given_back_this": ZERO, "total_revenue": ZERO,
            "mrr": ZERO, "arr": ZERO, "paying": 0, "trials": 0, "subscribers": 0, "cancelled_this_month": 0,
            "clients": 0, "new_clients_this": 0, "active_services": 0,
            "outstanding": ZERO, "overdue": ZERO, "overdue_count": 0, "avg_revenue_per_client": ZERO,
        },
        "charts": {
            "labels": labels, "gross": zeros, "refunds": zeros, "discounts": zeros, "net": zeros,
            "new_clients": zeros, "total_clients": zeros, "plans": [], "sub_status": [], "pay_methods": [],
            "pay_status": [], "invoice_status": [], "storage": [], "business_type": [], "org_size": [],
        },
        "top_clients": [], "trials_ending": [], "overdue_list": [], "today": today,
    }
