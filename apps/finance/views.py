import csv
import datetime
import json
from decimal import Decimal

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import logout
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Prefetch, Q, Sum
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.generic import TemplateView, View

from apps.billing import payments as billing_payments
from apps.billing import razorpay_client
from apps.billing import services as billing_services
from apps.billing.models import Invoice, Payment

from . import services
from .forms import (
    CashTransferForm,
    CategoryEditForm,
    CategoryForm,
    CustomerForm,
    ExpenseEntryForm,
    ExpenseEntryFormSet,
    FinanceSettingsForm,
    PartnerForm,
    PartnerTransactionForm,
    PayableEditForm,
    PayableForm,
    PurchaseEntryForm,
    PurchaseEntryFormSet,
    ReceivableEditForm,
    ReceivableForm,
    RecordPaymentForm,
    SalesEntryForm,
    SalesEntryFormSet,
    SubcategoryEditForm,
    SubcategoryForm,
    VendorEditForm,
    VendorForm,
)
from .models import (
    CashTransfer,
    Category,
    Customer,
    ExpenseEntry,
    Partner,
    PartnerTransaction,
    Payable,
    PaymentMode,
    PurchaseEntry,
    Receivable,
    SalesEntry,
    Subcategory,
    Vendor,
)
from .periods import PERIOD_CHOICES, resolve_period


def _decimal_default(obj):
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, datetime.date):
        return obj.isoformat()
    raise TypeError


