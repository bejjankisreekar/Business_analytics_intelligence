import calendar
import csv
import datetime
import io
import json
from decimal import Decimal

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import logout
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.paginator import Paginator
from django.db.models import Count, Prefetch, Q, Sum
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.generic import TemplateView, View

from apps.billing import payments as billing_payments
from apps.billing import razorpay_client
from apps.billing import services as billing_services
from apps.billing.models import Invoice, Payment
from apps.organizations.models import Organization

from . import imports, services
from .forms import (
    BankAccountEditForm,
    BankAccountForm,
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
    sales_import_formset,
)
from .models import (
    BankAccount,
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
    category (e.g. New Mobiles, Accessories) so the Analytics page can
    render a dedicated small chart per category instead of one combined
    chart mixing every category together."""
    groups: dict[str, list[dict]] = {}
    for row in product_quantity:
        groups.setdefault(row["category"], []).append(row)

    result = []
    for category, rows in groups.items():
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


class ManagerAccountRestrictedMixin:
    """Restricts manager accounts to daily entry views only.
    Add to views that should not be accessible to manager accounts."""

    def dispatch(self, request, *args, **kwargs):
        from apps.accounts.models import User

        user = request.user
        if user.is_authenticated and user.role == User.Role.MANAGER:
            return redirect("finance:daily_report")
        return super().dispatch(request, *args, **kwargs)


class TenantLoginRequiredMixin(LoginRequiredMixin):
    """Every tenant-facing view requires this. Two independent gates:

    1. Organization.is_service_active — a superadmin's manual stop/suspend
       switch. Tripped, it logs the user out entirely (existing behavior) -
       unless the service was stopped *for a pending payment*
       (Organization.is_payment_hold): that client stays signed in and is
       limited to the Billing page, like a lapsed subscription below.
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
            request.billing_locked = False
            if not org.is_service_active:
                if not org.is_payment_hold:
                    return redirect("accounts:suspended")
                request.billing_locked = True
                if not self.allow_when_locked:
                    return redirect("finance:billing")
            elif billing_services.is_account_suspended(org):
                return redirect("accounts:suspended")
            elif not billing_services.has_active_access(org):
                request.billing_locked = True
                if not self.allow_when_locked:
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


class DashboardView(ManagerAccountRestrictedMixin, TenantLoginRequiredMixin, TemplateView):
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
    apps.billing models live in our own shared database, same as
    Organization/User — not the org's Google Sheet — so this queries
    the default connection directly.

    allow_when_locked=True: this is the one page a lapsed (unpaid) org can
    still reach — otherwise they could never see or pay the invoice that
    would lift the lock."""

    template_name = "finance/billing.html"
    allow_when_locked = True

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        org = self.request.user.organization
        if org is None:
            return context
        payment_hold = org.is_payment_hold
        lapsed = not billing_services.has_active_access(org)
        locked = payment_hold or lapsed
        if lapsed:
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
        subscription = billing_services.get_current_subscription(org.id)
        # Same gross -> discount -> net the public pricing section shows for
        # this plan, and what a renewal invoice bills (services._renewal_amount).
        # CUSTOM cycles have no plan-defined price, so nothing to break down.
        plan_pricing = None
        if subscription is not None and subscription.billing_cycle != subscription.BillingCycle.CUSTOM:
            plan = subscription.plan
            yearly = subscription.billing_cycle == subscription.BillingCycle.YEARLY
            gross = plan.yearly_price if yearly else plan.monthly_price
            net = plan.effective_yearly_price if yearly else plan.effective_monthly_price
            if gross:
                plan_pricing = {
                    "gross": gross,
                    "discount": gross - net,
                    "discount_percent": plan.yearly_discount_percent if yearly else plan.monthly_discount_percent,
                    "net": net,
                    "period": "year" if yearly else "month",
                }
        context.update(
            {
                "active_nav": "billing",
                "organization": org,
                "subscription": subscription,
                "plan_pricing": plan_pricing,
                "invoices": invoices,
                "payments": Payment.objects.filter(organization_id=org.id).order_by("-payment_date")[:15],
                "outstanding": outstanding,
                "razorpay_configured": razorpay_client.is_configured(),
                "razorpay_payment_button_id": settings.RAZORPAY_PAYMENT_BUTTON_ID,
                "razorpay_payment_button_amount": settings.RAZORPAY_PAYMENT_BUTTON_AMOUNT,
                "locked": locked,
                "payment_hold": payment_hold,
                "discounted_invoice": next(
                    (
                        inv for inv in invoices
                        if inv.discount > 0 and inv.amount_due > 0
                        and inv.status not in (Invoice.Status.PAID, Invoice.Status.CANCELLED, Invoice.Status.DRAFT)
                    ),
                    None,
                ),
            }
        )
        return context


class InvoiceDetailView(TenantLoginRequiredMixin, TemplateView):
    """The org's own invoice-formatted view of one of its own invoices —
    line items, the billing period it covers (its subscription's start/end
    dates), payments made against it, and a coupon can be applied or
    (unlike the Billing page's plain "Apply") replaced here. Reached via
    "View invoice" on the Billing page.

    allow_when_locked=True: same reasoning as BillingView — a lapsed org
    must still be able to see the very invoice that's blocking them."""

    template_name = "finance/invoice_detail.html"
    allow_when_locked = True

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        org = self.request.user.organization
        invoice = get_object_or_404(
            Invoice.objects.select_related(
                "coupon_redemption__coupon", "subscription", "subscription__plan"
            ),
            pk=kwargs["pk"],
            organization_id=org.id,
        )
        context.update({
            "active_nav": "billing",
            "organization": org,
            "invoice": invoice,
            "payments": invoice.payments.order_by("-payment_date"),
            "can_manage_coupon": (
                invoice.status not in (Invoice.Status.PAID, Invoice.Status.CANCELLED)
                and invoice.amount_paid == 0
            ),
        })
        return context


class CreateInvoicePaymentOrderView(TenantLoginRequiredMixin, View):
    """Creates a Razorpay Order for one of this org's own outstanding
    invoices and hands back just enough for the Checkout popup to open.
    apps.billing models live in our own shared database, same as
    BillingView above — queried directly."""

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
    lapsed org bring their reactivation invoice down before paying it.

    `replace=1` (only sent by the invoice detail page's "Replace coupon"
    form) swaps out whatever coupon is already on the invoice instead of
    rejecting the request — see services.redeem_coupon."""

    allow_when_locked = True

    def post(self, request, pk, *args, **kwargs):
        org = request.user.organization
        invoice = get_object_or_404(Invoice, pk=pk, organization_id=org.id)
        code = request.POST.get("code", "")
        replace = request.POST.get("replace") == "1"
        try:
            redemption = billing_services.redeem_coupon(
                organization=org, invoice=invoice, code=code, replace=replace
            )
        except billing_services.CouponError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(
                request,
                f"Coupon {redemption.coupon.code} applied — {org.currency} {redemption.discount_amount} off "
                f"invoice {invoice.invoice_number}.",
            )
        return redirect(request.POST.get("next") or "finance:billing")


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


class AnalyticsView(ManagerAccountRestrictedMixin, TenantLoginRequiredMixin, PeriodMixin, TemplateView):
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
        purchase_quantity = services.product_quantity_breakdown(period.start, period.end, model=PurchaseEntry)

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
        organization = self.request.user.organization
        quantity_word = "count" if organization.business_type == Organization.BusinessType.HEALTHCARE else "units"

        context.update({
            "active_nav": "analytics",
            "organization": organization,
            "quantity_word": quantity_word,
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
            "purchase_quantity_groups": _product_quantity_groups(purchase_quantity),
        })
        return context


class SalesIntelligenceView(ManagerAccountRestrictedMixin, TenantLoginRequiredMixin, PeriodMixin, TemplateView):
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
        gdn_totals = services.gross_discount_net_totals(period.start, period.end)
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
            "gdn_totals": gdn_totals,
            "chart_gdn_labels": to_json(["Net Revenue", "Discount Given"]),
            "chart_gdn_values": to_json([gdn_totals["net"], gdn_totals["discount"]]),
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
            "has_breakdown": bool(breakdown_tree),
            "has_trend": bool(drilldown and drilldown["daily"]["series"]),
            "chart_drilldown_daily": to_json(drilldown["daily"] if drilldown else {"labels": [], "series": []}),
            "chart_drilldown_weekly": to_json(drilldown["weekly"] if drilldown else {"labels": [], "series": []}),
            "chart_drilldown_monthly": to_json(drilldown["monthly"] if drilldown else {"labels": [], "series": []}),
        })
        return context


class SubcategoryDetailView(TenantLoginRequiredMixin, PeriodMixin, View):
    """JSON behind the click-through on the Analytics quantity charts: units,
    revenue, purchases, items and estimated stock for one sub-category."""

    def get(self, request, *args, **kwargs):
        kind = request.GET.get("kind", "sale")
        category, name = request.GET.get("category", ""), request.GET.get("name", "")
        if kind not in ("sale", "purchase") or not category or not name:
            return JsonResponse({"error": "kind, category and name are required"}, status=400)
        period = self.get_period(services.get_finance_settings().fy_start_month)
        detail = services.subcategory_detail(kind, category, name, period)
        if detail is None:
            return JsonResponse({"error": "Sub-category not found"}, status=404)
        return JsonResponse(json.loads(to_json(detail)))


class ExpenseCategoryTrendView(TenantLoginRequiredMixin, View):
    """JSON behind the click-through on the Expense Analysis table: one
    category's spend over time, for the bar-chart popup. `granularity`
    picks the window — daily is the current calendar month up to today,
    weekly the last 12 weeks, monthly the last 6 months — same bucketing
    the page's own Cost trend chart uses, so the two read alike."""

    def get(self, request, *args, **kwargs):
        category_name = request.GET.get("category", "")
        if not category_name:
            return JsonResponse({"error": "category is required"}, status=400)
        granularity = request.GET.get("granularity", "daily")

        if granularity == "weekly":
            series = services.expense_category_weekly_trend(category_name)
            period_label = "Last 12 weeks"
        elif granularity == "monthly":
            series = services.expense_category_monthly_trend(category_name)
            period_label = "Last 6 months"
        else:
            granularity = "daily"
            fs = services.get_finance_settings()
            this_month = resolve_period("this_month", fs.fy_start_month)
            today = datetime.date.today()
            end = min(this_month.end, today)
            daily = services.expense_category_daily_trend(category_name, this_month.start, end)
            series = [{"label": r["date"].strftime("%d %b"), "amount": r["amount"]} for r in daily]
            period_label = f"{this_month.label} to date"

        payload = {
            "category": category_name,
            "granularity": granularity,
            "period_label": period_label,
            "labels": [r["label"] for r in series],
            "values": [r["amount"] for r in series],
        }
        return JsonResponse(json.loads(to_json(payload)))


class SalesChannelTrendView(TenantLoginRequiredMixin, View):
    """JSON behind the click-through on the Revenue Insights table: one
    channel's revenue over time, for the bar-chart popup. Same granularity
    windows as ExpenseCategoryTrendView, mirrored for SalesEntry/channel."""

    def get(self, request, *args, **kwargs):
        channel_name = request.GET.get("channel", "")
        if not channel_name:
            return JsonResponse({"error": "channel is required"}, status=400)
        granularity = request.GET.get("granularity", "daily")

        if granularity == "weekly":
            series = services.sales_channel_weekly_trend(channel_name)
            period_label = "Last 12 weeks"
        elif granularity == "monthly":
            series = services.sales_channel_monthly_trend(channel_name)
            period_label = "Last 6 months"
        else:
            granularity = "daily"
            fs = services.get_finance_settings()
            this_month = resolve_period("this_month", fs.fy_start_month)
            today = datetime.date.today()
            end = min(this_month.end, today)
            daily = services.sales_channel_daily_trend(channel_name, this_month.start, end)
            series = [{"label": r["date"].strftime("%d %b"), "amount": r["amount"]} for r in daily]
            period_label = f"{this_month.label} to date"

        payload = {
            "channel": channel_name,
            "granularity": granularity,
            "period_label": period_label,
            "labels": [r["label"] for r in series],
            "values": [r["amount"] for r in series],
        }
        return JsonResponse(json.loads(to_json(payload)))


class PurchaseExpenseIntelligenceView(ManagerAccountRestrictedMixin, TenantLoginRequiredMixin, PeriodMixin, TemplateView):
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
        })
        return context


