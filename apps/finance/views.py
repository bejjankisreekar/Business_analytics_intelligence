import datetime
import json
from decimal import Decimal

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import logout
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Sum
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
    PayableForm,
    PurchaseEntryForm,
    PurchaseEntryFormSet,
    ReceivableForm,
    RecordPaymentForm,
    SalesEntryForm,
    SalesEntryFormSet,
    SubcategoryForm,
    VendorEditForm,
    VendorForm,
)
from .models import (
    CashTransfer,
    Category,
    Customer,
    ExpenseEntry,
    Payable,
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
    return json.dumps(data, default=_decimal_default)


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
        weekly = services.weekly_trend(12)
        trend = services.monthly_trend(6)
        weekday = services.weekday_averages(period.start, period.end)
        expense_breakdown = services.category_breakdown(ExpenseEntry, period.start, period.end)
        purchase_breakdown = services.category_breakdown(PurchaseEntry, period.start, period.end)
        channel_breakdown = services.category_breakdown(SalesEntry, period.start, period.end, field="channel")
        payment_breakdown = services.payment_mode_breakdown(period.start, period.end)
        product_revenue = services.category_breakdown(SalesEntry, period.start, period.end, field="subcategory")

        cash_running = []
        running = services.total_balance_as_of(period.start - datetime.timedelta(days=1))
        for row in series:
            running = running + row["net"]
            cash_running.append({"date": row["date"], "balance": running})

        context.update({
            "active_nav": "analytics",
            "organization": self.request.user.organization,
            "period": period,
            "period_choices": PERIOD_CHOICES,
            "chart_daily_labels": to_json([r["date"].strftime("%d %b") for r in series]),
            "chart_daily_sales": to_json([r["sales"] for r in series]),
            "chart_daily_expenses": to_json([r["expenses"] for r in series]),
            "chart_daily_purchases": to_json([r["purchases"] for r in series]),
            "chart_daily_net": to_json([r["net"] for r in series]),
            "chart_weekly_labels": to_json([r["label"] for r in weekly]),
            "chart_weekly_sales": to_json([r["sales"] for r in weekly]),
            "chart_weekly_expenses": to_json([r["expenses"] for r in weekly]),
            "chart_weekly_purchases": to_json([r["purchases"] for r in weekly]),
            "chart_monthly_labels": to_json([r["month"] for r in trend]),
            "chart_monthly_sales": to_json([r["sales"] for r in trend]),
            "chart_monthly_expenses": to_json([r["expenses"] for r in trend]),
            "chart_monthly_purchases": to_json([r["purchases"] for r in trend]),
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
            "product_revenue": product_revenue,
            "chart_product_revenue_labels": to_json([r["name"] for r in product_revenue]),
            "chart_product_revenue_values": to_json([r["amount"] for r in product_revenue]),
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
        series = services.daily_series(period.start, period.end)
        weekly = services.weekly_trend(12)
        trend = services.monthly_trend(6)
        weekday = services.weekday_averages(period.start, period.end)
        channel_breakdown = services.category_breakdown(SalesEntry, period.start, period.end, field="channel")
        product_category_breakdown = services.category_breakdown(
            SalesEntry, period.start, period.end, field="product_category"
        )
        product_revenue = services.category_breakdown(SalesEntry, period.start, period.end, field="subcategory")[:10]
        payment_breakdown = services.payment_mode_breakdown(period.start, period.end, models=(SalesEntry,))
        sales_insights = services.sales_insights(fs.fy_start_month, weekday)
        vs_prev = services.sales_vs_previous_period(period)
        perf_trend = services.sales_performance_trend(6)

        context.update({
            "active_nav": "sales_intelligence",
            "organization": self.request.user.organization,
            "period": period,
            "period_choices": PERIOD_CHOICES,
            "kpis": kpis,
            "chart_daily_labels": to_json([r["date"].strftime("%d %b") for r in series]),
            "chart_daily_sales": to_json([r["sales"] for r in series]),
            "chart_weekly_labels": to_json([r["label"] for r in weekly]),
            "chart_weekly_sales": to_json([r["sales"] for r in weekly]),
            "chart_monthly_labels": to_json([r["month"] for r in trend]),
            "chart_monthly_sales": to_json([r["sales"] for r in trend]),
            "chart_weekday_labels": to_json([r["day"] for r in weekday]),
            "chart_weekday_avg": to_json([r["average"] for r in weekday]),
            "chart_channel_labels": to_json([r["name"] for r in channel_breakdown]),
            "chart_channel_values": to_json([r["amount"] for r in channel_breakdown]),
            "chart_product_category_labels": to_json([r["name"] for r in product_category_breakdown]),
            "chart_product_category_values": to_json([r["amount"] for r in product_category_breakdown]),
            "chart_product_revenue_labels": to_json([r["name"] for r in product_revenue]),
            "chart_product_revenue_values": to_json([r["amount"] for r in product_revenue]),
            "chart_payment_labels": to_json([r["name"] for r in payment_breakdown]),
            "chart_payment_values": to_json([r["amount"] for r in payment_breakdown]),
            "chart_vs_prev_labels": to_json([vs_prev["prev_label"], vs_prev["current_label"]]),
            "chart_vs_prev_values": to_json([vs_prev["prev_sales"], vs_prev["current_sales"]]),
            "chart_perf_labels": to_json([r["month"] for r in perf_trend]),
            "chart_perf_revenue": to_json([r["revenue"] for r in perf_trend]),
            "chart_perf_profit": to_json([r["profit"] for r in perf_trend]),
            "chart_perf_orders": to_json([r["orders"] for r in perf_trend]),
            "chart_perf_aov": to_json([r["avg_order_value"] for r in perf_trend]),
            "chart_perf_margin": to_json([r["gross_margin_pct"] for r in perf_trend]),
            "product_revenue": product_revenue,
            "sales_insights": sales_insights,
            "vs_prev": vs_prev,
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
        purchase_breakdown = services.category_breakdown(PurchaseEntry, period.start, period.end)
        expense_breakdown = services.category_breakdown(ExpenseEntry, period.start, period.end)
        product_category_breakdown = services.category_breakdown(
            PurchaseEntry, period.start, period.end, field="product_category"
        )
        vendor_breakdown = services.vendor_breakdown(period.start, period.end)
        payment_breakdown = services.payment_mode_breakdown(
            period.start, period.end, models=(ExpenseEntry, PurchaseEntry)
        )
        expense_analysis = services.expense_month_comparison(fs.fy_start_month)
        expense_asc = sorted(expense_analysis["rows"], key=lambda r: r["this_month"])

        context.update({
            "active_nav": "purchase_expense_intelligence",
            "organization": self.request.user.organization,
            "period": period,
            "period_choices": PERIOD_CHOICES,
            "kpis": kpis,
            "total_outflow": kpis["expenses"] + kpis["purchases"],
            "chart_daily_labels": to_json([r["date"].strftime("%d %b") for r in series]),
            "chart_daily_expenses": to_json([r["expenses"] for r in series]),
            "chart_daily_purchases": to_json([r["purchases"] for r in series]),
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
            "chart_product_category_labels": to_json([r["name"] for r in product_category_breakdown]),
            "chart_product_category_values": to_json([r["amount"] for r in product_category_breakdown]),
            "chart_vendor_labels": to_json([r["name"] for r in vendor_breakdown[:10]]),
            "chart_vendor_values": to_json([r["amount"] for r in vendor_breakdown[:10]]),
            "chart_payment_labels": to_json([r["name"] for r in payment_breakdown]),
            "chart_payment_values": to_json([r["amount"] for r in payment_breakdown]),
            "purchase_breakdown": purchase_breakdown,
            "expense_breakdown": expense_breakdown,
            "vendor_breakdown": vendor_breakdown[:10],
            "expense_analysis": expense_analysis,
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

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        selected_date = self._selected_date()
        report = services.daily_report(selected_date)

        context.update({
            "active_nav": "daily_report",
            "organization": self.request.user.organization,
            "report": report,
            "today": datetime.date.today(),
            "prev_date": selected_date - datetime.timedelta(days=1),
            "next_date": selected_date + datetime.timedelta(days=1),
            "sale_form": SalesEntryForm(
                initial={"date": selected_date}, auto_id="id_sale_%s", min_date=_historical_min_date(self.request)
            ),
            "expense_form": ExpenseEntryForm(
                initial={"date": selected_date}, auto_id="id_expense_%s", min_date=_historical_min_date(self.request)
            ),
            "purchase_form": PurchaseEntryForm(
                initial={"date": selected_date}, auto_id="id_purchase_%s", min_date=_historical_min_date(self.request)
            ),
            "transfer_form": CashTransferForm(
                initial={"date": selected_date}, auto_id="id_transfer_%s", min_date=_historical_min_date(self.request)
            ),
            "subcategory_map_json": to_json(services.subcategory_map()),
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
            queryset=SalesEntry.objects.none(), prefix="sale",
            initial=[{"date": selected_date}] * SalesEntryFormSet.extra,
            form_kwargs={"min_date": min_date},
        ),
        "expense_formset": expense_formset or ExpenseEntryFormSet(
            queryset=ExpenseEntry.objects.none(), prefix="expense",
            initial=[{"date": selected_date}] * ExpenseEntryFormSet.extra,
            form_kwargs={"min_date": min_date},
        ),
        "purchase_formset": purchase_formset or PurchaseEntryFormSet(
            queryset=PurchaseEntry.objects.none(), prefix="purchase",
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
        context.update(_bulk_entry_context(self.request, selected_date))
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
        request.POST, queryset=model.objects.none(), prefix=prefix,
        initial=[{"date": selected_date}] * total,
        form_kwargs={"min_date": _historical_min_date(request)},
    )


def _save_bulk_formset(formset, request) -> tuple[int, list]:
    """Save every non-empty row in a validated formset, tagging
    created_by_email — mirrors what the single-entry Add*View classes do
    per row. Fully-blank extra rows are already excluded by Django's
    empty_permitted handling before this is called. Returns the count and
    the saved model instances, so the caller can show a detailed recap of
    exactly what was just logged."""
    count = 0
    saved = []
    for form in formset:
        if not form.cleaned_data:
            continue
        entry = form.save(commit=False)
        entry.created_by_email = request.user.email
        entry.save()
        saved.append(entry)
        count += 1
    return count, saved


def _describe_saved(entries, kind: str) -> list[dict]:
    """Turn just-saved SalesEntry/ExpenseEntry/PurchaseEntry rows into the
    detailed recap shown on the bulk-entry page: category/channel label,
    date, amount, payment mode and note per row."""
    rows = []
    for e in entries:
        if kind == "sale":
            label = e.channel.name if e.channel else "Sales"
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
            count, saved = _save_bulk_formset(formset, request)
            if count:
                messages.success(request, f"Logged {count} purchase{'s' if count != 1 else ''}.")
            context = _bulk_entry_context(request, selected_date, active_bulk_tab="purchase")
            context["just_saved_type"] = "purchase"
            context["just_saved_entries"] = _describe_saved(saved, "purchase")
            return render(request, "finance/daily_bulk_entry.html", context)
        messages.error(request, "Couldn't save one or more purchase rows — the errors are highlighted below.")
        context = _bulk_entry_context(
            request, selected_date, active_bulk_tab="purchase", purchase_formset=formset
        )
        return render(request, "finance/daily_bulk_entry.html", context)


class AddSaleView(TenantLoginRequiredMixin, View):
    def post(self, request, *args, **kwargs):
        form = SalesEntryForm(request.POST, min_date=_historical_min_date(request))
        if form.is_valid():
            entry = form.save(commit=False)
            entry.created_by_email = request.user.email
            entry.save()
            messages.success(request, f"Logged sale of {entry.amount} on {entry.date:%d %b %Y}.")
        else:
            messages.error(request, "Couldn't save that sale: " + "; ".join(
                f"{f}: {', '.join(e)}" for f, e in form.errors.items()
            ))
        return redirect(request.POST.get("next") or "finance:dashboard")


class AddExpenseView(TenantLoginRequiredMixin, View):
    def post(self, request, *args, **kwargs):
        form = ExpenseEntryForm(request.POST, min_date=_historical_min_date(request))
        if form.is_valid():
            entry = form.save(commit=False)
            entry.created_by_email = request.user.email
            entry.save()
            messages.success(request, f"Logged expense of {entry.amount} on {entry.date:%d %b %Y}.")
        else:
            messages.error(request, "Couldn't save that expense: " + "; ".join(
                f"{f}: {', '.join(e)}" for f, e in form.errors.items()
            ))
        return redirect(request.POST.get("next") or "finance:dashboard")


class AddPurchaseView(TenantLoginRequiredMixin, View):
    def post(self, request, *args, **kwargs):
        form = PurchaseEntryForm(request.POST, min_date=_historical_min_date(request))
        if form.is_valid():
            entry = form.save(commit=False)
            entry.created_by_email = request.user.email
            entry.save()
            messages.success(request, f"Logged purchase of {entry.amount} on {entry.date:%d %b %Y}.")
        else:
            messages.error(request, "Couldn't save that purchase: " + "; ".join(
                f"{f}: {', '.join(e)}" for f, e in form.errors.items()
            ))
        return redirect(request.POST.get("next") or "finance:dashboard")


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


ENTRY_MODELS = {"sale": SalesEntry, "expense": ExpenseEntry, "purchase": PurchaseEntry, "transfer": CashTransfer}
ENTRY_FORMS = {
    "sale": SalesEntryForm, "expense": ExpenseEntryForm, "purchase": PurchaseEntryForm,
    "transfer": CashTransferForm,
}
ENTRY_LABELS = {"sale": "sale", "expense": "expense", "purchase": "purchase", "transfer": "transfer"}


class DeleteEntryView(TenantLoginRequiredMixin, View):
    def post(self, request, entry_type, pk, *args, **kwargs):
        model = ENTRY_MODELS.get(entry_type)
        if model is None:
            messages.error(request, "Unknown entry type.")
            return redirect("finance:entries")
        entry = get_object_or_404(model, pk=pk)
        entry.delete()
        messages.success(request, "Entry deleted.")
        return redirect(request.POST.get("next") or "finance:entries")


class EditEntryView(TenantLoginRequiredMixin, TemplateView):
    template_name = "finance/edit_entry.html"

    def _get_entry(self, entry_type, pk):
        model = ENTRY_MODELS.get(entry_type)
        if model is None:
            return None, None
        return model, get_object_or_404(model, pk=pk)

    def get(self, request, entry_type, pk, *args, **kwargs):
        model, entry = self._get_entry(entry_type, pk)
        if model is None:
            messages.error(request, "Unknown entry type.")
            return redirect("finance:entries")
        form_class = ENTRY_FORMS[entry_type]
        form = form_class(instance=entry, min_date=_historical_min_date(request))
        return self.render(request, entry_type, entry, form)

    def post(self, request, entry_type, pk, *args, **kwargs):
        model, entry = self._get_entry(entry_type, pk)
        next_url = request.POST.get("next") or "finance:entries"
        if model is None:
            messages.error(request, "Unknown entry type.")
            return redirect(next_url)
        form_class = ENTRY_FORMS[entry_type]
        form = form_class(request.POST, instance=entry, min_date=_historical_min_date(request))
        if form.is_valid():
            form.save()
            messages.success(request, f"Updated {ENTRY_LABELS[entry_type]} on {entry.date:%d %b %Y}.")
        else:
            messages.error(request, "Couldn't save that entry: " + "; ".join(
                f"{f}: {', '.join(e)}" for f, e in form.errors.items()
            ))
        return redirect(next_url)

    def render(self, request, entry_type, entry, form):
        context = {
            "active_nav": "entries",
            "organization": request.user.organization,
            "entry_type": entry_type,
            "entry": entry,
            "form": form,
            "next": request.GET.get("next") or request.POST.get("next") or "",
            "subcategory_map_json": to_json(services.subcategory_map()),
        }
        return self.render_to_response(context)


class EntriesView(TenantLoginRequiredMixin, TemplateView):
    template_name = "finance/entries.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        entry_type = self.request.GET.get("type", "sale")
        date_filter = None
        raw_date = self.request.GET.get("date")
        if raw_date:
            try:
                date_filter = datetime.date.fromisoformat(raw_date)
            except ValueError:
                pass
        model = ENTRY_MODELS.get(entry_type, SalesEntry)
        qs = model.objects.all()
        if entry_type == "sale":
            qs = qs.select_related("channel", "subcategory")
        elif entry_type in ("expense", "purchase"):
            qs = qs.select_related("category", "subcategory")
        if date_filter:
            qs = qs.filter(date=date_filter)
        qs = qs.order_by("-date", "-created_at")[:200]
        edit_form_class = ENTRY_FORMS[entry_type]
        min_date = _historical_min_date(self.request)
        entries = list(qs)
        for e in entries:
            e.edit_form = edit_form_class(instance=e, auto_id=f"id_edit_{e.pk}_%s", min_date=min_date)
        context.update({
            "active_nav": "entries",
            "organization": self.request.user.organization,
            "entry_type": entry_type,
            "date_filter": date_filter,
            "tabs": [
                ("sale", "Sales"), ("expense", "Expenses"), ("purchase", "Purchases"),
                ("transfer", "Cash ⇄ Bank"),
            ],
            "entries": entries,
            "sale_form": SalesEntryForm(auto_id="id_sale_%s", min_date=min_date),
            "expense_form": ExpenseEntryForm(auto_id="id_expense_%s", min_date=min_date),
            "purchase_form": PurchaseEntryForm(auto_id="id_purchase_%s", min_date=min_date),
            "transfer_form": CashTransferForm(auto_id="id_transfer_%s", min_date=min_date),
            "subcategory_map_json": to_json(services.subcategory_map()),
        })
        return context


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
    (Category.Kind.SALES, "Sales Categories", "e.g. In-store, Online", "e.g. a brand"),
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
        categories = Category.objects.filter(kind=kind).prefetch_related("subcategories")
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


class CashPositionView(TenantLoginRequiredMixin, PeriodMixin, TemplateView):
    """Receivables, payables, upcoming obligations and a cash-flow snapshot
    — everything the org is owed, everything it owes, and what's due soon."""

    template_name = "finance/cash_position.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        fs = services.get_finance_settings()
        period = self.get_period(fs.fy_start_month)
        summary = services.cash_position_summary()

        context.update({
            "active_nav": "cash_position",
            "organization": self.request.user.organization,
            "period": period,
            "period_choices": PERIOD_CHOICES,
            "cash_flow": services.cash_flow_statement(period),
            "receivable_form": ReceivableForm(auto_id="id_receivable_%s"),
            "payable_form": PayableForm(auto_id="id_payable_%s"),
            "payment_form": RecordPaymentForm(auto_id="id_payment_%s"),
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
            messages.error(request, "Can't delete a receivable that already has payments recorded against it.")
        else:
            receivable.delete()
            messages.success(request, "Receivable deleted.")
        return redirect("finance:cash_position")


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
                    product_category=receivable.product_category,
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
            messages.error(request, "Can't delete a payable that already has payments recorded against it.")
        else:
            payable.delete()
            messages.success(request, "Payable deleted.")
        return redirect("finance:cash_position")


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
                    product_category=payable.product_category,
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