def to_json(data) -> str:
    """JSON for embedding inside a <script> block via `|safe`. Names in the
    data are user-typed (category, sub-category, vendor), so anything that
    could close the script element or open an HTML comment is escaped —
    all still valid JSON, and JS reads them back as the original text."""
    return (
        json.dumps(data, default=_decimal_default)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def _pad_daily_series(series: list[dict], min_days: int) -> list[dict]:
    """Extend a short `daily_series` result with trailing empty days so the
    daily bar chart always has at least `min_days` slots — otherwise a
    15-day period renders fatter bars than a 30-day one. Padded days keep
    their (continuing) date so the x-axis stays labeled, but carry `None`
    values so no bar is drawn for them."""
    padded = list(series)
    if not padded:
        return padded
    next_day = padded[-1]["date"]
    while len(padded) < min_days:
        next_day = next_day + datetime.timedelta(days=1)
        padded.append({"date": next_day, "sales": None, "expenses": None, "purchases": None, "net": None})
    return padded


def _product_quantity_groups(product_quantity: list[dict]) -> list[dict]:
    """`product_quantity_breakdown()`'s flat rows, split into one group per
    sales channel (e.g. New Mobiles, Accessories) so the Analytics page can
    render a dedicated small chart per category instead of one combined
    chart mixing every category together."""
    groups: dict[str, list[dict]] = {}
    for row in product_quantity:
        groups.setdefault(row["category"], []).append(row)

    result = []
    for category, rows in groups.items():
        if len(rows) <= 1:
            continue
        total = sum(r["quantity"] for r in rows)
        result.append({
            "category": category,
            "total": total,
            "chart_height": max(240, len(rows) * 22),
            "labels_json": to_json([r["name"] for r in rows]),
            "values_json": to_json([r["quantity"] for r in rows]),
        })
    result.sort(key=lambda g: -g["total"])
    return result


def _historical_min_date(request):
    """The earliest date `request.user`'s org may create/backdate a
    transaction to, per their plan — passed as `min_date=` into every
    SalesEntryForm/ExpenseEntryForm/PurchaseEntryForm/CashTransferForm
    (and bulk formset) construction so HistoricalWindowFormMixin.clean_date
    can enforce it server-side."""
    return billing_services.historical_window_start(request.user.organization)


class TenantLoginRequiredMixin(LoginRequiredMixin):
    """Every tenant-facing view requires this. Two independent gates:

    1. Organization.is_service_active — a superadmin's manual stop/suspend
       switch. Tripped, it logs the user out entirely (existing behavior).
    2. billing_services.has_active_access() — the prepaid gate: has the
       org's trial or paid period actually lapsed? Tripped, the user stays
       logged in but every view except the ones that opt in via
       `allow_when_locked = True` (Billing itself, and its two payment
       endpoints) bounces to the Billing page instead — self-service:
       they can see the (auto-generated) invoice and pay it, and a real
       successful payment is the only thing that lifts this.
    """

    login_url = "accounts:login"
    allow_when_locked = False

    def dispatch(self, request, *args, **kwargs):
        user = request.user
        if user.is_authenticated and user.organization is not None:
            org = user.organization
            if not org.is_service_active:
                logout(request)
                messages.error(request, "This organization's access has been suspended. Contact support.")
                return redirect("accounts:login")
            if not self.allow_when_locked and not billing_services.has_active_access(org):
                billing_services.ensure_renewal_invoice(org)
                return redirect("finance:billing")
        return super().dispatch(request, *args, **kwargs)


class PeriodMixin:
    """Reads ?period=&from=&to= from the query string and resolves it."""

    def get_period(self, fy_start_month: int):
        key = self.request.GET.get("period", "this_month")
        custom_from = self.request.GET.get("from")
        custom_to = self.request.GET.get("to")
        custom_from = datetime.date.fromisoformat(custom_from) if custom_from else None
        custom_to = datetime.date.fromisoformat(custom_to) if custom_to else None
        return resolve_period(key, fy_start_month, custom_from=custom_from, custom_to=custom_to)


class DashboardView(TenantLoginRequiredMixin, TemplateView):
    """A deliberately light home screen: this month's numbers (with growth
    vs the prior period), a couple of headline charts, quick-add, and
    what just happened. The full, period-driven chart set lives on the
    Analytics Intelligence page instead."""

    template_name = "finance/dashboard.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        fs = services.get_finance_settings()
        today = datetime.date.today()
        period = resolve_period("this_month", fs.fy_start_month)

        kpis = services.kpis_for_period(period)
        cash, bank = services.cash_and_bank_as_of(period.end)
        series = services.daily_series(period.start, period.end)
        expense_breakdown = services.category_breakdown(ExpenseEntry, period.start, period.end)

        context.update({
            "active_nav": "dashboard",
            "organization": self.request.user.organization,
            "today": today,
            "period": period,
            "kpis": kpis,
            "cash_in_hand": cash,
            "bank_balance": bank,
            "recent_entries": services.recent_entries(8),
            "transfer_form": CashTransferForm(auto_id="id_transfer_%s", min_date=_historical_min_date(self.request)),
            "chart_daily_labels": to_json([r["date"].strftime("%d %b") for r in series]),
            "chart_daily_sales": to_json([r["sales"] for r in series]),
            "chart_daily_expenses": to_json([r["expenses"] for r in series]),
            "chart_daily_purchases": to_json([r["purchases"] for r in series]),
            "chart_expense_labels": to_json([r["name"] for r in expense_breakdown]),
            "chart_expense_values": to_json([r["amount"] for r in expense_breakdown]),
            "expense_breakdown": expense_breakdown,
        })
        return context


class BillingView(TenantLoginRequiredMixin, TemplateView):
    """The org's own view of its subscription, invoices, and payment
    history — surfaces any pending/overdue amount so they know to pay.
    apps.billing models live in the same (public-schema) database as
    Organization/User, not the tenant schema, so this queries the default
    connection directly, no schema_context needed.

    allow_when_locked=True: this is the one page a lapsed (unpaid) org can
    still reach — otherwise they could never see or pay the invoice that
    would lift the lock."""

    template_name = "finance/billing.html"
    allow_when_locked = True

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        org = self.request.user.organization
        locked = not billing_services.has_active_access(org)
        if locked:
            # Covers landing here directly (bookmark, fresh login) rather
            # than being bounced from another page — same idempotent call
            # either way, so the invoice always exists once lapsed.
            billing_services.ensure_renewal_invoice(org)
        invoices = (
            Invoice.objects.filter(organization_id=org.id)
            .select_related("coupon_redemption__coupon")
            .order_by("-invoice_date", "-created_at")
        )
        outstanding = (
            invoices.exclude(status__in=[Invoice.Status.PAID, Invoice.Status.CANCELLED, Invoice.Status.DRAFT])
            .aggregate(t=Sum("amount_due"))["t"]
            or 0
        )
        context.update(
            {
                "active_nav": "billing",
                "organization": org,
                "subscription": billing_services.get_current_subscription(org.id),
                "invoices": invoices,
                "payments": Payment.objects.filter(organization_id=org.id).order_by("-payment_date")[:15],
                "outstanding": outstanding,
                "razorpay_configured": razorpay_client.is_configured(),
                "locked": locked,
            }
        )
        return context


class CreateInvoicePaymentOrderView(TenantLoginRequiredMixin, View):
    """Creates a Razorpay Order for one of this org's own outstanding
    invoices and hands back just enough for the Checkout popup to open.
    apps.billing models live in the shared public schema, same as
    BillingView above — queried directly, no schema_context needed."""

    allow_when_locked = True

    def post(self, request, pk, *args, **kwargs):
        org = request.user.organization
        invoice = get_object_or_404(Invoice, pk=pk, organization_id=org.id)
        if invoice.amount_due <= 0:
            return JsonResponse({"error": "This invoice has nothing due."}, status=400)
        if not razorpay_client.is_configured():
            return JsonResponse({"error": "Online payment isn't set up yet — contact support."}, status=503)

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


class VerifyInvoicePaymentView(TenantLoginRequiredMixin, View):
    """The Checkout popup's success handler posts back here with Razorpay's
    three fields; once the signature checks out, it's recorded as a real
    Payment against this invoice right away. The webhook (once
    RAZORPAY_WEBHOOK_SECRET is configured) independently confirms the same
    payment — safely a no-op the second time, since record_payment is
    idempotent on (gateway, transaction_id)."""

    allow_when_locked = True

    def post(self, request, pk, *args, **kwargs):
        org = request.user.organization
        invoice = get_object_or_404(Invoice, pk=pk, organization_id=org.id)

        order_id = request.POST.get("razorpay_order_id", "")
        payment_id = request.POST.get("razorpay_payment_id", "")
        signature = request.POST.get("razorpay_signature", "")

        if not razorpay_client.verify_payment_signature(
            order_id=order_id, payment_id=payment_id, signature=signature
        ):
            return JsonResponse({"error": "Payment could not be verified."}, status=400)

        billing_payments.record_payment(
            org,
            invoice=invoice,
            amount=invoice.amount_due,
            currency=invoice.currency,
            payment_method=Payment.Method.OTHER,
            gateway=Payment.Gateway.RAZORPAY,
            transaction_id=payment_id,
            status=Payment.Status.SUCCESS,
            raw_response={"order_id": order_id, "payment_id": payment_id},
        )
        messages.success(request, "Payment received — thank you!")
        return JsonResponse({"ok": True})


class ApplyCouponView(TenantLoginRequiredMixin, View):
    """Redeems a coupon code against one of this org's own outstanding
    invoices — folds the discount into the invoice's `discount` field, so
    the amount CreateInvoicePaymentOrderView/Razorpay checkout reads is
    already reduced by the time the customer clicks Pay Now. Works even
    while locked out (allow_when_locked) — the whole point is letting a
    lapsed org bring their reactivation invoice down before paying it."""

    allow_when_locked = True

    def post(self, request, pk, *args, **kwargs):
        org = request.user.organization
        invoice = get_object_or_404(Invoice, pk=pk, organization_id=org.id)
        code = request.POST.get("code", "")
        try:
            redemption = billing_services.redeem_coupon(organization=org, invoice=invoice, code=code)
        except billing_services.CouponError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(
                request,
                f"Coupon {redemption.coupon.code} applied — {org.currency} {redemption.discount_amount} off "
                f"invoice {invoice.invoice_number}.",
            )
        return redirect("finance:billing")


class CreateAutopaySubscriptionView(TenantLoginRequiredMixin, View):
    """Creates a Razorpay Subscription for the org's current plan and hands
    back what the Checkout popup (subscription mode) needs to open — the
    customer still has to authorize the mandate there before autopay is
    actually on (see VerifyAutopaySetupView)."""

    allow_when_locked = True

    def post(self, request, *args, **kwargs):
        try:
            data = billing_services.enable_autopay(request.user.organization)
        except billing_services.AutopayError as exc:
            return JsonResponse({"error": str(exc)}, status=400)
        return JsonResponse(data)


class VerifyAutopaySetupView(TenantLoginRequiredMixin, View):
    """The Checkout popup's success handler (subscription mode) posts back
    here with Razorpay's three fields; once verified, autopay is flipped
    on — Razorpay will auto-charge this mandate every billing cycle from
    here on, reported back to us via webhook (subscription.charged)."""

    allow_when_locked = True

    def post(self, request, *args, **kwargs):
        org = request.user.organization
        try:
            billing_services.confirm_autopay(
                organization=org,
                razorpay_subscription_id=request.POST.get("razorpay_subscription_id", ""),
                payment_id=request.POST.get("razorpay_payment_id", ""),
                signature=request.POST.get("razorpay_signature", ""),
            )
        except billing_services.AutopayError as exc:
            return JsonResponse({"error": str(exc)}, status=400)
        messages.success(request, "Autopay is on — your subscription will renew automatically every billing cycle.")
        return JsonResponse({"ok": True})


class CancelAutopayView(TenantLoginRequiredMixin, View):
    """The org owner turning autopay back off, from the Billing page."""

    allow_when_locked = True

    def post(self, request, *args, **kwargs):
        billing_services.cancel_autopay(request.user.organization)
        messages.success(request, "Autopay turned off — your subscription will no longer renew automatically.")
        return redirect("finance:billing")


class AnalyticsView(TenantLoginRequiredMixin, PeriodMixin, TemplateView):
    """Every chart and period-driven number lives here — pick a period at
    the top and the whole page (KPIs + every chart) updates to match it."""

    template_name = "finance/analytics.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        fs = services.get_finance_settings()
        period = self.get_period(fs.fy_start_month)

        series = services.daily_series(period.start, period.end)
        today = datetime.date.today()
        weekly_count = max(18, services.weeks_since(fs.opening_date, today))
        monthly_count = max(18, services.months_since(fs.opening_date, today))
        weekly = services.weekly_trend(weekly_count)
        trend = services.monthly_trend(6)
        trend_wide = services.monthly_trend(monthly_count)
        weekday = services.weekday_averages(period.start, period.end)
        expense_breakdown = services.category_breakdown(ExpenseEntry, period.start, period.end)
        purchase_breakdown = services.category_breakdown(PurchaseEntry, period.start, period.end)
        channel_breakdown = services.category_breakdown(SalesEntry, period.start, period.end, field="channel")
        payment_breakdown = services.payment_mode_breakdown(period.start, period.end)
        product_quantity = services.product_quantity_breakdown(period.start, period.end)

        subcategory_categories = services.subcategory_revenue_categories(period.start, period.end)
        category_ids = {str(c.id) for c in subcategory_categories}
        selected_subcategory_category = self.request.GET.get("subcat_category")
        if selected_subcategory_category not in category_ids:
            selected_subcategory_category = next(iter(category_ids), None)
        product_revenue = services.subcategory_revenue_breakdown(
            period.start, period.end, category_id=selected_subcategory_category
        )

        cash_running = []
        running = services.total_balance_as_of(period.start - datetime.timedelta(days=1))
        for row in series:
            running = running + row["net"]
            cash_running.append({"date": row["date"], "balance": running})

        padded_daily = _pad_daily_series(series, 30)

        context.update({
            "active_nav": "analytics",
            "organization": self.request.user.organization,
            "period": period,
            "period_choices": PERIOD_CHOICES,
            "chart_daily_labels": to_json([r["date"].strftime("%d %b") for r in padded_daily]),
            "chart_daily_sales": to_json([r["sales"] for r in padded_daily]),
            "chart_daily_expenses": to_json([r["expenses"] for r in padded_daily]),
            "chart_daily_purchases": to_json([r["purchases"] for r in padded_daily]),
            "chart_daily_net": to_json([r["net"] for r in padded_daily]),
            "chart_weekly_labels": to_json([r["label"] for r in weekly]),
            "chart_weekly_sales": to_json([r["sales"] for r in weekly]),
            "chart_weekly_expenses": to_json([r["expenses"] for r in weekly]),
            "chart_weekly_purchases": to_json([r["purchases"] for r in weekly]),
            "chart_monthly_labels": to_json([r["month"] for r in trend_wide]),
            "chart_monthly_sales": to_json([r["sales"] for r in trend_wide]),
            "chart_monthly_expenses": to_json([r["expenses"] for r in trend_wide]),
            "chart_monthly_purchases": to_json([r["purchases"] for r in trend_wide]),
            "chart_cash_labels": to_json([r["date"].strftime("%d %b") for r in cash_running]),
            "chart_cash_balance": to_json([r["balance"] for r in cash_running]),
            "chart_trend_labels": to_json([r["month"] for r in trend]),
            "chart_trend_sales": to_json([r["sales"] for r in trend]),
            "chart_trend_expenses": to_json([r["expenses"] for r in trend]),
            "chart_trend_purchases": to_json([r["purchases"] for r in trend]),
            "chart_trend_net": to_json([r["net"] for r in trend]),
            "chart_weekday_labels": to_json([r["day"] for r in weekday]),
            "chart_weekday_avg": to_json([r["average"] for r in weekday]),
            "chart_expense_labels": to_json([r["name"] for r in expense_breakdown]),
            "chart_expense_values": to_json([r["amount"] for r in expense_breakdown]),
            "chart_purchase_labels": to_json([r["name"] for r in purchase_breakdown]),
            "chart_purchase_values": to_json([r["amount"] for r in purchase_breakdown]),
            "chart_channel_labels": to_json([r["name"] for r in channel_breakdown]),
            "chart_channel_values": to_json([r["amount"] for r in channel_breakdown]),
            "chart_payment_labels": to_json([r["name"] for r in payment_breakdown]),
            "chart_payment_values": to_json([r["amount"] for r in payment_breakdown]),
            "expense_breakdown": expense_breakdown,
            "purchase_breakdown": purchase_breakdown,
            "channel_breakdown": channel_breakdown,
            "product_revenue": product_revenue,
            "chart_product_revenue_labels": to_json([r["name"] for r in product_revenue]),
            "chart_product_revenue_values": to_json([r["amount"] for r in product_revenue]),
            "subcategory_categories": subcategory_categories,
            "selected_subcategory_category": selected_subcategory_category,
            "product_quantity": product_quantity,
            "product_quantity_groups": _product_quantity_groups(product_quantity),
        })
        return context


class SalesIntelligenceView(TenantLoginRequiredMixin, PeriodMixin, TemplateView):
    """Every sales-side insight in one place: trend, channel breakdown,
    best days to sell, revenue by product, how sales are paid, and
    auto-generated sales insights (channel momentum vs last month)."""

    template_name = "finance/sales_intelligence.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        fs = services.get_finance_settings()
        period = self.get_period(fs.fy_start_month)

        kpis = services.kpis_for_period(period)
        weekday = services.weekday_averages(period.start, period.end)
        channel_breakdown = services.category_breakdown(SalesEntry, period.start, period.end, field="channel")
        product_revenue = services.category_breakdown(SalesEntry, period.start, period.end, field="subcategory")[:10]
        payment_breakdown = services.payment_mode_breakdown(period.start, period.end, models=(SalesEntry,))
        sales_insights = services.sales_insights(fs.fy_start_month, weekday)
        vs_prev_week = services.sales_vs_previous_week()
        vs_prev_month = services.sales_vs_previous_month()
        perf_trend = services.sales_performance_trend(6)

        subcategory_groups = services.categories_with_subcategories()
        group_ids = {str(g.id) for g in subcategory_groups}
        selected_group_id = self.request.GET.get("category")
        if selected_group_id not in group_ids:
            selected_group_id = str(subcategory_groups[0].id) if subcategory_groups else None
        drilldown = services.subcategory_children_trend(selected_group_id, period) if selected_group_id else None
        breakdown_tree = services.category_breakdown_tree(selected_group_id, period) if selected_group_id else []
        selected_group = next((g for g in subcategory_groups if str(g.id) == selected_group_id), None)

        context.update({
            "active_nav": "sales_intelligence",
            "organization": self.request.user.organization,
            "period": period,
            "period_choices": PERIOD_CHOICES,
            "kpis": kpis,
            "chart_weekday_labels": to_json([r["day"] for r in weekday]),
            "chart_weekday_avg": to_json([r["average"] for r in weekday]),
            "chart_channel_labels": to_json([r["name"] for r in channel_breakdown]),
            "chart_channel_values": to_json([r["amount"] for r in channel_breakdown]),
            "chart_product_revenue_labels": to_json([r["name"] for r in product_revenue]),
            "chart_product_revenue_values": to_json([r["amount"] for r in product_revenue]),
            "chart_payment_labels": to_json([r["name"] for r in payment_breakdown]),
            "chart_payment_values": to_json([r["amount"] for r in payment_breakdown]),
            "chart_perf_labels": to_json([r["month"] for r in perf_trend]),
            "chart_perf_revenue": to_json([r["revenue"] for r in perf_trend]),
            "chart_perf_profit": to_json([r["profit"] for r in perf_trend]),
            "chart_perf_orders": to_json([r["orders"] for r in perf_trend]),
            "chart_perf_aov": to_json([r["avg_order_value"] for r in perf_trend]),
            "chart_perf_margin": to_json([r["gross_margin_pct"] for r in perf_trend]),
            "product_revenue": product_revenue,
            "sales_insights": sales_insights,
            "chart_insights_labels": to_json([r["name"] for r in sales_insights["rows"]]),
            "chart_insights_this_month": to_json([r["this_month"] for r in sales_insights["rows"]]),
            "chart_insights_last_month": to_json([r["last_month"] for r in sales_insights["rows"]]),
            "vs_prev_week": vs_prev_week,
            "vs_prev_month": vs_prev_month,
            "subcategory_groups": subcategory_groups,
            "selected_group_id": selected_group_id,
            "selected_group": selected_group,
            "chart_breakdown_tree": to_json(breakdown_tree),
            "chart_drilldown_daily": to_json(drilldown["daily"] if drilldown else {"labels": [], "series": []}),
            "chart_drilldown_weekly": to_json(drilldown["weekly"] if drilldown else {"labels": [], "series": []}),
            "chart_drilldown_monthly": to_json(drilldown["monthly"] if drilldown else {"labels": [], "series": []}),
        })
        return context


class PurchaseExpenseIntelligenceView(TenantLoginRequiredMixin, PeriodMixin, TemplateView):
    """Every purchase- and expense-side insight in one place: trend,
    category/vendor breakdowns, cost by product category, and how outflow
    is paid."""

    template_name = "finance/purchase_expense_intelligence.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        fs = services.get_finance_settings()
        period = self.get_period(fs.fy_start_month)

        kpis = services.kpis_for_period(period)
        series = services.daily_series(period.start, period.end)
        weekly = services.weekly_trend(12)
        trend = services.monthly_trend(6)
        # The cost trend (daily / weekly / monthly) mirrors the revenue trend on
        # Analytics — same windows, same daily padding — so the two read alike.
        today = datetime.date.today()
        trend_weekly = services.weekly_trend(max(18, services.weeks_since(fs.opening_date, today)))
        trend_monthly = services.monthly_trend(max(18, services.months_since(fs.opening_date, today)))
        trend_daily = _pad_daily_series(series, 30)
        purchase_breakdown = services.category_breakdown(PurchaseEntry, period.start, period.end)
        expense_breakdown = services.category_breakdown(ExpenseEntry, period.start, period.end)
        vendor_breakdown = services.vendor_breakdown(period.start, period.end)
        payment_breakdown = services.payment_mode_breakdown(
            period.start, period.end, models=(ExpenseEntry, PurchaseEntry)
        )
        expense_analysis = services.expense_month_comparison(fs.fy_start_month)
        expense_asc = sorted(expense_analysis["rows"], key=lambda r: r["this_month"])
        cost_trees = {
            "expense": services.cost_tree(ExpenseEntry, period.start, period.end),
            "purchase": services.cost_tree(PurchaseEntry, period.start, period.end),
        }

        context.update({
            "cost_trees_json": to_json(cost_trees),
            "cost_tree_has_data": {k: bool(v) for k, v in cost_trees.items()},
            "active_nav": "purchase_expense_intelligence",
            "organization": self.request.user.organization,
            "period": period,
            "period_choices": PERIOD_CHOICES,
            "kpis": kpis,
            "total_outflow": kpis["expenses"] + kpis["purchases"],
            "chart_daily_labels": to_json([r["date"].strftime("%d %b") for r in series]),
            "chart_daily_expenses": to_json([r["expenses"] for r in series]),
            "chart_daily_purchases": to_json([r["purchases"] for r in series]),
            "cost_trend_json": to_json({
                "daily": {
                    "labels": [r["date"].strftime("%d %b") for r in trend_daily],
                    "expenses": [r["expenses"] for r in trend_daily],
                    "purchases": [r["purchases"] for r in trend_daily],
                },
                "weekly": {
                    "labels": [r["label"] for r in trend_weekly],
                    "expenses": [r["expenses"] for r in trend_weekly],
                    "purchases": [r["purchases"] for r in trend_weekly],
                },
                "monthly": {
                    "labels": [r["month"] for r in trend_monthly],
                    "expenses": [r["expenses"] for r in trend_monthly],
                    "purchases": [r["purchases"] for r in trend_monthly],
                },
            }),
            "chart_weekly_labels": to_json([r["label"] for r in weekly]),
            "chart_weekly_expenses": to_json([r["expenses"] for r in weekly]),
            "chart_weekly_purchases": to_json([r["purchases"] for r in weekly]),
            "chart_monthly_labels": to_json([r["month"] for r in trend]),
            "chart_monthly_expenses": to_json([r["expenses"] for r in trend]),
            "chart_monthly_purchases": to_json([r["purchases"] for r in trend]),
            "chart_purchase_labels": to_json([r["name"] for r in purchase_breakdown]),
            "chart_purchase_values": to_json([r["amount"] for r in purchase_breakdown]),
            "chart_expense_labels": to_json([r["name"] for r in expense_breakdown]),
            "chart_expense_values": to_json([r["amount"] for r in expense_breakdown]),
            "chart_vendor_labels": to_json([r["name"] for r in vendor_breakdown[:10]]),
            "chart_vendor_values": to_json([r["amount"] for r in vendor_breakdown[:10]]),
            "chart_payment_labels": to_json([r["name"] for r in payment_breakdown]),
            "chart_payment_values": to_json([r["amount"] for r in payment_breakdown]),
            "purchase_breakdown": purchase_breakdown,
            "expense_breakdown": expense_breakdown,
            "vendor_breakdown": vendor_breakdown[:10],
            "expense_analysis": expense_analysis,
            "expense_asc": expense_asc,
            "chart_expense_asc_labels": to_json([r["name"] for r in expense_asc]),
            "chart_expense_asc_values": to_json([r["this_month"] for r in expense_asc]),
        })
        return context


class DailyReportView(TenantLoginRequiredMixin, TemplateView):
    template_name = "finance/daily_report.html"

    def _selected_date(self):
        raw = self.request.GET.get("date")
        if raw:
            try:
                return datetime.date.fromisoformat(raw)
            except ValueError:
                pass
        return datetime.date.today()

    def _selected_mode(self):
        mode = self.request.GET.get("mode", "all")
        return mode if mode in ("cash", "bank", "all") else "all"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        selected_date = self._selected_date()
        selected_mode = self._selected_mode()
        payment_mode_filter = {"cash": PaymentMode.CASH, "bank": PaymentMode.BANK}.get(selected_mode)
        report = services.daily_report(selected_date, payment_mode=payment_mode_filter)
        min_date = _historical_min_date(self.request)
        for transfer in report["transfers"]:
            transfer.edit_form = CashTransferForm(
                instance=transfer, auto_id=f"id_edit_transfer_{transfer.pk}_%s", min_date=min_date
            )

        context.update({
            "active_nav": "daily_report",
            "organization": self.request.user.organization,
            "report": report,
            "selected_mode": selected_mode,
            "today": datetime.date.today(),
            "prev_date": selected_date - datetime.timedelta(days=1),
            "next_date": selected_date + datetime.timedelta(days=1),
            "transfer_form": CashTransferForm(
                initial={"date": selected_date}, auto_id="id_transfer_%s", min_date=_historical_min_date(self.request)
            ),
        })
        return context


def _parse_date(raw, default=None):
    if raw:
        try:
            return datetime.date.fromisoformat(raw)
        except ValueError:
            pass
    return default or datetime.date.today()


def _bulk_entry_context(request, selected_date, *, active_bulk_tab="sale", sale_formset=None,
                         expense_formset=None, purchase_formset=None):
    """Context for the bulk-entry screen. Pass a bound (invalid) formset for
    the type that just failed validation so its errors and already-typed
    rows survive the re-render instead of being lost on redirect; the other
    two types fall back to fresh blank formsets."""
    report = services.daily_report(selected_date)
    min_date = _historical_min_date(request)
    return {
        "active_nav": "daily_report",
        "organization": request.user.organization,
        "selected_date": selected_date,
        "today": datetime.date.today(),
        "prev_date": selected_date - datetime.timedelta(days=1),
        "next_date": selected_date + datetime.timedelta(days=1),
        "report": report,
        "subcategory_map_json": to_json(services.subcategory_map()),
        "active_bulk_tab": active_bulk_tab,
        "min_date": min_date,
        "sale_formset": sale_formset or SalesEntryFormSet(
            queryset=SalesEntry.objects.filter(date=selected_date).order_by("created_at"), prefix="sale",
            initial=[{"date": selected_date}] * SalesEntryFormSet.extra,
            form_kwargs={"min_date": min_date},
        ),
        "expense_formset": expense_formset or ExpenseEntryFormSet(
            queryset=ExpenseEntry.objects.filter(date=selected_date).order_by("created_at"), prefix="expense",
            initial=[{"date": selected_date}] * ExpenseEntryFormSet.extra,
            form_kwargs={"min_date": min_date},
        ),
        "purchase_formset": purchase_formset or PurchaseEntryFormSet(
            queryset=PurchaseEntry.objects.filter(date=selected_date).order_by("created_at"), prefix="purchase",
            initial=[{"date": selected_date}] * PurchaseEntryFormSet.extra,
            form_kwargs={"min_date": min_date},
        ),
    }


class DailyBulkEntryView(TenantLoginRequiredMixin, TemplateView):
    """Spreadsheet-style bulk add for a day's sales/expenses/purchases —
    its own screen so the day-ending workflow isn't crowded onto the
    read-only Daily Report summary."""

    template_name = "finance/daily_bulk_entry.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        selected_date = _parse_date(self.request.GET.get("date"))
        tab = self.request.GET.get("tab")
        active_tab = tab if tab in ("sale", "expense", "purchase") else "sale"
        context.update(_bulk_entry_context(self.request, selected_date, active_bulk_tab=active_tab))
        return context


class DailyReportPdfView(TenantLoginRequiredMixin, View):
    def get(self, request, *args, **kwargs):
        raw = request.GET.get("date")
        try:
            selected_date = datetime.date.fromisoformat(raw) if raw else datetime.date.today()
        except ValueError:
            selected_date = datetime.date.today()

        report = services.daily_report(selected_date)
        context = {"organization": request.user.organization, "report": report}
        return _render_statement_pdf(
            request, "finance/pdf/daily_report_pdf.html", context, f"daily-report-{selected_date}.pdf"
        )


def _bound_bulk_formset(FormSetClass, model, request, prefix, selected_date):
    """Reconstruct a bound formset for POST validation with each row's
    `date` initial matching what was actually rendered (the day being
    entered). Without this, an untouched row's `has_changed()` check falls
    back to the date field's own hardcoded "today" initial — which only
    happens to match when entering today; for any other date it makes
    Django think every blank row was edited, breaking the empty-row skip
    and surfacing "required" errors on rows the user never touched."""
    total = int(request.POST.get(f"{prefix}-TOTAL_FORMS") or FormSetClass.extra)
    return FormSetClass(
        request.POST, queryset=model.objects.filter(date=selected_date).order_by("created_at"), prefix=prefix,
        initial=[{"date": selected_date}] * total,
        form_kwargs={"min_date": _historical_min_date(request)},
    )


def _save_bulk_formset(formset, request) -> tuple[int, list]:
    """Save/update every changed row in a validated formset and delete any
    row checked for removal. New rows get created_by_email tagged; editing
    an existing row (the formset's queryset now includes the day's already-
    logged entries, not just blank ones) leaves its original creator
    untouched, matching EditEntryView. Fully-blank extra rows are already
    excluded by Django's empty_permitted handling before this is called.
    Returns the count of rows saved (created or updated) and the saved
    model instances, so the caller can show a detailed recap."""
    count = 0
    saved = []
    for form in formset:
        if not form.cleaned_data:
            continue
        if form.cleaned_data.get("DELETE"):
            if form.instance.pk:
                form.instance.delete()
            continue
        entry = form.save(commit=False)
        if entry._state.adding:
            entry.created_by_email = request.user.email
        entry.save()
        saved.append(entry)
        count += 1
    return count, saved


def _save_purchase_formset(formset, request) -> tuple[int, int, list]:
    """Like _save_bulk_formset, but a row marked "on credit" becomes a
    Payable (see _create_payable_from_purchase) instead of an immediate
    PurchaseEntry. Returns (purchases saved, on-credit rows saved, the
    PurchaseEntry instances) — the on-credit count is separate since those
    rows have no PurchaseEntry to show in the "just saved" recap.

    "On credit" only applies to a genuinely new row: for an existing
    PurchaseEntry (now editable here too), checking it is a no-op rather
    than spinning up a second, disconnected Payable for money that's
    already been logged as paid."""
    count = 0
    credit_count = 0
    saved = []
    for form in formset:
        if not form.cleaned_data:
            continue
        if form.cleaned_data.get("DELETE"):
            if form.instance.pk:
                form.instance.delete()
            continue
        is_new = form.instance._state.adding
        if is_new and form.cleaned_data.get("on_credit"):
            _create_payable_from_purchase(form.cleaned_data, request)
            credit_count += 1
            continue
        entry = form.save(commit=False)
        if is_new:
            entry.created_by_email = request.user.email
        entry.save()
        saved.append(entry)
        count += 1
    return count, credit_count, saved


def _create_payable_from_purchase(cleaned_data: dict, request) -> Payable:
    """A Daily Entries purchase logged as "on credit" becomes a Payable
    instead of an immediate PurchaseEntry — the PurchaseEntry only gets
    created later, when the bill is actually paid off (mirrors
    RecordPayablePaymentView), so cash/bank/P&L never double-count a
    purchase that hasn't been paid for yet. The category/subcategory/qty
    detail PurchaseEntry would normally carry has no home on Payable, so it
    gets folded into the note instead of silently dropped."""
    detail_bits = [b for b in [
        cleaned_data["category"].name if cleaned_data.get("category") else None,
        cleaned_data["subcategory"].name if cleaned_data.get("subcategory") else None,
        f"Qty {cleaned_data['quantity']}" if cleaned_data.get("quantity") else None,
    ] if b]
    detail = " · ".join(detail_bits)
    note = cleaned_data.get("note") or ""
    if detail:
        note = f"{detail} — {note}" if note else detail
    return Payable.objects.create(
        vendor=cleaned_data["vendor"],
        bill_date=cleaned_data["date"],
        due_date=cleaned_data["date"] + datetime.timedelta(days=30),
        amount=cleaned_data["amount"],
        note=note,
        created_by_email=request.user.email,
    )


def _describe_saved(entries, kind: str) -> list[dict]:
    """Turn just-saved SalesEntry/ExpenseEntry/PurchaseEntry rows into the
    detailed recap shown on the bulk-entry page: category/channel label,
    date, amount, payment mode and note per row."""
    rows = []
    for e in entries:
        if kind == "sale":
            label = e.channel.name if e.channel else "Revenue"
        else:
            label = e.category.name if e.category else kind.capitalize()
        if e.subcategory:
            label = f"{label} · {e.subcategory.name}"
        rows.append({
            "date": e.date,
            "label": label,
            "amount": e.amount,
            "payment_mode": e.get_payment_mode_display(),
            "note": e.note,
        })
    return rows


class BulkAddSalesView(TenantLoginRequiredMixin, View):
    def post(self, request, *args, **kwargs):
        selected_date = _parse_date(request.POST.get("selected_date"))
        formset = _bound_bulk_formset(SalesEntryFormSet, SalesEntry, request, "sale", selected_date)
        if formset.is_valid():
            count, saved = _save_bulk_formset(formset, request)
            if count:
                messages.success(request, f"Logged {count} sale{'s' if count != 1 else ''}.")
            context = _bulk_entry_context(request, selected_date, active_bulk_tab="sale")
            context["just_saved_type"] = "sale"
            context["just_saved_entries"] = _describe_saved(saved, "sale")
            return render(request, "finance/daily_bulk_entry.html", context)
        messages.error(request, "Couldn't save one or more sale rows — the errors are highlighted below.")
        context = _bulk_entry_context(request, selected_date, active_bulk_tab="sale", sale_formset=formset)
        return render(request, "finance/daily_bulk_entry.html", context)


class BulkAddExpensesView(TenantLoginRequiredMixin, View):
    def post(self, request, *args, **kwargs):
        selected_date = _parse_date(request.POST.get("selected_date"))
        formset = _bound_bulk_formset(ExpenseEntryFormSet, ExpenseEntry, request, "expense", selected_date)
        if formset.is_valid():
            count, saved = _save_bulk_formset(formset, request)
            if count:
                messages.success(request, f"Logged {count} expense{'s' if count != 1 else ''}.")
            context = _bulk_entry_context(request, selected_date, active_bulk_tab="expense")
            context["just_saved_type"] = "expense"
            context["just_saved_entries"] = _describe_saved(saved, "expense")
            return render(request, "finance/daily_bulk_entry.html", context)
        messages.error(request, "Couldn't save one or more expense rows — the errors are highlighted below.")
        context = _bulk_entry_context(request, selected_date, active_bulk_tab="expense", expense_formset=formset)
        return render(request, "finance/daily_bulk_entry.html", context)


class BulkAddPurchasesView(TenantLoginRequiredMixin, View):
    def post(self, request, *args, **kwargs):
        selected_date = _parse_date(request.POST.get("selected_date"))
        formset = _bound_bulk_formset(PurchaseEntryFormSet, PurchaseEntry, request, "purchase", selected_date)
        if formset.is_valid():
            count, credit_count, saved = _save_purchase_formset(formset, request)
            if count or credit_count:
                parts = []
                if count:
                    parts.append(f"{count} purchase{'s' if count != 1 else ''}")
                if credit_count:
                    parts.append(f"{credit_count} on credit (added to vendor payables)")
                messages.success(request, "Logged " + " and ".join(parts) + ".")
            context = _bulk_entry_context(request, selected_date, active_bulk_tab="purchase")
            context["just_saved_type"] = "purchase"
            context["just_saved_entries"] = _describe_saved(saved, "purchase")
            return render(request, "finance/daily_bulk_entry.html", context)
        messages.error(request, "Couldn't save one or more purchase rows — the errors are highlighted below.")
        context = _bulk_entry_context(
            request, selected_date, active_bulk_tab="purchase", purchase_formset=formset
        )
        return render(request, "finance/daily_bulk_entry.html", context)


class EditTransferView(TenantLoginRequiredMixin, View):
    def post(self, request, pk, *args, **kwargs):
        transfer = get_object_or_404(CashTransfer, pk=pk)
        form = CashTransferForm(request.POST, instance=transfer, min_date=_historical_min_date(request))
        if form.is_valid():
            form.save()
            messages.success(request, f"Updated transfer of {transfer.amount} on {transfer.date:%d %b %Y}.")
        else:
            messages.error(request, "Couldn't save that transfer: " + "; ".join(
                f"{f}: {', '.join(e)}" for f, e in form.errors.items()
            ))
        return redirect(request.POST.get("next") or "finance:daily_report")


class DeleteTransferView(TenantLoginRequiredMixin, View):
    def post(self, request, pk, *args, **kwargs):
        transfer = get_object_or_404(CashTransfer, pk=pk)
        transfer.delete()
        messages.success(request, "Transfer deleted.")
        return redirect(request.POST.get("next") or "finance:daily_report")


class AddTransferView(TenantLoginRequiredMixin, View):
    def post(self, request, *args, **kwargs):
        form = CashTransferForm(request.POST, min_date=_historical_min_date(request))
        if form.is_valid():
            entry = form.save(commit=False)
            entry.created_by_email = request.user.email
            entry.save()
            messages.success(
                request, f"Logged {entry.get_direction_display().lower()} of {entry.amount} on {entry.date:%d %b %Y}."
            )
        else:
            messages.error(request, "Couldn't save that transfer: " + "; ".join(
                f"{f}: {', '.join(e)}" for f, e in form.errors.items()
            ))
        return redirect(request.POST.get("next") or "finance:dashboard")


class ReportsView(TenantLoginRequiredMixin, PeriodMixin, TemplateView):
    template_name = "finance/reports.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        fs = services.get_finance_settings()
        period = self.get_period(fs.fy_start_month)

        context.update({
            "active_nav": "reports",
            "organization": self.request.user.organization,
            "period": period,
            "period_choices": PERIOD_CHOICES,
            "pnl": services.profit_and_loss(period),
            "balance_sheet": services.balance_sheet(period.end),
            "cash_flow": services.cash_flow_statement(period),
            "gst": services.gst_summary(period),
        })
        return context


def _render_statement_pdf(request, template_name, context, filename):
    try:
        from xhtml2pdf import pisa
    except ImportError:
        messages.error(request, "PDF export isn't available on this server.")
        return redirect("finance:reports")

    from io import BytesIO

    from django.template.loader import render_to_string

    html = render_to_string(template_name, context)
    buffer = BytesIO()
    pisa.CreatePDF(src=html, dest=buffer)
    response = HttpResponse(buffer.getvalue(), content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


class StatementPdfView(TenantLoginRequiredMixin, PeriodMixin, View):
    statement = None

    def get(self, request, *args, **kwargs):
        fs = services.get_finance_settings()
        period = self.get_period(fs.fy_start_month)
        org = request.user.organization

        if self.statement == "pnl":
            context = {"organization": org, "pnl": services.profit_and_loss(period)}
            template = "finance/pdf/pnl_pdf.html"
            filename = f"profit-and-loss-{period.start}-{period.end}.pdf"
        elif self.statement == "balance_sheet":
            context = {"organization": org, "balance_sheet": services.balance_sheet(period.end)}
            template = "finance/pdf/balance_sheet_pdf.html"
            filename = f"balance-sheet-{period.end}.pdf"
        elif self.statement == "gst":
            context = {"organization": org, "gst": services.gst_summary(period)}
            template = "finance/pdf/gst_pdf.html"
            filename = f"gst-summary-{period.start}-{period.end}.pdf"
        else:
            context = {"organization": org, "cash_flow": services.cash_flow_statement(period)}
            template = "finance/pdf/cash_flow_pdf.html"
            filename = f"cash-flow-{period.start}-{period.end}.pdf"

        return _render_statement_pdf(request, template, context, filename)


MONTH_NAMES = {
    1: "January", 2: "February", 3: "March", 4: "April", 5: "May", 6: "June",
    7: "July", 8: "August", 9: "September", 10: "October", 11: "November", 12: "December",
}


class FinanceSettingsView(TenantLoginRequiredMixin, TemplateView):
    template_name = "finance/settings.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        fs = services.get_finance_settings()
        categories = list(Category.objects.prefetch_related("subcategories").all())
        context["active_nav"] = "settings"
        context["organization"] = self.request.user.organization
        context["fs"] = fs
        context["fy_start_month_name"] = MONTH_NAMES.get(fs.fy_start_month, "")
        context["form"] = kwargs.get("form") or FinanceSettingsForm(instance=fs)
        context["sales_categories"] = [c for c in categories if c.kind == Category.Kind.SALES]
        context["expense_categories"] = [c for c in categories if c.kind == Category.Kind.EXPENSE]
        context["purchase_categories"] = [c for c in categories if c.kind == Category.Kind.PURCHASE]
        context["product_categories"] = [c for c in categories if c.kind == Category.Kind.PRODUCT]
        context["customers"] = Customer.objects.all()
        context["customer_form"] = CustomerForm()
        context["vendors"] = Vendor.objects.all()
        return context

    def post(self, request, *args, **kwargs):
        fs = services.get_finance_settings()
        form = FinanceSettingsForm(request.POST, instance=fs)
        if form.is_valid():
            form.save()
            messages.success(request, "Finance settings updated.")
            return redirect("finance:settings")
        return self.render_to_response(self.get_context_data(form=form))


CATEGORY_TABS = [
    (Category.Kind.SALES, "Revenue Categories", "e.g. In-store, Online", "e.g. a brand"),
    (Category.Kind.EXPENSE, "Expense Categories", "e.g. Rent, Marketing", "e.g. an employee name"),
    (Category.Kind.PURCHASE, "Purchase Categories", "e.g. Inventory / Stock", "e.g. a vendor"),
    (Category.Kind.PRODUCT, "Product Categories", "e.g. Medicines, Electronics", "e.g. a sub-line"),
]
CATEGORY_KIND_KEYS = {k for k, *_ in CATEGORY_TABS}


def _category_kind(request) -> str:
    kind = request.GET.get("kind") or request.POST.get("kind") or Category.Kind.SALES
    return kind if kind in CATEGORY_KIND_KEYS else Category.Kind.SALES


class CategoriesView(TenantLoginRequiredMixin, TemplateView):
    """Full category directory for one kind at a time (sales/expense/
    purchase/product) — add, rename, deactivate, delete, plus each
    category's sub-categories."""

    template_name = "finance/categories.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        kind = _category_kind(self.request)
        categories = Category.objects.filter(kind=kind).prefetch_related(
            Prefetch(
                "subcategories",
                queryset=Subcategory.objects.filter(parent__isnull=True).prefetch_related("children"),
            )
        )
        tab = next(t for t in CATEGORY_TABS if t[0] == kind)
        context.update({
            "active_nav": "categories",
            "organization": self.request.user.organization,
            "kind": kind,
            "tabs": CATEGORY_TABS,
            "tab_label": tab[1],
            "name_placeholder": tab[2],
            "sub_placeholder": tab[3],
            "categories": categories,
            "category_form": CategoryForm(initial={"kind": kind}, auto_id="id_category_%s"),
            "subcategory_form": SubcategoryForm(auto_id="id_subcategory_%s"),
        })
        return context


class AddCategoryView(TenantLoginRequiredMixin, View):
    def post(self, request, *args, **kwargs):
        form = CategoryForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "Category added.")
        else:
            messages.error(request, "Couldn't add that category — it may already exist.")
        return redirect(request.POST.get("next") or "finance:categories")


class EditCategoryView(TenantLoginRequiredMixin, TemplateView):
    template_name = "finance/edit_category.html"

    def get(self, request, pk, *args, **kwargs):
        category = get_object_or_404(Category, pk=pk)
        form = CategoryEditForm(instance=category)
        return self.render(request, category, form)

    def post(self, request, pk, *args, **kwargs):
        category = get_object_or_404(Category, pk=pk)
        form = CategoryEditForm(request.POST, instance=category)
        if form.is_valid():
            form.save()
            messages.success(request, "Category updated.")
            next_url = request.POST.get("next") or f"{reverse('finance:categories')}?kind={category.kind}"
            return redirect(next_url)
        return self.render(request, category, form)

    def render(self, request, category, form):
        next_url = request.GET.get("next") or request.POST.get("next") or (
            f"{reverse('finance:categories')}?kind={category.kind}"
        )
        context = {
            "active_nav": "categories",
            "organization": request.user.organization,
            "category": category,
            "form": form,
            "next": next_url,
        }
        return self.render_to_response(context)


class DeleteCategoryView(TenantLoginRequiredMixin, View):
    def post(self, request, pk, *args, **kwargs):
        category = get_object_or_404(Category, pk=pk)
        kind = category.kind
        category.delete()
        messages.success(request, "Category deleted.")
        return redirect(request.POST.get("next") or f"{reverse('finance:categories')}?kind={kind}")


class ToggleCategoryView(TenantLoginRequiredMixin, View):
    def post(self, request, pk, *args, **kwargs):
        category = get_object_or_404(Category, pk=pk)
        category.is_active = not category.is_active
        category.save(update_fields=["is_active"])
        return redirect(request.POST.get("next") or "finance:categories")


class AddSubcategoryView(TenantLoginRequiredMixin, View):
    def post(self, request, *args, **kwargs):
        form = SubcategoryForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "Sub-category added.")
        else:
            messages.error(request, "Couldn't add that sub-category — it may already exist.")
        return redirect(request.POST.get("next") or "finance:categories")