class DailySummaryRangeMixin:
    """Shared by the Daily Performance page and its exports: the ?from/&to range
    (default: the last 7 days), swapped if reversed and capped at MAX_DAYS."""

    MAX_DAYS = 366

    def _parse_date(self, key):
        try:
            return datetime.date.fromisoformat(self.request.GET.get(key, ""))
        except ValueError:
            return None

    def summary_range(self):
        date_to = self._parse_date("to") or datetime.date.today()
        date_from = self._parse_date("from") or date_to - datetime.timedelta(days=6)
        if date_from > date_to:
            date_from, date_to = date_to, date_from
        date_from = max(date_from, date_to - datetime.timedelta(days=self.MAX_DAYS - 1))
        return date_from, date_to


class DailySummaryView(DailySummaryRangeMixin, TenantLoginRequiredMixin, TemplateView):
    template_name = "finance/daily_summary.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        date_from, date_to = self.summary_range()
        context.update({
            "active_nav": "daily_summary",
            "organization": self.request.user.organization,
            "date_from": date_from,
            "date_to": date_to,
            "days": services.daily_series(date_from, date_to),
        })
        return context


class DailySummaryExcelView(DailySummaryRangeMixin, TenantLoginRequiredMixin, View):
    """One row per day (Date, Revenue, Expenses, Purchases, Net) plus a total row."""

    def get(self, request, *args, **kwargs):
        from io import BytesIO

        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Font

        date_from, date_to = self.summary_range()
        days = services.daily_series(date_from, date_to)

        wb = Workbook()
        ws = wb.active
        ws.title = "Daily Performance"
        ws.append(["Date", "Day", "Revenue", "Expenses", "Purchases", "Net"])
        for cell in ws[1]:
            cell.font = Font(bold=True)
            cell.alignment = Alignment(horizontal="center")
        for d in days:
            ws.append([d["date"], d["date"].strftime("%a"), d["sales"], d["expenses"], d["purchases"], d["net"]])
            ws.cell(row=ws.max_row, column=1).number_format = "dd mmm yyyy"
        ws.append([
            "Total", "",
            sum((d["sales"] for d in days), Decimal(0)), sum((d["expenses"] for d in days), Decimal(0)),
            sum((d["purchases"] for d in days), Decimal(0)), sum((d["net"] for d in days), Decimal(0)),
        ])
        for cell in ws[ws.max_row]:
            cell.font = Font(bold=True)
        for row in ws.iter_rows(min_row=2, min_col=3):
            for cell in row:
                cell.number_format = "#,##0.00"
        ws.freeze_panes = "A2"
        for col, width in zip("ABCDEF", (16, 8, 17, 17, 17, 17)):
            ws.column_dimensions[col].width = width

        buffer = BytesIO()
        wb.save(buffer)
        response = HttpResponse(
            buffer.getvalue(),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        response["Content-Disposition"] = f'attachment; filename="daily-performance-{date_from}-{date_to}.xlsx"'
        return response


class DailySummaryPdfView(DailySummaryRangeMixin, TenantLoginRequiredMixin, View):
    def get(self, request, *args, **kwargs):
        date_from, date_to = self.summary_range()
        days = services.daily_series(date_from, date_to)
        context = {
            "organization": request.user.organization,
            "date_from": date_from,
            "date_to": date_to,
            "days": days,
            "total_sales": sum((d["sales"] for d in days), Decimal(0)),
            "total_expenses": sum((d["expenses"] for d in days), Decimal(0)),
            "total_purchases": sum((d["purchases"] for d in days), Decimal(0)),
            "total_net": sum((d["net"] for d in days), Decimal(0)),
        }
        return _render_statement_pdf(
            request, "finance/pdf/daily_summary_pdf.html", context,
            f"daily-performance-{date_from}-{date_to}.pdf",
            fallback_url_name="finance:daily_summary",
        )


class DailyEntryDaysView(TenantLoginRequiredMixin, View):
    """JSON for the Daily Book's month-calendar pop-up: for ?month=YYYY-MM,
    how many revenue/expense/purchase entries were logged on each day."""

    def get(self, request, *args, **kwargs):
        from django.http import JsonResponse

        raw = request.GET.get("month", "")
        try:
            year, month = (int(part) for part in raw.split("-"))
            first = datetime.date(year, month, 1)
        except (ValueError, TypeError):
            first = datetime.date.today().replace(day=1)
        last = (first.replace(day=28) + datetime.timedelta(days=4))
        last = last.replace(day=1) - datetime.timedelta(days=1)

        counts = {}
        models = (SalesEntry, ExpenseEntry, PurchaseEntry)
        from apps.sheets_store.session import get_active_session

        session = get_active_session()
        if session is not None:
            # Google Sheets org: the ORM-style path would download every
            # column of all three tabs (3 slow reads). Only the date column
            # matters here, so fetch just that, for all three tabs, in one call.
            from openpyxl.utils import get_column_letter

            from apps.sheets_store import client as sheets_client
            from apps.sheets_store.store import _tab_name, sheet_fields

            ranges = []
            for model in models:
                names = [f.name for f in sheet_fields(model)]
                col = get_column_letter(names.index("date") + 1)
                ranges.append(f"{_tab_name(model)}!{col}2:{col}100000")
            try:
                columns = sheets_client.batch_get_values(
                    session.access_token, spreadsheet_id=session.spreadsheet_id, a1_ranges=ranges
                )
            except Exception:
                return JsonResponse({"error": "Couldn't read entries from Google Sheets."}, status=502)
            prefix = first.strftime("%Y-%m-")
            for column in columns:
                for cell in column:
                    value = str(cell[0])[:10] if cell else ""
                    if value.startswith(prefix):
                        counts[value] = counts.get(value, 0) + 1
        else:
            for model in models:
                rows = (
                    model.objects.filter(date__gte=first, date__lte=last)
                    .values("date").annotate(n=Count("id"))
                )
                for row in rows:
                    key = row["date"].isoformat()
                    counts[key] = counts.get(key, 0) + row["n"]
        return JsonResponse({"month": first.strftime("%Y-%m"), "days": counts})


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

        from apps.accounts.models import User

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
            "is_manager": self.request.user.role == User.Role.MANAGER,
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
        "bulk_prefixes_json": to_json([
            {"prefix": "sale", "main_field": "channel"},
            {"prefix": "expense", "main_field": "category"},
            {"prefix": "purchase", "main_field": "category"},
        ]),
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


def _daily_pdf_density(report) -> dict:
    """Pick how much detail and what type size lets the Daily Report PDF fit
    one A4 page. The page holds roughly 28 rows at 9px, 33 at 8px and 38 at
    7px (measured with the letterhead, opening line and closing block); a busy day drops the per-sub-category lines and keeps category
    totals only."""
    def rows(groups, detail):
        total = 0
        for g in groups:
            total += 1
            if detail and g["has_detail"] and len(g["subgroups"]) > 1:
                total += len(g["subgroups"])
        return total

    def tallest(detail):
        money_out = list(report["expenses_by_category"]) + list(report["purchases_by_category"])
        return max(rows(report["sales_by_category"], detail), rows(money_out, detail))

    # Room left under the table for transfers / net / closing balance.
    below = len(report["transfers"])
    detailed = tallest(True) + below
    for limit, px in ((28, 9), (33, 8), (38, 7)):
        if detailed <= limit:
            return {"pdf_detail": True, "font_px": px}
    return {"pdf_detail": False, "font_px": 8}


class DailyReportPdfView(TenantLoginRequiredMixin, View):
    def get(self, request, *args, **kwargs):
        raw = request.GET.get("date")
        try:
            selected_date = datetime.date.fromisoformat(raw) if raw else datetime.date.today()
        except ValueError:
            selected_date = datetime.date.today()

        report = services.daily_report(selected_date)
        context = {
            "organization": request.user.organization,
            "report": report,
            "show_bank": request.GET.get("show_bank") == "1",
            **_daily_pdf_density(report),
        }
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
        row = {
            "date": e.date,
            "label": label,
            "amount": e.amount,
            "payment_mode": e.get_payment_mode_display(),
            "note": e.note,
        }
        if kind == "sale":
            row["gross_amount"] = e.gross_amount
            row["discount"] = e.discount
        rows.append(row)
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


def _import_review_context(request, headers, data_rows, mapping) -> dict:
    """Build the review-screen context for a parsed import file: an
    editable SalesEntryFormSet pre-filled from `mapping`'s reading of
    `data_rows`, paired row-by-row with why each one might need a manual
    look before saving (see imports.build_initial_rows)."""
    min_date = _historical_min_date(request)
    initial_rows, warnings = imports.build_initial_rows(headers, data_rows, mapping)
    formset = sales_import_formset(len(initial_rows))(
        queryset=SalesEntry.objects.none(), initial=initial_rows, prefix="sale",
        form_kwargs={"min_date": min_date},
    )
    return {
        "active_nav": "daily_report",
        "organization": request.user.organization,
        "min_date": min_date,
        "sale_formset": formset,
        "rows": list(zip(formset, warnings)),
        "row_count": len(initial_rows),
        "flagged_count": sum(1 for w in warnings if w),
        "headers": headers,
        "mapping_fields": [
            (field, label, required, mapping.get(field))
            for field, label, required in imports.TARGET_FIELDS
        ],
        "raw_data_json": to_json({"headers": headers, "rows": data_rows}),
        "subcategory_map_json": to_json(services.subcategory_map()),
        "bulk_prefixes_json": to_json([{"prefix": "sale", "main_field": "channel"}]),
    }


class ImportSalesUploadView(TenantLoginRequiredMixin, View):
    """Upload → parse → map-and-review for Revenue entries. The uploaded
    file is only ever read into memory (imports.parse_upload) — never saved
    to disk or a session; re-mapping columns re-posts the already-parsed
    grid back to this same view via a hidden field instead of re-uploading."""

    def get(self, request, *args, **kwargs):
        return render(request, "finance/import_sales_upload.html", {
            "active_nav": "daily_report",
            "target_fields": imports.TARGET_FIELDS,
            "max_rows": imports.MAX_IMPORT_ROWS,
            "max_upload_mb": imports.MAX_UPLOAD_BYTES // (1024 * 1024),
        })

    def post(self, request, *args, **kwargs):
        raw_json = request.POST.get("raw_data")
        if raw_json:
            try:
                payload = json.loads(raw_json)
                headers, data_rows = payload["headers"], payload["rows"]
            except (ValueError, KeyError, TypeError):
                messages.error(request, "Something went wrong reading that data — please upload the file again.")
                return redirect("finance:import_sales")
            mapping = {
                field: request.POST.get(f"map_{field}") or None for field, _label, _required in imports.TARGET_FIELDS
            }
        else:
            uploaded = request.FILES.get("file")
            if not uploaded:
                messages.error(request, "Choose a CSV or Excel file first.")
                return self.get(request, *args, **kwargs)
            try:
                headers, data_rows = imports.parse_upload(uploaded)
            except imports.ImportParseError as exc:
                messages.error(request, str(exc))
                return self.get(request, *args, **kwargs)
            mapping = imports.guess_column_mapping(headers)

        context = _import_review_context(request, headers, data_rows, mapping)
        return render(request, "finance/import_sales_review.html", context)


class ImportSalesCommitView(TenantLoginRequiredMixin, View):
    """Saves the reviewed/edited import formset — the exact same save path
    (_save_bulk_formset) Bulk Entry itself uses, so historical-window limits,
    bank-account clearing and gross/discount handling all apply identically."""

    def post(self, request, *args, **kwargs):
        total = int(request.POST.get("sale-TOTAL_FORMS") or 0)
        formset = sales_import_formset(total)(
            request.POST, queryset=SalesEntry.objects.none(), prefix="sale",
            form_kwargs={"min_date": _historical_min_date(request)},
        )
        if formset.is_valid():
            count, saved = _save_bulk_formset(formset, request)
            if count:
                messages.success(request, f"Imported {count} revenue entr{'y' if count == 1 else 'ies'}.")
            else:
                messages.warning(request, "Nothing was saved — every row was empty or removed.")
            return redirect("finance:daily_bulk_entry")
        messages.error(request, "Couldn't save one or more rows — fix the highlighted errors and save again.")
        return render(request, "finance/import_sales_review.html", {
            "active_nav": "daily_report",
            "organization": request.user.organization,
            "sale_formset": formset,
            "rows": list(zip(formset, [[]] * total)),
            "row_count": total,
            "flagged_count": 0,
            "subcategory_map_json": to_json(services.subcategory_map()),
            "bulk_prefixes_json": to_json([{"prefix": "sale", "main_field": "channel"}]),
        })


class ImportSalesSampleView(TenantLoginRequiredMixin, View):
    """A ready-to-fill example file for the Import Revenue upload page —
    every header matches a mapping target field exactly, so a client can
    literally fill it in and upload it back unchanged."""

    def get(self, request, *args, **kwargs):
        buffer = io.StringIO()
        csv.writer(buffer).writerows(imports.SAMPLE_ROWS)
        response = HttpResponse(buffer.getvalue(), content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename="revenue_import_sample.csv"'
        return response


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


class ReportsView(ManagerAccountRestrictedMixin, TenantLoginRequiredMixin, PeriodMixin, TemplateView):
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


MONTHLY_SUMMARY_KINDS = {
    "sales": (SalesEntry, "channel", "Revenue"),
    "purchases": (PurchaseEntry, "category", "Purchases"),
    "expenses": (ExpenseEntry, "category", "Expenses"),
}

GRANULARITY_CHOICES = [
    ("daily", "Daily"),
    ("weekly", "Weekly"),
    ("monthly", "Monthly"),
    ("quarterly", "Quarterly"),
    ("yearly", "Yearly"),
]
GRANULARITY_KEYS = {key for key, _ in GRANULARITY_CHOICES}


def _monthly_summary_query(request):
    """Parses ?kind=&granularity=&period=&from=&to= the same way for the
    report page and its CSV/PDF exports, so the three never drift apart."""
    kind = request.GET.get("kind", "sales")
    if kind not in MONTHLY_SUMMARY_KINDS:
        kind = "sales"
    granularity = request.GET.get("granularity", "monthly")
    if granularity not in GRANULARITY_KEYS:
        granularity = "monthly"

    fs = services.get_finance_settings()
    period_key = request.GET.get("period", "this_fy")
    custom_from = request.GET.get("from")
    custom_to = request.GET.get("to")
    custom_from = datetime.date.fromisoformat(custom_from) if custom_from else None
    custom_to = datetime.date.fromisoformat(custom_to) if custom_to else None
    period = resolve_period(period_key, fs.fy_start_month, custom_from=custom_from, custom_to=custom_to)

    model, field, label = MONTHLY_SUMMARY_KINDS[kind]
    matrix = services.category_period_matrix(model, field, period.start, period.end, granularity)
    yoy = services.year_over_year_comparison(model, period.start, period.end)
    return {
        "kind": kind,
        "kind_label": label,
        "granularity": granularity,
        "period": period,
        "matrix": matrix,
        "yoy": yoy,
        # Whether subcategory rows should be expanded — carried as ?expand=1
        # from the report page's "Expand all" toggle into the Excel/PDF
        # export links, so what you see is what gets downloaded.
        "expand_all": request.GET.get("expand") == "1",
    }


class ReportLineDetailView(TenantLoginRequiredMixin, View):
    """JSON list of the individual entries behind one Financial Reports
    row — e.g. clicking "Salaries & Wages" or "Revenue" shows exactly
    which transactions add up to that figure, instead of just the total.

    `category` filters to one category/channel name ("__uncategorized__"
    for entries with none); `gst_rate` filters to one GST rate instead
    ("untaxed" for no category or a 0% rate). Omit both for the kind's
    grand total (e.g. P&L's Revenue / Cost of Goods rows)."""

    MAX_ENTRIES = 300

    def get(self, request, *args, **kwargs):
        kind = request.GET.get("kind", "sales")
        if kind not in MONTHLY_SUMMARY_KINDS:
            return JsonResponse({"error": "invalid kind"}, status=400)
        try:
            start = datetime.date.fromisoformat(request.GET.get("from", ""))
            end = datetime.date.fromisoformat(request.GET.get("to", ""))
        except ValueError:
            return JsonResponse({"error": "from and to are required"}, status=400)

        model, field, _ = MONTHLY_SUMMARY_KINDS[kind]
        qs = model.objects.filter(date__gte=start, date__lte=end)

        category = request.GET.get("category")
        if category == "__uncategorized__":
            qs = qs.filter(**{f"{field}__isnull": True})
        elif category:
            qs = qs.filter(**{f"{field}__name": category})

        gst_rate = request.GET.get("gst_rate")
        if gst_rate == "untaxed":
            qs = qs.filter(Q(**{f"{field}__isnull": True}) | Q(**{f"{field}__gst_rate": 0}))
        elif gst_rate == "taxed":
            qs = qs.filter(**{f"{field}__isnull": False, f"{field}__gst_rate__gt": 0})
        elif gst_rate:
            qs = qs.filter(**{f"{field}__gst_rate": gst_rate})

        qs = qs.select_related(field, "subcategory").order_by("-date", "-created_at")
        total_count = qs.count()
        total = qs.aggregate(t=Sum("amount"))["t"] or services.ZERO

        entries = []
        for e in qs[: self.MAX_ENTRIES]:
            top = getattr(e, field)
            entries.append({
                "date": e.date.isoformat(),
                "name": e.subcategory.name if e.subcategory_id else (top.name if top else "Uncategorized"),
                "note": e.note,
                "amount": e.amount,
            })

        payload = {
            "total": total,
            "count": total_count,
            "truncated": total_count > self.MAX_ENTRIES,
            "entries": entries,
        }
        return JsonResponse(json.loads(to_json(payload)))


class MonthlySummaryView(TenantLoginRequiredMixin, TemplateView):
    """Spreadsheet-style report: every category down the side, one column
    per day/week/month/quarter/year of the chosen period across the top —
    one tab each for Sales/Purchases/Expenses."""

    template_name = "finance/monthly_summary.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(_monthly_summary_query(self.request))
        context.update({
            "active_nav": "monthly_summary",
            "organization": self.request.user.organization,
            "granularity_choices": GRANULARITY_CHOICES,
            "period_choices": PERIOD_CHOICES,
        })
        return context


class MonthlySummaryExcelView(TenantLoginRequiredMixin, View):
    """The Monthly Summary grid (plain amounts — not the Δ%/share views)
    as a formatted .xlsx workbook: bold headers/totals, a currency number
    format, and the category column + header row frozen for scrolling."""

    def get(self, request, *args, **kwargs):
        from io import BytesIO

        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Font
        from openpyxl.utils import get_column_letter

        data = _monthly_summary_query(request)
        matrix, kind, kind_label, period = data["matrix"], data["kind"], data["kind_label"], data["period"]
        expand_all = data["expand_all"]

        wb = Workbook()
        ws = wb.active
        ws.title = kind_label[:31]

        header = [kind_label] + matrix["labels"] + ["Total"]
        ws.append(header)
        for cell in ws[1]:
            cell.font = Font(bold=True)
            cell.alignment = Alignment(horizontal="center")

        subrow_font = Font(italic=True, color="666666")
        for row in matrix["rows"]:
            ws.append([row["name"]] + [cell["value"] for cell in row["cells"]] + [row["total"]])
            if expand_all and row["has_subrows"]:
                for subrow in row["subrows"]:
                    ws.append([subrow["name"]] + [cell["value"] for cell in subrow["cells"]] + [subrow["total"]])
                    name_cell = ws.cell(row=ws.max_row, column=1)
                    name_cell.alignment = Alignment(indent=1)
                    for cell in ws[ws.max_row]:
                        cell.font = subrow_font

        ws.append(["Total"] + [cell["value"] for cell in matrix["column_cells"]] + [matrix["grand_total"]])
        for cell in ws[ws.max_row]:
            cell.font = Font(bold=True)

        for row in ws.iter_rows(min_row=2, min_col=2):
            for cell in row:
                cell.number_format = "#,##0.00"

        ws.freeze_panes = "B2"
        ws.column_dimensions["A"].width = 28
        # 14 was too tight for larger organizations — a long grand total
        # like "15,732,160.20" (13 characters) only just fits a width-14
        # column with no padding margin, so Excel renders it as "###"
        # until the user manually widens it. 17 leaves real headroom.
        for i in range(2, len(header) + 1):
            ws.column_dimensions[get_column_letter(i)].width = 17

        buffer = BytesIO()
        wb.save(buffer)
        filename = f"monthly-summary-{kind}-{period.start}-{period.end}.xlsx"
        response = HttpResponse(
            buffer.getvalue(),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response


class MonthlySummaryPdfView(TenantLoginRequiredMixin, View):
    """The Monthly Summary grid (plain amounts) as a landscape PDF, mirroring
    the P&L/Balance Sheet/Cash Flow export pattern on the Reports page."""

    def get(self, request, *args, **kwargs):
        data = _monthly_summary_query(request)
        period = data["period"]
        n_cols = len(data["matrix"]["labels"])
        # xhtml2pdf's table layout doesn't reliably shrink-to-fit the way a
        # browser does — with many periods and no explicit column widths it
        # lets each number overflow its column instead of wrapping, so
        # adjacent columns visually overlap. Pin every column to an explicit
        # percentage (name/total columns get a fixed share, periods split
        # the rest evenly) and scale the font down as columns multiply, so
        # a cramped column wraps onto a second line instead of overlapping.
        if n_cols <= 8:
            font_px = 9
        elif n_cols <= 14:
            font_px = 7
        elif n_cols <= 20:
            font_px = 6
        else:
            font_px = 5
        # The name/total columns need less room than their 14%/7% default
        # once there are many period columns competing for the same page
        # width — give most of that back to the data columns so each one
        # has enough space for a full amount without overflowing.
        if n_cols <= 8:
            name_col_pct, total_col_pct = 14, 7
        elif n_cols <= 20:
            name_col_pct, total_col_pct = 12, 6
        else:
            name_col_pct, total_col_pct = 10, 5
        data_col_pct = round(max(100 - name_col_pct - total_col_pct, 0) / max(n_cols, 1), 2)
        context = {
            "organization": request.user.organization,
            "kind_label": data["kind_label"],
            "period": period,
            "matrix": data["matrix"],
            "yoy": data["yoy"],
            "expand_all": data["expand_all"],
            "font_px": font_px,
            "name_col_pct": name_col_pct,
            "total_col_pct": total_col_pct,
            "data_col_pct": data_col_pct,
        }
        filename = f"monthly-summary-{data['kind']}-{period.start}-{period.end}.pdf"
        return _render_statement_pdf(
            request, "finance/pdf/monthly_summary_pdf.html", context, filename,
            fallback_url_name="finance:monthly_summary",
        )


def _render_statement_pdf(request, template_name, context, filename, fallback_url_name="finance:reports"):
    try:
        from xhtml2pdf import pisa
    except ImportError:
        messages.error(request, "PDF export isn't available on this server.")
        return redirect(fallback_url_name)

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


CATEGORY_KIND_FOR_REPORT_KIND = {
    "sales": Category.Kind.SALES,
    "expenses": Category.Kind.EXPENSE,
    "purchases": Category.Kind.PURCHASE,
}


def _category_statement_query(request) -> dict:
    """Parses ?kind=&category=&subcategory=&from=&to= the same way for the
    Category Statement page and its Excel/CSV/PDF exports, so the three
    never drift apart (mirrors _monthly_summary_query's pattern). Date
    range defaults to this calendar month, same convention as
    _ledger_filter_context — an explicit ?from=&to= (emptied) means "all
    time" instead."""
    kind = request.GET.get("kind", "sales")
    if kind not in MONTHLY_SUMMARY_KINDS:
        kind = "sales"
    model, field, label = MONTHLY_SUMMARY_KINDS[kind]

    category_id = request.GET.get("category") or None
    subcategory_id = request.GET.get("subcategory") or None
    category = Category.objects.filter(pk=category_id).first() if category_id else None
    subcategory = Subcategory.objects.filter(pk=subcategory_id).first() if subcategory_id else None

    default_range = "from" not in request.GET and "to" not in request.GET
    if default_range:
        today = datetime.date.today()
        date_from = today.replace(day=1)
        date_to = today.replace(day=calendar.monthrange(today.year, today.month)[1])
    else:
        def _parse(name):
            raw = request.GET.get(name)
            try:
                return datetime.date.fromisoformat(raw) if raw else None
            except ValueError:
                return None
        date_from, date_to = _parse("from"), _parse("to")
        if date_from and date_to and date_from > date_to:
            date_from, date_to = date_to, date_from

    result = (
        services.category_statement_entries(model, field, category_id, subcategory_id, date_from, date_to)
        if category_id else {"entries": [], "totals": {"gross": 0, "discount": 0, "amount": 0, "quantity": 0, "count": 0}}
    )

    return {
        "kind": kind,
        "kind_label": label,
        "model": model,
        "categories": Category.objects.filter(kind=CATEGORY_KIND_FOR_REPORT_KIND[kind], is_active=True).order_by("name"),
        "category": category,
        "subcategory": subcategory,
        "category_id": category_id or "",
        "subcategory_id": subcategory_id or "",
        "date_from": date_from,
        "date_to": date_to,
        "default_range": default_range,
        "entries": result["entries"],
        "totals": result["totals"],
    }


class CategoryStatementView(TenantLoginRequiredMixin, View):
    """A statement for one Category — optionally narrowed to a Sub-category
    or a specific Item — over a date range: total(s) at the top, then every
    entry behind that total, with an Excel/CSV/PDF export of the same."""

    def get(self, request, *args, **kwargs):
        data = _category_statement_query(request)
        page = _paginate(request, data["entries"])
        context = {
            "active_nav": "category_statement",
            "organization": request.user.organization,
            "subcategory_map_json": to_json(services.subcategory_map()),
            **data,
        }
        context["entries"] = page["page_obj"].object_list
        context.update(page)
        return render(request, "finance/category_statement.html", context)


class CategoryStatementExcelView(TenantLoginRequiredMixin, View):
    def get(self, request, *args, **kwargs):
        from io import BytesIO

        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Font
        from openpyxl.utils import get_column_letter

        data = _category_statement_query(request)
        is_sales = data["kind"] == "sales"
        org = request.user.organization
        column_count = 11 if is_sales else 9
        last_col_letter = get_column_letter(column_count)

        wb = Workbook()
        ws = wb.active
        ws.title = data["kind_label"][:31]

        # Letterhead — organization, report name/filters, date range — so an
        # exported file still identifies itself once it's out of context.
        report_title = f"Category Statement · {data['kind_label']}"
        if data["category"]:
            report_title += f" · {data['category'].name}"
        if data["subcategory"]:
            report_title += f" · {data['subcategory'].name}"
        if data["date_from"] and data["date_to"]:
            date_range = f"{data['date_from']:%d %b %Y} – {data['date_to']:%d %b %Y}"
        elif data["date_from"]:
            date_range = f"From {data['date_from']:%d %b %Y}"
        elif data["date_to"]:
            date_range = f"Until {data['date_to']:%d %b %Y}"
        else:
            date_range = "All time"

        def _letterhead_row(text, font, alignment=Alignment(horizontal="center")):
            ws.append([text])
            row = ws.max_row
            ws.merge_cells(f"A{row}:{last_col_letter}{row}")
            cell = ws.cell(row=row, column=1)
            cell.font = font
            cell.alignment = alignment

        _letterhead_row(org.name, Font(bold=True, size=14))
        _letterhead_row(report_title, Font(bold=True, size=11))
        _letterhead_row(date_range, Font(italic=True, size=9, color="666666"))
        ws.append([])

        header = ["Date", "Sub-category", "Item", "Customer/Vendor", "Qty"]
        if is_sales:
            header += ["Gross", "Discount"]
        header += ["Net" if is_sales else "Amount", "Via", "Bank", "Note"]
        ws.append(header)
        header_row = ws.max_row
        for cell in ws[header_row]:
            cell.font = Font(bold=True)
            cell.alignment = Alignment(horizontal="center")
        for e in data["entries"]:
            row = [e["date"], e["subcategory"], e["item"], e["customer"] or e["vendor"], e["quantity"]]
            if is_sales:
                row += [e["gross_amount"], e["discount"]]
            row += [e["amount"], e["payment_mode"], e["bank_account"], e["note"]]
            ws.append(row)
        total_row = ["Total", "", "", "", data["totals"]["quantity"]]
        if is_sales:
            total_row += [data["totals"]["gross"], data["totals"]["discount"]]
        total_row += [data["totals"]["amount"], "", "", ""]
        ws.append(total_row)
        for cell in ws[ws.max_row]:
            cell.font = Font(bold=True)
        amount_min_col, amount_max_col = (6, 8) if is_sales else (6, 6)
        for row in ws.iter_rows(min_row=header_row + 1, min_col=amount_min_col, max_col=amount_max_col):
            for cell in row:
                cell.number_format = "#,##0.00"
        widths = [12, 16, 16, 18, 6] + ([10, 10] if is_sales else []) + [10, 8, 14, 24]
        for i, width in enumerate(widths, start=1):
            ws.column_dimensions[get_column_letter(i)].width = width
        ws.freeze_panes = f"A{header_row + 1}"

        buffer = BytesIO()
        wb.save(buffer)
        filename = f"category-statement-{data['kind']}-{data['date_from']}-{data['date_to']}.xlsx"
        response = HttpResponse(
            buffer.getvalue(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response


class CategoryStatementCsvView(TenantLoginRequiredMixin, View):
    def get(self, request, *args, **kwargs):
        data = _category_statement_query(request)
        is_sales = data["kind"] == "sales"
        buffer = io.StringIO()
        writer = csv.writer(buffer)

        header = ["Date", "Sub-category", "Item", "Customer/Vendor", "Qty"]
        if is_sales:
            header += ["Gross", "Discount"]
        header += ["Net" if is_sales else "Amount", "Via", "Bank", "Note"]
        writer.writerow(header)

        for e in data["entries"]:
            row = [e["date"], e["subcategory"], e["item"], e["customer"] or e["vendor"], e["quantity"] or ""]
            if is_sales:
                row += [e["gross_amount"] or "", e["discount"] or ""]
            row += [e["amount"], e["payment_mode"], e["bank_account"], e["note"]]
            writer.writerow(row)

        total_row = ["Total", "", "", "", data["totals"]["quantity"]]
        if is_sales:
            total_row += [data["totals"]["gross"], data["totals"]["discount"]]
        total_row += [data["totals"]["amount"], "", "", ""]
        writer.writerow(total_row)

        filename = f"category-statement-{data['kind']}-{data['date_from']}-{data['date_to']}.csv"
        response = HttpResponse(buffer.getvalue(), content_type="text/csv")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response


class CategoryStatementPdfView(TenantLoginRequiredMixin, View):
    def get(self, request, *args, **kwargs):
        data = _category_statement_query(request)
        context = {"organization": request.user.organization, **data}
        filename = f"category-statement-{data['kind']}-{data['date_from']}-{data['date_to']}.pdf"
        return _render_statement_pdf(
            request, "finance/pdf/category_statement_pdf.html", context, filename,
            fallback_url_name="finance:category_statement",
        )


MONTH_NAMES = {
    1: "January", 2: "February", 3: "March", 4: "April", 5: "May", 6: "June",
    7: "July", 8: "August", 9: "September", 10: "October", 11: "November", 12: "December",
}


class FinanceSettingsView(ManagerAccountRestrictedMixin, TenantLoginRequiredMixin, TemplateView):
    template_name = "finance/settings.html"

    def get_context_data(self, **kwargs):
        from apps.accounts.models import User
        from apps.organizations import google_drive_client as drive

        context = super().get_context_data(**kwargs)
        fs = services.get_finance_settings()
        categories = list(Category.objects.prefetch_related("subcategories").all())
        user = self.request.user
        context["active_nav"] = "settings"
        context["organization"] = user.organization
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
        context["bank_accounts"] = BankAccount.objects.all()
        can_edit_org = bool(user.organization_id) and user.role in (User.Role.OWNER, User.Role.ADMIN)
        context["can_connect_drive"] = can_edit_org and drive.is_configured()
        context["drive_connection"] = getattr(user.organization, "cloud_backup", None) if can_edit_org else None
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
        subcategories = Subcategory.objects.filter(
            category=category, parent__isnull=True
        ).prefetch_related("children")
        tab = next(t for t in CATEGORY_TABS if t[0] == category.kind)
        context = {
            "active_nav": "categories",
            "organization": request.user.organization,
            "category": category,
            "subcategories": subcategories,
            "sub_placeholder": tab[3],
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


PAGE_SIZES = (20, 50, 100)
DEFAULT_PAGE_SIZE = 50


def _page_size(request) -> int:
    try:
        size = int(request.GET.get("per_page", DEFAULT_PAGE_SIZE))
    except ValueError:
        size = DEFAULT_PAGE_SIZE
    return size if size in PAGE_SIZES else DEFAULT_PAGE_SIZE


def _paginate(request, items, param="page", newest_last=False):
    """Slice `items` to one page of 20 / 50 / 100 (?per_page=). With
    `newest_last` (running-balance ledgers, oldest first) the page that opens
    by default is the last one, so the most recent entries show first."""
    per_page = _page_size(request)
    paginator = Paginator(items, per_page)
    raw = request.GET.get(param)
    if raw is None and newest_last:
        number = paginator.num_pages
    else:
        number = raw
    page = paginator.get_page(number)
    numbers = [n if isinstance(n, int) else "…" for n in paginator.get_elided_page_range(page.number, on_each_side=1, on_ends=1)]
    params = request.GET.copy()
    params.pop(param, None)
    return {
        "page_obj": page,
        "page_numbers": numbers,
        "page_query": params.urlencode(),
        "per_page": per_page,
        "page_sizes": PAGE_SIZES,
    }


def _ledger_filter_context(request, entries, paginate=True):
    """Read ?from=&to=&q= and narrow `entries` with services.filter_ledger.
    Returns the filtered ledger plus the values the filter form needs.
    `paginate=False` (for exports, which need every matched row rather than
    one page of them) skips the pagination step entirely."""
    def parse(name):
        raw = request.GET.get(name)
        try:
            return datetime.date.fromisoformat(raw) if raw else None
        except ValueError:
            return None

    # No date parameters at all -> this month. Submitting the form with the dates
    # emptied (?from=&to=) is how a user asks for the whole history.
    default_range = "from" not in request.GET and "to" not in request.GET
    if default_range:
        today = datetime.date.today()
        date_from = today.replace(day=1)
        date_to = today.replace(day=calendar.monthrange(today.year, today.month)[1])
    else:
        date_from, date_to = parse("from"), parse("to")
        if date_from and date_to and date_from > date_to:
            date_from, date_to = date_to, date_from
    query = (request.GET.get("q") or "").strip()
    result = services.filter_ledger(entries, date_from, date_to, query)
    if paginate:
        page = _paginate(request, result["entries"])
        result["entries"] = list(page["page_obj"].object_list)
        result.update(page)
    result.update({
        "date_from": date_from,
        "date_to": date_to,
        "query": query,
        "is_filtered": bool(date_from or date_to or query),
        "default_range": default_range,
    })
    return result


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

        try:
            as_of = datetime.date.fromisoformat(self.request.GET.get("as_of", ""))
        except ValueError:
            as_of = None
        query = (self.request.GET.get("q") or "").strip()
        needle = query.lower()

        # Businesses that only make general counter sales keep no customer list, so the
        # Customer Ledgers block is left out for them entirely.
        has_customers = Customer.objects.exists()
        customer_outstanding = services.customer_outstanding_map(as_of)
        customers = list(Customer.objects.all())
        for customer in customers:
            customer.outstanding = customer_outstanding.get(customer.id, services.ZERO)
        vendor_outstanding = services.vendor_outstanding_map(as_of)
        vendors = list(Vendor.objects.all())
        for vendor in vendors:
            vendor.outstanding = vendor_outstanding.get(vendor.name, services.ZERO)

        if needle:
            customers = [
                c for c in customers
                if needle in " ".join([c.name, c.phone or "", c.email or ""]).lower()
            ]
            vendors = [v for v in vendors if needle in " ".join([v.name, v.phone or "", v.details or ""]).lower()]

        customer_page = _paginate(self.request, customers, param="cpage")
        vendor_page = _paginate(self.request, vendors, param="vpage")
        context.update({
            "active_nav": "ledgers",
            "organization": self.request.user.organization,
            "cash_balance": cash,
            "bank_balance": bank,
            "bank_account_rows": services.bank_account_rows(today),
            "customers": list(customer_page["page_obj"].object_list),
            "vendors": list(vendor_page["page_obj"].object_list),
            "has_customers": has_customers,
            "customer_pager": customer_page,
            "vendor_pager": vendor_page,
            "per_page": customer_page["per_page"],
            "page_sizes": PAGE_SIZES,
            "query": query,
            "as_of": as_of,
        })
        return context


class CustomerLedgerView(TenantLoginRequiredMixin, TemplateView):
    """One customer's individual ledger: every invoice (debit) and payment
    (credit) in date order with a running balance — classic ledger format."""

    template_name = "finance/customer_ledger.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        customer = get_object_or_404(Customer, pk=kwargs["pk"])
        flt = _ledger_filter_context(self.request, services.customer_ledger_entries(customer))
        entries = flt["entries"]
        invoices = {r.pk: r for r in Receivable.objects.filter(customer=customer)}
        for row in entries:
            if row["source"]["role"] == "invoice":
                row["edit_form"] = ReceivableEditForm(
                    instance=invoices[row["source"]["pk"]], auto_id=f"id_edit_inv_{row['source']['pk']}_%s"
                )
        context.update({
            "active_nav": "ledgers",
            "organization": self.request.user.organization,
            "customer": customer,
            **flt,
        })
        return context


class VendorLedgerView(TenantLoginRequiredMixin, TemplateView):
    """One vendor's individual ledger: every bill (debit) and payment
    (credit) in date order with a running balance."""

    template_name = "finance/vendor_ledger.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        vendor = get_object_or_404(Vendor, pk=kwargs["pk"])
        flt = _ledger_filter_context(self.request, services.vendor_ledger_entries(vendor.name))
        entries = flt["entries"]
        bills = {p.pk: p for p in Payable.objects.filter(vendor=vendor.name)}
        for row in entries:
            if row["source"]["role"] == "invoice":
                row["edit_form"] = PayableEditForm(
                    instance=bills[row["source"]["pk"]], auto_id=f"id_edit_bill_{row['source']['pk']}_%s"
                )
        context.update({
            "active_nav": "ledgers",
            "organization": self.request.user.organization,
            "vendor": vendor,
            **flt,
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
        flt = _ledger_filter_context(self.request, services.account_ledger_entries(self.account))
        context.update({
            "active_nav": "ledgers",
            "organization": self.request.user.organization,
            "account_label": self.account_label,
            **flt,
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


class BankAccountsView(TenantLoginRequiredMixin, TemplateView):
    """Full bank account directory: which bank, opening balance, and the
    current computed closing balance — with edit/delete/deactivate."""

    template_name = "finance/bank_accounts.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update({
            "active_nav": "bank_accounts",
            "organization": self.request.user.organization,
            "bank_account_rows": services.bank_account_rows(),
            "bank_account_form": BankAccountForm(auto_id="id_bank_account_%s"),
        })
        return context


class AddBankAccountView(TenantLoginRequiredMixin, View):
    def post(self, request, *args, **kwargs):
        form = BankAccountForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "Bank account added.")
        else:
            messages.error(request, "Couldn't add that bank account — the name may already exist.")
        return redirect("finance:bank_accounts")


class EditBankAccountView(TenantLoginRequiredMixin, TemplateView):
    template_name = "finance/edit_bank_account.html"

    def get(self, request, pk, *args, **kwargs):
        account = get_object_or_404(BankAccount, pk=pk)
        form = BankAccountEditForm(instance=account)
        return self.render(request, account, form)

    def post(self, request, pk, *args, **kwargs):
        account = get_object_or_404(BankAccount, pk=pk)
        form = BankAccountEditForm(request.POST, instance=account)
        if not form.is_valid():
            return self.render(request, account, form)
        form.save()
        messages.success(request, "Bank account updated.")
        return redirect("finance:bank_accounts")

    def render(self, request, account, form):
        context = {
            "active_nav": "bank_accounts",
            "organization": request.user.organization,
            "bank_account": account,
            "form": form,
        }
        return self.render_to_response(context)


class DeleteBankAccountView(TenantLoginRequiredMixin, View):
    def post(self, request, pk, *args, **kwargs):
        account = get_object_or_404(BankAccount, pk=pk)
        account.delete()
        messages.success(request, "Bank account deleted.")
        return redirect("finance:bank_accounts")


class ToggleBankAccountView(TenantLoginRequiredMixin, View):
    def post(self, request, pk, *args, **kwargs):
        account = get_object_or_404(BankAccount, pk=pk)
        account.is_active = not account.is_active
        account.save(update_fields=["is_active"])
        return redirect("finance:bank_accounts")


class BankAccountLedgerView(TenantLoginRequiredMixin, TemplateView):
    """One bank account's own running-balance ledger — mirrors
    VendorLedgerView/AccountLedgerView, scoped to a specific BankAccount."""

    template_name = "finance/bank_account_ledger.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        account = get_object_or_404(BankAccount, pk=kwargs["pk"])
        flt = _ledger_filter_context(self.request, services.bank_account_ledger_entries(account))
        context.update({
            "active_nav": "bank_accounts",
            "organization": self.request.user.organization,
            "bank_account": account,
            **flt,
        })
        return context


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
            messages.success(request, f"Logged {receivable.amount} to receive from {receivable.customer}.")
        else:
            messages.error(request, "Couldn't save that money to receive: " + "; ".join(
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
            messages.success(request, "Money-to-receive entry deleted.")
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
                    bank_account=form.cleaned_data["bank_account"],
                    note=form.cleaned_data["note"] or f"Payment received for invoice — {receivable.customer}",
                    created_by_email=request.user.email,
                )
                receivable.amount_received += amount
                receivable.save(update_fields=["amount_received"])
                messages.success(request, f"Recorded {amount} received against this.")
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
            messages.success(request, f"Logged {payable.amount} to pay to {payable.vendor}.")
        else:
            messages.error(request, "Couldn't save that money to pay: " + "; ".join(
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
            messages.success(request, "Money-to-pay entry deleted.")
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
                    bank_account=form.cleaned_data["bank_account"],
                    note=form.cleaned_data["note"] or f"Payment to {payable.vendor} for bill",
                    created_by_email=request.user.email,
                )
                payable.amount_paid += amount
                payable.save(update_fields=["amount_paid"])
                messages.success(request, f"Recorded {amount} paid against this.")
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
        flt = _ledger_filter_context(self.request, services.partner_ledger_entries(partner))
        entries = flt["entries"]
        min_date = _historical_min_date(self.request)
        txns = {t.pk: t for t in partner.transactions.all()}
        for row in entries:
            txn = txns.get(row["source"]["pk"])
            row["edit_form"] = PartnerTransactionForm(
                instance=txn, auto_id=f"id_edit_ptxn_{txn.pk}_%s", min_date=min_date
            )
            row["txn"] = txn
        context.update({
            "active_nav": "cash_position",
            "organization": self.request.user.organization,
            "partner": partner,
            **flt,
            "total_invested": flt["total_debit"],
            "total_withdrawn": flt["total_credit"],
            "net_capital": flt["closing_balance"],
        })
        return context


def _partner_ledger_export_data(request, pk):
    """Same filtering as PartnerLedgerView, but every matched row (no
    pagination) — shared by the Excel/CSV/PDF exports below."""
    partner = get_object_or_404(Partner, pk=pk)
    flt = _ledger_filter_context(request, services.partner_ledger_entries(partner), paginate=False)
    if flt["date_from"] and flt["date_to"]:
        date_range = f"{flt['date_from']:%d %b %Y} – {flt['date_to']:%d %b %Y}"
    elif flt["date_from"]:
        date_range = f"From {flt['date_from']:%d %b %Y}"
    elif flt["date_to"]:
        date_range = f"Until {flt['date_to']:%d %b %Y}"
    else:
        date_range = "All time"
    return {
        "organization": request.user.organization,
        "partner": partner,
        "date_range": date_range,
        **flt,
    }


class PartnerLedgerExcelView(TenantLoginRequiredMixin, View):
    def get(self, request, *args, **kwargs):
        from io import BytesIO

        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Font
        from openpyxl.utils import get_column_letter

        data = _partner_ledger_export_data(request, kwargs["pk"])
        org = data["organization"]

        wb = Workbook()
        ws = wb.active
        ws.title = "Partner Ledger"

        def _letterhead_row(text, font, alignment=Alignment(horizontal="center")):
            ws.append([text])
            row = ws.max_row
            ws.merge_cells(f"A{row}:E{row}")
            cell = ws.cell(row=row, column=1)
            cell.font = font
            cell.alignment = alignment

        _letterhead_row(org.name, Font(bold=True, size=14))
        _letterhead_row(f"{data['partner'].name} · Partner Ledger", Font(bold=True, size=11))
        _letterhead_row(data["date_range"], Font(italic=True, size=9, color="666666"))
        ws.append([])

        header = ["Date", "Particular", "Invested", "Withdrawn", "Net Capital"]
        ws.append(header)
        header_row = ws.max_row
        for cell in ws[header_row]:
            cell.font = Font(bold=True)
            cell.alignment = Alignment(horizontal="center")

        if data["opening_bf"] is not None:
            ws.append([data["date_from"], "Balance brought forward", None, None, data["opening_bf"]])
            for cell in ws[ws.max_row]:
                cell.font = Font(italic=True)

        for e in data["entries"]:
            ws.append([e["date"], e["particular"], e["debit"] or None, e["credit"] or None, e["balance"]])

        ws.append(["Total", "", data["total_debit"], data["total_credit"], data["closing_balance"]])
        for cell in ws[ws.max_row]:
            cell.font = Font(bold=True)

        for row in ws.iter_rows(min_row=header_row + 1, min_col=3, max_col=5):
            for cell in row:
                cell.number_format = "#,##0.00"
        widths = [12, 32, 14, 14, 14]
        for i, width in enumerate(widths, start=1):
            ws.column_dimensions[get_column_letter(i)].width = width
        ws.freeze_panes = f"A{header_row + 1}"

        buffer = BytesIO()
        wb.save(buffer)
        filename = f"partner-ledger-{data['partner'].name}-{data['date_from']}-{data['date_to']}.xlsx"
        response = HttpResponse(
            buffer.getvalue(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response


class PartnerLedgerCsvView(TenantLoginRequiredMixin, View):
    def get(self, request, *args, **kwargs):
        data = _partner_ledger_export_data(request, kwargs["pk"])
        buffer = io.StringIO()
        writer = csv.writer(buffer)

        writer.writerow(["Date", "Particular", "Invested", "Withdrawn", "Net Capital"])
        if data["opening_bf"] is not None:
            writer.writerow([data["date_from"], "Balance brought forward", "", "", data["opening_bf"]])
        for e in data["entries"]:
            writer.writerow([e["date"], e["particular"], e["debit"] or "", e["credit"] or "", e["balance"]])
        writer.writerow(["Total", "", data["total_debit"], data["total_credit"], data["closing_balance"]])

        filename = f"partner-ledger-{data['partner'].name}-{data['date_from']}-{data['date_to']}.csv"
        response = HttpResponse(buffer.getvalue(), content_type="text/csv")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response


class PartnerLedgerPdfView(TenantLoginRequiredMixin, View):
    def get(self, request, *args, **kwargs):
        data = _partner_ledger_export_data(request, kwargs["pk"])
        filename = f"partner-ledger-{data['partner'].name}-{data['date_from']}-{data['date_to']}.pdf"
        return _render_statement_pdf(
            request, "finance/pdf/partner_ledger_pdf.html", data, filename,
            fallback_url_name="finance:partner_ledger",
        )