class EditSubcategoryView(TenantLoginRequiredMixin, TemplateView):
    template_name = "finance/edit_subcategory.html"

    def get(self, request, pk, *args, **kwargs):
        subcategory = get_object_or_404(Subcategory, pk=pk)
        form = SubcategoryEditForm(instance=subcategory)
        return self.render(request, subcategory, form)

    def post(self, request, pk, *args, **kwargs):
        subcategory = get_object_or_404(Subcategory, pk=pk)
        form = SubcategoryEditForm(request.POST, instance=subcategory)
        if form.is_valid():
            form.save()
            messages.success(request, "Sub-category updated.")
            next_url = request.POST.get("next") or f"{reverse('finance:categories')}?kind={subcategory.category.kind}"
            return redirect(next_url)
        return self.render(request, subcategory, form)

    def render(self, request, subcategory, form):
        next_url = request.GET.get("next") or request.POST.get("next") or (
            f"{reverse('finance:categories')}?kind={subcategory.category.kind}"
        )
        context = {
            "active_nav": "categories",
            "organization": request.user.organization,
            "subcategory": subcategory,
            "form": form,
            "next": next_url,
        }
        return self.render_to_response(context)


class DeleteSubcategoryView(TenantLoginRequiredMixin, View):
    def post(self, request, pk, *args, **kwargs):
        subcategory = get_object_or_404(Subcategory, pk=pk)
        subcategory.delete()
        messages.success(request, "Sub-category deleted.")
        return redirect(request.POST.get("next") or "finance:categories")


class ToggleSubcategoryView(TenantLoginRequiredMixin, View):
    def post(self, request, pk, *args, **kwargs):
        subcategory = get_object_or_404(Subcategory, pk=pk)
        subcategory.is_active = not subcategory.is_active
        subcategory.save(update_fields=["is_active"])
        return redirect(request.POST.get("next") or "finance:categories")


class AddCustomerView(TenantLoginRequiredMixin, View):
    def post(self, request, *args, **kwargs):
        form = CustomerForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "Customer added.")
        else:
            messages.error(request, "Couldn't add that customer — the name may already exist.")
        return redirect("finance:settings")


class ToggleCustomerView(TenantLoginRequiredMixin, View):
    def post(self, request, pk, *args, **kwargs):
        customer = get_object_or_404(Customer, pk=pk)
        customer.is_active = not customer.is_active
        customer.save(update_fields=["is_active"])
        return redirect("finance:settings")


class VendorsView(TenantLoginRequiredMixin, TemplateView):
    """Full vendor directory: contact details, opening balance, and current
    outstanding (from open payables) — with edit/delete/deactivate."""

    template_name = "finance/vendors.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        outstanding = services.vendor_outstanding_map()
        vendors = list(Vendor.objects.all())
        for vendor in vendors:
            vendor.outstanding = outstanding.get(vendor.name, services.ZERO)
        context.update({
            "active_nav": "vendors",
            "organization": self.request.user.organization,
            "vendors": vendors,
            "vendor_form": VendorForm(auto_id="id_vendor_%s"),
        })
        return context


class LedgersView(TenantLoginRequiredMixin, TemplateView):
    """Every ledger in the business in one place: Cash and Bank (current
    balance, linking into their full transaction history), plus a
    directory of customers and vendors with their current outstanding
    balance, linking into each one's full debit/credit/running-balance
    statement."""

    template_name = "finance/ledgers.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        today = datetime.date.today()
        cash, bank = services.cash_and_bank_as_of(today)

        customer_outstanding = services.customer_outstanding_map()
        customers = list(Customer.objects.all())
        for customer in customers:
            customer.outstanding = customer_outstanding.get(customer.id, services.ZERO)

        vendor_outstanding = services.vendor_outstanding_map()
        vendors = list(Vendor.objects.all())
        for vendor in vendors:
            vendor.outstanding = vendor_outstanding.get(vendor.name, services.ZERO)

        context.update({
            "active_nav": "ledgers",
            "organization": self.request.user.organization,
            "cash_balance": cash,
            "bank_balance": bank,
            "customers": customers,
            "vendors": vendors,
        })
        return context


class CustomerLedgerView(TenantLoginRequiredMixin, TemplateView):
    """One customer's individual ledger: every invoice (debit) and payment
    (credit) in date order with a running balance — classic ledger format."""

    template_name = "finance/customer_ledger.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        customer = get_object_or_404(Customer, pk=kwargs["pk"])
        entries = services.customer_ledger_entries(customer)
        invoices = {r.pk: r for r in Receivable.objects.filter(customer=customer)}
        for row in entries:
            if row["source"]["role"] == "invoice":
                row["edit_form"] = ReceivableEditForm(
                    instance=invoices[row["source"]["pk"]], auto_id=f"id_edit_inv_{row['source']['pk']}_%s"
                )
        total_debit = sum((e["debit"] for e in entries), services.ZERO)
        total_credit = sum((e["credit"] for e in entries), services.ZERO)
        context.update({
            "active_nav": "ledgers",
            "organization": self.request.user.organization,
            "customer": customer,
            "entries": entries,
            "total_debit": total_debit,
            "total_credit": total_credit,
            "closing_balance": entries[-1]["balance"] if entries else services.ZERO,
        })
        return context


class VendorLedgerView(TenantLoginRequiredMixin, TemplateView):
    """One vendor's individual ledger: every bill (debit) and payment
    (credit) in date order with a running balance."""

    template_name = "finance/vendor_ledger.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        vendor = get_object_or_404(Vendor, pk=kwargs["pk"])
        entries = services.vendor_ledger_entries(vendor.name)
        bills = {p.pk: p for p in Payable.objects.filter(vendor=vendor.name)}
        for row in entries:
            if row["source"]["role"] == "invoice":
                row["edit_form"] = PayableEditForm(
                    instance=bills[row["source"]["pk"]], auto_id=f"id_edit_bill_{row['source']['pk']}_%s"
                )
        total_debit = sum((e["debit"] for e in entries), services.ZERO)
        total_credit = sum((e["credit"] for e in entries), services.ZERO)
        context.update({
            "active_nav": "ledgers",
            "organization": self.request.user.organization,
            "vendor": vendor,
            "entries": entries,
            "total_debit": total_debit,
            "total_credit": total_credit,
            "closing_balance": entries[-1]["balance"] if entries else services.ZERO,
        })
        return context


class AccountLedgerView(TenantLoginRequiredMixin, TemplateView):
    """The Cash-in-hand or Bank ledger: every sale (debit), expense/
    purchase (credit) and cash/bank transfer affecting this account, in
    date order with a running balance starting from its opening balance."""

    template_name = "finance/account_ledger.html"
    account = None
    account_label = None

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        entries = services.account_ledger_entries(self.account)
        total_debit = sum((e["debit"] for e in entries), services.ZERO)
        total_credit = sum((e["credit"] for e in entries), services.ZERO)
        context.update({
            "active_nav": "ledgers",
            "organization": self.request.user.organization,
            "account_label": self.account_label,
            "entries": entries,
            "total_debit": total_debit,
            "total_credit": total_credit,
            "closing_balance": entries[-1]["balance"] if entries else services.ZERO,
        })
        return context


def _opening_balance_payable(vendor):
    """The still-unpaid Payable created for this vendor's opening balance,
    if any — the one row it's safe to keep in sync with the vendor record."""
    return Payable.objects.filter(vendor=vendor.name, note="Opening balance", amount_paid=0).order_by(
        "-created_at"
    ).first()


class AddVendorView(TenantLoginRequiredMixin, View):
    def post(self, request, *args, **kwargs):
        form = VendorForm(request.POST)
        if form.is_valid():
            vendor = form.save()
            if vendor.opening_balance > 0:
                Payable.objects.create(
                    vendor=vendor.name,
                    bill_date=vendor.opening_balance_as_on,
                    due_date=vendor.opening_balance_as_on,
                    amount=vendor.opening_balance,
                    note="Opening balance",
                    created_by_email=request.user.email,
                )
            messages.success(request, "Vendor added.")
        else:
            messages.error(request, "Couldn't add that vendor — the name may already exist.")
        return redirect("finance:vendors")


class EditVendorView(TenantLoginRequiredMixin, TemplateView):
    template_name = "finance/edit_vendor.html"

    def get(self, request, pk, *args, **kwargs):
        vendor = get_object_or_404(Vendor, pk=pk)
        form = VendorEditForm(instance=vendor)
        return self.render(request, vendor, form)

    def post(self, request, pk, *args, **kwargs):
        vendor = get_object_or_404(Vendor, pk=pk)
        old_balance, old_as_on = vendor.opening_balance, vendor.opening_balance_as_on
        form = VendorEditForm(request.POST, instance=vendor)
        if not form.is_valid():
            return self.render(request, vendor, form)

        new_balance = form.cleaned_data["opening_balance"]
        balance_changed = new_balance != old_balance or form.cleaned_data["opening_balance_as_on"] != old_as_on
        payable = _opening_balance_payable(vendor) if balance_changed else None

        if balance_changed and payable is None and Payable.objects.filter(
            vendor=vendor.name, note="Opening balance"
        ).exclude(amount_paid=0).exists():
            messages.error(
                request,
                "This vendor's opening balance has already been partly paid — "
                "adjust it from Cash Position instead of here.",
            )
            return self.render(request, vendor, form)

        form.save()

        if balance_changed:
            if new_balance <= 0:
                if payable:
                    payable.delete()
            elif payable:
                payable.amount = new_balance
                payable.bill_date = vendor.opening_balance_as_on
                payable.due_date = vendor.opening_balance_as_on
                payable.save(update_fields=["amount", "bill_date", "due_date"])
            else:
                Payable.objects.create(
                    vendor=vendor.name,
                    bill_date=vendor.opening_balance_as_on,
                    due_date=vendor.opening_balance_as_on,
                    amount=new_balance,
                    note="Opening balance",
                    created_by_email=request.user.email,
                )

        messages.success(request, "Vendor updated.")
        return redirect("finance:vendors")

    def render(self, request, vendor, form):
        context = {
            "active_nav": "vendors",
            "organization": request.user.organization,
            "vendor": vendor,
            "form": form,
        }
        return self.render_to_response(context)


class DeleteVendorView(TenantLoginRequiredMixin, View):
    def post(self, request, pk, *args, **kwargs):
        vendor = get_object_or_404(Vendor, pk=pk)
        vendor.delete()
        messages.success(request, "Vendor deleted.")
        return redirect("finance:vendors")


class ToggleVendorView(TenantLoginRequiredMixin, View):
    def post(self, request, pk, *args, **kwargs):
        vendor = get_object_or_404(Vendor, pk=pk)
        vendor.is_active = not vendor.is_active
        vendor.save(update_fields=["is_active"])
        return redirect("finance:vendors")


class AgingReportView(TenantLoginRequiredMixin, TemplateView):
    """Who owes what and how overdue it is, one row per customer/vendor —
    the standard aging matrix, built from the same open receivables/
    payables Cash Position already tracks."""

    template_name = "finance/aging_report.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        today = datetime.date.today()
        context.update({
            "active_nav": "cash_position",
            "organization": self.request.user.organization,
            "as_of": today,
            "receivables_aging": services.receivables_aging(today),
            "payables_aging": services.payables_aging(today),
        })
        return context


class CashPositionView(TenantLoginRequiredMixin, PeriodMixin, TemplateView):
    """Receivables, payables, upcoming obligations and a cash-flow snapshot
    — everything the org is owed, everything it owes, and what's due soon."""

    template_name = "finance/cash_position.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        fs = services.get_finance_settings()
        period = self.get_period(fs.fy_start_month)
        summary = services.cash_position_summary()

        partner_rows = services.partner_balance_rows()

        context.update({
            "active_nav": "cash_position",
            "organization": self.request.user.organization,
            "period": period,
            "period_choices": PERIOD_CHOICES,
            "cash_flow": services.cash_flow_statement(period),
            "receivable_form": ReceivableForm(auto_id="id_receivable_%s"),
            "payable_form": PayableForm(auto_id="id_payable_%s"),
            "payment_form": RecordPaymentForm(auto_id="id_payment_%s"),
            "partner_form": PartnerForm(
                auto_id="id_partner_%s",
                initial={"opening_balance_as_on": resolve_period("this_fy", fs.fy_start_month).start},
            ),
            "partner_transaction_form": PartnerTransactionForm(auto_id="id_partner_txn_%s"),
            "partner_rows": partner_rows,
            "total_partner_net_capital": sum((r["net_capital"] for r in partner_rows), services.ZERO),
            "net_position": summary["total_receivable"] - summary["total_payable"],
            **summary,
        })
        return context


class AddReceivableView(TenantLoginRequiredMixin, View):
    def post(self, request, *args, **kwargs):
        form = ReceivableForm(request.POST)
        if form.is_valid():
            receivable = form.save(commit=False)
            receivable.created_by_email = request.user.email
            receivable.save()
            messages.success(request, f"Logged a receivable of {receivable.amount} for {receivable.customer}.")
        else:
            messages.error(request, "Couldn't save that receivable: " + "; ".join(
                f"{f}: {', '.join(e)}" for f, e in form.errors.items()
            ))
        return redirect("finance:cash_position")


class DeleteReceivableView(TenantLoginRequiredMixin, View):
    def post(self, request, pk, *args, **kwargs):
        receivable = get_object_or_404(Receivable, pk=pk)
        if receivable.amount_received > 0:
            messages.error(request, "Can't delete an invoice that already has payments recorded against it.")
        else:
            receivable.delete()
            messages.success(request, "Receivable deleted.")
        return redirect(request.POST.get("next") or "finance:cash_position")


class RecordReceivablePaymentView(TenantLoginRequiredMixin, View):
    def post(self, request, pk, *args, **kwargs):
        receivable = get_object_or_404(Receivable, pk=pk)
        form = RecordPaymentForm(request.POST)
        if form.is_valid():
            amount = form.cleaned_data["amount"]
            if amount > receivable.balance:
                messages.error(request, f"That's more than the outstanding balance of {receivable.balance}.")
            else:
                SalesEntry.objects.create(
                    date=form.cleaned_data["date"],
                    customer=receivable.customer,
                    amount=amount,
                    payment_mode=form.cleaned_data["payment_mode"],
                    note=form.cleaned_data["note"] or f"Payment received for invoice — {receivable.customer}",
                    created_by_email=request.user.email,
                )
                receivable.amount_received += amount
                receivable.save(update_fields=["amount_received"])
                messages.success(request, f"Recorded a payment of {amount} against this receivable.")
        else:
            messages.error(request, "Couldn't record that payment: " + "; ".join(
                f"{f}: {', '.join(e)}" for f, e in form.errors.items()
            ))
        return redirect("finance:cash_position")


class AddPayableView(TenantLoginRequiredMixin, View):
    def post(self, request, *args, **kwargs):
        form = PayableForm(request.POST)
        if form.is_valid():
            payable = form.save(commit=False)
            payable.created_by_email = request.user.email
            payable.save()
            messages.success(request, f"Logged a payable of {payable.amount} to {payable.vendor}.")
        else:
            messages.error(request, "Couldn't save that payable: " + "; ".join(
                f"{f}: {', '.join(e)}" for f, e in form.errors.items()
            ))
        return redirect("finance:cash_position")


class DeletePayableView(TenantLoginRequiredMixin, View):
    def post(self, request, pk, *args, **kwargs):
        payable = get_object_or_404(Payable, pk=pk)
        if payable.amount_paid > 0:
            messages.error(request, "Can't delete a bill that already has payments recorded against it.")
        else:
            payable.delete()
            messages.success(request, "Payable deleted.")
        return redirect(request.POST.get("next") or "finance:cash_position")


class RecordPayablePaymentView(TenantLoginRequiredMixin, View):
    def post(self, request, pk, *args, **kwargs):
        payable = get_object_or_404(Payable, pk=pk)
        form = RecordPaymentForm(request.POST)
        if form.is_valid():
            amount = form.cleaned_data["amount"]
            if amount > payable.balance:
                messages.error(request, f"That's more than the outstanding balance of {payable.balance}.")
            else:
                PurchaseEntry.objects.create(
                    date=form.cleaned_data["date"],
                    vendor=payable.vendor,
                    amount=amount,
                    payment_mode=form.cleaned_data["payment_mode"],
                    note=form.cleaned_data["note"] or f"Payment to {payable.vendor} for bill",
                    created_by_email=request.user.email,
                )
                payable.amount_paid += amount
                payable.save(update_fields=["amount_paid"])
                messages.success(request, f"Recorded a payment of {amount} against this payable.")
        else:
            messages.error(request, "Couldn't record that payment: " + "; ".join(
                f"{f}: {', '.join(e)}" for f, e in form.errors.items()
            ))
        return redirect("finance:cash_position")


class AddPartnerView(TenantLoginRequiredMixin, View):
    def post(self, request, *args, **kwargs):
        form = PartnerForm(request.POST)
        if form.is_valid():
            partner = form.save()
            if partner.opening_balance > 0:
                PartnerTransaction.objects.create(
                    partner=partner,
                    date=partner.opening_balance_as_on,
                    kind=PartnerTransaction.Kind.INVESTMENT,
                    amount=partner.opening_balance,
                    note="Opening balance",
                    created_by_email=request.user.email,
                )
            messages.success(request, "Partner added.")
        else:
            messages.error(request, "Couldn't add that partner — the name may already exist.")
        return redirect("finance:cash_position")


class AddPartnerTransactionView(TenantLoginRequiredMixin, View):
    def post(self, request, *args, **kwargs):
        form = PartnerTransactionForm(request.POST, min_date=_historical_min_date(request))
        if form.is_valid():
            txn = form.save(commit=False)
            txn.created_by_email = request.user.email
            txn.save()
            verb = "invested" if txn.kind == PartnerTransaction.Kind.INVESTMENT else "withdrew"
            messages.success(request, f"{txn.partner} {verb} {txn.amount}.")
        else:
            messages.error(request, "Couldn't save that transaction: " + "; ".join(
                f"{f}: {', '.join(e)}" for f, e in form.errors.items()
            ))
        return redirect("finance:cash_position")


class EditPartnerTransactionView(TenantLoginRequiredMixin, View):
    def post(self, request, pk, *args, **kwargs):
        txn = get_object_or_404(PartnerTransaction, pk=pk)
        form = PartnerTransactionForm(request.POST, instance=txn, min_date=_historical_min_date(request))
        if form.is_valid():
            form.save()
            messages.success(request, f"Updated {txn.partner}'s transaction on {txn.date:%d %b %Y}.")
        else:
            messages.error(request, "Couldn't save that transaction: " + "; ".join(
                f"{f}: {', '.join(e)}" for f, e in form.errors.items()
            ))
        return redirect(request.POST.get("next") or "finance:cash_position")


class EditReceivableView(TenantLoginRequiredMixin, View):
    def post(self, request, pk, *args, **kwargs):
        receivable = get_object_or_404(Receivable, pk=pk)
        form = ReceivableEditForm(request.POST, instance=receivable)
        if form.is_valid():
            form.save()
            messages.success(request, "Invoice updated.")
        else:
            messages.error(request, "Couldn't save that invoice: " + "; ".join(
                f"{f}: {', '.join(e)}" for f, e in form.errors.items()
            ))
        return redirect(request.POST.get("next") or "finance:cash_position")


class EditPayableView(TenantLoginRequiredMixin, View):
    def post(self, request, pk, *args, **kwargs):
        payable = get_object_or_404(Payable, pk=pk)
        form = PayableEditForm(request.POST, instance=payable)
        if form.is_valid():
            form.save()
            messages.success(request, "Bill updated.")
        else:
            messages.error(request, "Couldn't save that bill: " + "; ".join(
                f"{f}: {', '.join(e)}" for f, e in form.errors.items()
            ))
        return redirect(request.POST.get("next") or "finance:cash_position")


class DeletePartnerTransactionView(TenantLoginRequiredMixin, View):
    def post(self, request, pk, *args, **kwargs):
        txn = get_object_or_404(PartnerTransaction, pk=pk)
        txn.delete()
        messages.success(request, "Partner transaction deleted.")
        return redirect(request.POST.get("next") or "finance:cash_position")


class PartnerLedgerView(TenantLoginRequiredMixin, TemplateView):
    """One partner's full investment/withdrawal history with a running
    net-capital balance."""

    template_name = "finance/partner_ledger.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        partner = get_object_or_404(Partner, pk=kwargs["pk"])
        entries = services.partner_ledger_entries(partner)
        min_date = _historical_min_date(self.request)
        txns = {t.pk: t for t in partner.transactions.all()}
        for row in entries:
            txn = txns.get(row["source"]["pk"])
            row["edit_form"] = PartnerTransactionForm(
                instance=txn, auto_id=f"id_edit_ptxn_{txn.pk}_%s", min_date=min_date
            )
            row["txn"] = txn
        total_debit = sum((e["debit"] for e in entries), services.ZERO)
        total_credit = sum((e["credit"] for e in entries), services.ZERO)
        context.update({
            "active_nav": "cash_position",
            "organization": self.request.user.organization,
            "partner": partner,
            "entries": entries,
            "total_invested": total_debit,
            "total_withdrawn": total_credit,
            "net_capital": entries[-1]["balance"] if entries else services.ZERO,
        })
        return context

