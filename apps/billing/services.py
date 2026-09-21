import calendar
import datetime
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from . import invoicing, razorpay_client
from .models import Coupon, CouponRedemption, Invoice, Payment, Plan, Subscription


def get_current_subscription(organization_id, using: str = "default"):
    return (
        Subscription.objects.using(using)
        .filter(organization_id=organization_id, is_current=True)
        .select_related("plan")
        .first()
    )


def _supersede_current(organization_id, using: str) -> None:
    """Mark whatever subscription is currently `is_current=True` for this
    org as no longer current, WITHOUT touching its other fields — it stays
    exactly as it was, forming the history trail."""
    Subscription.objects.using(using).filter(organization_id=organization_id, is_current=True).update(
        is_current=False
    )


@transaction.atomic
def start_trial(organization, plan: Plan, *, using: str = "default", start_date=None) -> Subscription:
    """Bootstrap subscription for a brand-new organization."""
    start_date = start_date or timezone.localdate()
    trial_end = start_date + datetime.timedelta(days=plan.trial_days) if plan.trial_days else None

    _supersede_current(organization.id, using)
    sub = Subscription(
        organization=organization,
        plan=plan,
        start_date=start_date,
        trial_start_date=start_date if plan.trial_days else None,
        trial_end_date=trial_end,
        end_date=trial_end,
        price=Decimal("0.00"),
        discount=Decimal("0.00"),
        tax=Decimal("0.00"),
        billing_cycle=Subscription.BillingCycle.MONTHLY,
        payment_status=Subscription.PaymentStatus.NOT_APPLICABLE,
        status=Subscription.Status.TRIAL if plan.trial_days else Subscription.Status.ACTIVE,
        is_current=True,
    )
    sub.save(using=using)
    return sub


@transaction.atomic
def create_subscription(
    organization,
    plan: Plan,
    *,
    using: str = "default",
    billing_cycle: str = Subscription.BillingCycle.MONTHLY,
    start_date=None,
    end_date=None,
    price=None,
    discount=Decimal("0.00"),
    tax=Decimal("0.00"),
    status: str = Subscription.Status.ACTIVE,
    payment_status: str = Subscription.PaymentStatus.PENDING,
    auto_renewal: bool = True,
    notes: str = "",
) -> Subscription:
    """Supersede whatever subscription is current for this org with a new
    one — this is how a plan change, renewal, or manual billing update is
    recorded. The old row is kept as-is (history), never edited or deleted.
    """
    start_date = start_date or timezone.localdate()
    if price is None:
        price = plan.yearly_price if billing_cycle == Subscription.BillingCycle.YEARLY else plan.monthly_price
    if end_date is None and billing_cycle != Subscription.BillingCycle.CUSTOM:
        days = 365 if billing_cycle == Subscription.BillingCycle.YEARLY else 30
        end_date = start_date + datetime.timedelta(days=days)

    _supersede_current(organization.id, using)
    sub = Subscription(
        organization=organization,
        plan=plan,
        start_date=start_date,
        end_date=end_date,
        price=price,
        discount=discount,
        tax=tax,
        billing_cycle=billing_cycle,
        status=status,
        payment_status=payment_status,
        auto_renewal=auto_renewal,
        notes=notes,
        is_current=True,
    )
    sub.save(using=using)
    return sub


@transaction.atomic
def extend_trial(subscription: Subscription, *, new_trial_end_date, using: str = "default") -> Subscription:
    subscription.trial_end_date = new_trial_end_date
    if not subscription.end_date or subscription.end_date < new_trial_end_date:
        subscription.end_date = new_trial_end_date
    if subscription.status not in (Subscription.Status.ACTIVE,):
        subscription.status = Subscription.Status.TRIAL
    subscription.save(using=using)
    return subscription


@transaction.atomic
def grant_complimentary(
    organization,
    *,
    using: str = "default",
    start_date,
    end_date,
    plan: Plan | None = None,
    notes: str = "",
) -> Subscription:
    """Free/complimentary access for an arbitrary date range (1 month, 1
    year, or any custom range) — e.g. a goodwill extension or promo. Creates
    a new current subscription with zero price; the plan it's tagged with
    is cosmetic (which features/limits show as "granted") since nothing is
    actually charged.
    """
    if plan is None:
        current = get_current_subscription(organization.id, using=using)
        plan = current.plan if current else Plan.objects.using(using).filter(is_active=True).first()
    if plan is None:
        raise ValueError("No plan available to attach the complimentary grant to.")

    _supersede_current(organization.id, using)
    sub = Subscription(
        organization=organization,
        plan=plan,
        start_date=start_date,
        end_date=end_date,
        price=Decimal("0.00"),
        discount=Decimal("0.00"),
        tax=Decimal("0.00"),
        billing_cycle=Subscription.BillingCycle.CUSTOM,
        status=Subscription.Status.ACTIVE,
        payment_status=Subscription.PaymentStatus.NOT_APPLICABLE,
        auto_renewal=False,
        is_complimentary=True,
        notes=notes,
        is_current=True,
    )
    sub.save(using=using)
    return sub


def cancel_subscription(subscription: Subscription, *, using: str = "default", cancellation_date=None) -> Subscription:
    subscription.status = Subscription.Status.CANCELLED
    subscription.cancellation_date = cancellation_date or timezone.localdate()
    subscription.auto_renewal = False
    subscription.save(using=using)
    return subscription


def _shift_months(d: datetime.date, months: int) -> datetime.date:
    """`d` minus `months` calendar months, clamping the day if the target
    month is shorter (e.g. 31 Mar minus 1 month -> 28/29 Feb). No external
    dependency (dateutil) needed for a shift this simple."""
    month_index = d.month - 1 - months
    year = d.year + month_index // 12
    month = month_index % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return datetime.date(year, month, day)


RENEWAL_GRACE_DAYS = 3


def historical_window_start(organization, *, using: str = "default", as_of=None) -> datetime.date | None:
    """The earliest date this org's current plan allows a NEW transaction
    to be dated — the backend half of the historical-data-limit rule
    (never trust the frontend alone). Returns None when there's no
    subscription/plan on file, meaning no limit is enforced (the billing
    access gate already blocks a truly lapsed org from creating anything
    at all, so this only matters for an org with an active plan).

    Combines two independent floors and returns whichever is later (more
    restrictive): the plan's normal rolling window, and — if the org's
    last renewal came in later than RENEWAL_GRACE_DAYS after its previous
    period lapsed — entry_floor_date, which permanently walls off the
    unpaid gap from ever being backdated into. A renewal within the grace
    window instead leaves entry_floor_date null, so the normal window
    alone decides (already wide enough to cover a grace-period-long gap
    in the common case)."""
    as_of = as_of or timezone.localdate()
    sub = get_current_subscription(organization.id, using=using)
    if sub is None:
        return None
    normal_floor = _shift_months(as_of, sub.plan.historical_months_limit)
    if sub.entry_floor_date and sub.entry_floor_date > normal_floor:
        return sub.entry_floor_date
    return normal_floor


def has_active_access(organization, *, using: str = "default", as_of=None) -> bool:
    """The prepaid gate: does the org's CURRENT subscription still cover
    `as_of` (today by default)? This is deliberately never optimistic — no
    subscription at all, or one whose trial/paid period has lapsed, always
    reads as inactive, regardless of how it got that way. It's checked
    alongside (not instead of) Organization.is_service_active, which is
    the separate, manually-controlled superadmin stop/suspend switch.
    """
    as_of = as_of or timezone.localdate()
    sub = get_current_subscription(organization.id, using=using)
    if sub is None:
        return False
    if sub.status in (Subscription.Status.CANCELLED, Subscription.Status.EXPIRED, Subscription.Status.SUSPENDED):
        return False
    if sub.status == Subscription.Status.TRIAL:
        return sub.trial_end_date is None or sub.trial_end_date >= as_of
    return sub.end_date is None or sub.end_date >= as_of


def ensure_renewal_invoice(organization, *, using: str = "default") -> None:
    """Called whenever access is found to be lapsed: if there's no unpaid
    invoice yet for the current (lapsed) subscription, issue one now, due
    immediately — prepaid means no grace period — and flip the
    subscription to PAYMENT_DUE/PAST_DUE so superadmin views show it too.
    Idempotent: a lapsed period only ever gets one open invoice, so this
    is safe to call on every blocked request.
    """
    sub = get_current_subscription(organization.id, using=using)
    if sub is None:
        return

    has_open_invoice = Invoice.objects.using(using).filter(
        organization_id=organization.id,
        subscription=sub,
        status__in=[Invoice.Status.ISSUED, Invoice.Status.PARTIALLY_PAID, Invoice.Status.OVERDUE],
    ).exists()
    if has_open_invoice:
        return

    amount = (
        sub.plan.yearly_price if sub.billing_cycle == Subscription.BillingCycle.YEARLY else sub.plan.monthly_price
    )
    if not amount:
        return

    today = timezone.localdate()
    invoicing.create_invoice(
        organization,
        using=using,
        subscription=sub,
        subtotal=amount,
        invoice_date=today,
        due_date=today,
        status=Invoice.Status.ISSUED,
    )
    new_status = (
        Subscription.Status.PAYMENT_DUE if sub.status == Subscription.Status.TRIAL else Subscription.Status.PAST_DUE
    )
    if sub.status != new_status:
        sub.status = new_status
        sub.save(using=using, update_fields=["status", "updated_at"])


def renew_subscription_from_invoice(invoice: Invoice, *, using: str = "default") -> None:
    """A renewal invoice was just paid in full — extend its subscription's
    coverage by one billing period and flip it back to ACTIVE, which is
    what has_active_access() reads to restore full access. This is the
    ONLY path that reactivates a lapsed subscription — never a time-based
    or manual shortcut, matching the prepaid rule that service never
    resumes without a real successful payment.

    Also decides the fate of the gap between when the prior period lapsed
    and today: paid within RENEWAL_GRACE_DAYS, the gap stays fully
    backdatable (entry_floor_date cleared, so only the plan's normal
    window applies); paid later than that, entry_floor_date is set to
    today, permanently blocking the gap from ever being entered — per
    invoice creation only ever happening via ensure_renewal_invoice() once
    access has actually lapsed, this subscription's status/dates as found
    here are exactly the lapsed state, read before they're overwritten
    below.
    """
    sub = invoice.subscription
    if sub is None or not sub.is_current:
        return

    today = timezone.localdate()

    lapse_date = sub.trial_end_date if sub.status == Subscription.Status.PAYMENT_DUE else sub.end_date
    lapse_date = lapse_date or sub.end_date or sub.trial_end_date
    if lapse_date and (today - lapse_date).days > RENEWAL_GRACE_DAYS:
        sub.entry_floor_date = today
    else:
        sub.entry_floor_date = None

    days = 365 if sub.billing_cycle == Subscription.BillingCycle.YEARLY else 30
    base = sub.end_date if sub.end_date and sub.end_date >= today else today
    sub.end_date = base + datetime.timedelta(days=days)
    if sub.start_date is None:
        sub.start_date = today
    sub.status = Subscription.Status.ACTIVE
    sub.payment_status = Subscription.PaymentStatus.PAID
    sub.save(using=using)


class CouponError(Exception):
    """Raised by redeem_coupon() with a message safe to show the customer
    directly — never a raw model/DB error."""


@transaction.atomic
def redeem_coupon(*, organization, invoice: Invoice, code: str, using: str = "default") -> CouponRedemption:
    """Apply a coupon code to one of this org's own outstanding invoices:
    validates the code, computes its discount off the invoice's subtotal,
    folds that into Invoice.discount (stacking with any manual discount
    already on the invoice) and lets Invoice.save() recompute total/
    amount_due — the same total/amount_due CreateInvoicePaymentOrderView
    and Razorpay checkout read, so no other code needs to know a coupon
    was involved. Raises CouponError with a customer-facing message on
    any failure; nothing is written unless redemption fully succeeds.
    """
    code = (code or "").strip().upper()
    if not code:
        raise CouponError("Enter a coupon code.")

    if invoice.status in (Invoice.Status.PAID, Invoice.Status.CANCELLED):
        raise CouponError("This invoice is already settled — a coupon can't be applied to it.")
    if invoice.amount_paid and invoice.amount_paid > 0:
        raise CouponError("A payment has already been made against this invoice, so a coupon can no longer be applied.")
    if CouponRedemption.objects.using(using).filter(invoice=invoice).exists():
        raise CouponError("A coupon has already been applied to this invoice.")

    coupon = Coupon.objects.using(using).filter(code=code).first()
    if coupon is None:
        raise CouponError("That coupon code isn't valid.")
    if not coupon.is_valid_now(using=using):
        raise CouponError("That coupon isn't active or has expired.")

    if coupon.max_redemptions_per_org is not None:
        already_used = CouponRedemption.objects.using(using).filter(coupon=coupon, organization=organization).count()
        if already_used >= coupon.max_redemptions_per_org:
            raise CouponError("You've already used this coupon the maximum number of times.")

    return _apply_coupon(coupon, organization, invoice, using=using)


def _apply_coupon(coupon: Coupon, organization, invoice: Invoice, *, using: str) -> CouponRedemption:
    discount = coupon.discount_amount_for(invoice.subtotal)
    if discount <= 0:
        raise CouponError("This coupon doesn't apply any discount to this invoice.")

    invoice.discount = (invoice.discount or Decimal("0")) + discount
    invoice.save(using=using)

    redemption = CouponRedemption(
        coupon=coupon, organization=organization, invoice=invoice, discount_amount=discount
    )
    redemption.save(using=using)

    if invoice.amount_due <= 0:
        # A 100%-off coupon settles the invoice outright: treat it as paid so access is restored.
        from . import payments

        payments.apply_payment_to_invoice(invoice, Decimal("0"), using=using)
    return redemption


@transaction.atomic
def apply_coupon_as_admin(*, coupon: Coupon, invoice: Invoice, using: str = "default") -> CouponRedemption:
    """A superadmin attaches a coupon to a client's invoice — same effect as
    the client redeeming the code, so the invoice's total drops and the client
    sees the reduced amount on their Billing page. The admin decides, so the
    validity window and usage caps are not enforced; the coupon just has to be
    switched on, and the invoice still open with nothing paid yet."""
    if not coupon.is_active:
        raise CouponError("That coupon is switched off.")
    if invoice.status in (Invoice.Status.PAID, Invoice.Status.CANCELLED):
        raise CouponError("This invoice is already settled - a coupon can't be applied to it.")
    if invoice.amount_paid and invoice.amount_paid > 0:
        raise CouponError("A payment has already been made against this invoice.")
    if CouponRedemption.objects.using(using).filter(invoice=invoice).exists():
        raise CouponError("A coupon has already been applied to this invoice.")
    return _apply_coupon(coupon, invoice.organization, invoice, using=using)


# ---------------------------------------------------------------------------
# Autopay — recurring billing via Razorpay Subscriptions. Razorpay owns the
# monthly clock; this app only reacts to what it reports (Checkout's
# success callback for setup, webhooks for every charge after that). There
# is deliberately no local scheduler/cron here.
# ---------------------------------------------------------------------------

AUTOPAY_TOTAL_CYCLES = 9999  # Razorpay requires a cap; this stands in for "until cancelled" (~832 years monthly).


class AutopayError(Exception):
    """Raised with a message safe to show the customer directly."""


def get_or_create_razorpay_plan(plan: Plan, billing_cycle: str, *, using: str = "default") -> str:
    """Razorpay's own Plan id for `plan`'s price at `billing_cycle`,
    creating it on Razorpay (and caching the id on our Plan row) the first
    time it's needed — Razorpay has no upsert, so this cache is what makes
    repeated calls idempotent instead of spawning a new Plan every time."""
    field = "razorpay_yearly_plan_id" if billing_cycle == Subscription.BillingCycle.YEARLY else "razorpay_monthly_plan_id"
    existing = getattr(plan, field)
    if existing:
        return existing

    amount = plan.yearly_price if billing_cycle == Subscription.BillingCycle.YEARLY else plan.monthly_price
    if not amount:
        raise AutopayError("This plan has no price set for autopay to bill against.")
    period = "yearly" if billing_cycle == Subscription.BillingCycle.YEARLY else "monthly"

    razorpay_plan = razorpay_client.create_plan(
        name=f"{plan.name} ({period})", amount=amount, currency="INR", period=period
    )
    setattr(plan, field, razorpay_plan["id"])
    plan.save(using=using, update_fields=[field, "updated_at"])
    return razorpay_plan["id"]


def enable_autopay(organization, *, using: str = "default") -> dict:
    """Starts setting up autopay: creates a Razorpay Subscription against
    the org's current plan/cycle and hands back what the Checkout popup
    (in subscription mode) needs to open. autopay_enabled is NOT set yet —
    that only happens once Checkout's success callback is verified
    (confirm_autopay), since the mandate isn't real until the customer
    actually authorizes it."""
    if not razorpay_client.is_configured():
        raise AutopayError("Online payment isn't set up yet — contact support.")

    sub = get_current_subscription(organization.id, using=using)
    if sub is None:
        raise AutopayError("No subscription on file to enable autopay for.")
    if sub.autopay_enabled and sub.razorpay_subscription_id:
        raise AutopayError("Autopay is already on for this organization.")

    razorpay_plan_id = get_or_create_razorpay_plan(sub.plan, sub.billing_cycle, using=using)
    razorpay_sub = razorpay_client.create_subscription(
        razorpay_plan_id=razorpay_plan_id,
        total_count=AUTOPAY_TOTAL_CYCLES,
        notes={"organization_code": organization.organization_code, "subscription_pk": str(sub.pk)},
    )
    return {
        "subscription_id": razorpay_sub["id"],
        "key_id": settings.RAZORPAY_KEY_ID,
        "plan_name": sub.plan.name,
        "organization_name": organization.name,
    }


@transaction.atomic
def confirm_autopay(*, organization, razorpay_subscription_id: str, payment_id: str, signature: str, using: str = "default") -> Subscription:
    if not razorpay_client.verify_subscription_signature(
        subscription_id=razorpay_subscription_id, payment_id=payment_id, signature=signature
    ):
        raise AutopayError("Autopay authorization could not be verified.")

    sub = get_current_subscription(organization.id, using=using)
    if sub is None:
        raise AutopayError("No subscription on file to enable autopay for.")
    sub.razorpay_subscription_id = razorpay_subscription_id
    sub.autopay_enabled = True
    sub.auto_renewal = True
    sub.save(using=using, update_fields=["razorpay_subscription_id", "autopay_enabled", "auto_renewal", "updated_at"])
    return sub


def cancel_autopay(organization, *, using: str = "default") -> None:
    """Either the org owner (Billing page) or superadmin (ops console)
    calls this — both routes land here. Cancels the mandate on Razorpay's
    side immediately (no more future charges) and clears our own flag;
    the current period the org already paid for is untouched, so access
    isn't affected until it would naturally lapse."""
    sub = get_current_subscription(organization.id, using=using)
    if sub is None or not sub.autopay_enabled:
        return
    if sub.razorpay_subscription_id and razorpay_client.is_configured():
        try:
            razorpay_client.cancel_subscription(sub.razorpay_subscription_id)
        except Exception:
            pass  # Already cancelled/expired on Razorpay's side, or unreachable — still clear our flag below.
    sub.autopay_enabled = False
    sub.auto_renewal = False
    sub.save(using=using, update_fields=["autopay_enabled", "auto_renewal", "updated_at"])


def disable_autopay_flag(razorpay_subscription_id: str, *, using: str = "default") -> None:
    """Called from the webhook when Razorpay itself reports the mandate is
    no longer active (subscription.cancelled/halted/completed) — e.g. the
    customer cancelled from their bank/UPI app directly, or a card
    expired. Only clears the local flag; never touches access/dates."""
    Subscription.objects.using(using).filter(razorpay_subscription_id=razorpay_subscription_id).update(
        autopay_enabled=False, auto_renewal=False, updated_at=timezone.now()
    )


@transaction.atomic
def record_subscription_charge(
    *, razorpay_subscription_id: str, amount, currency: str, transaction_id: str, using: str = "default"
) -> None:
    """Razorpay just auto-charged an org's autopay mandate for its next
    billing cycle (webhook: subscription.charged). Creates the cycle's
    invoice, marks it paid via the same idempotent record_payment() path
    manual payments use, which in turn calls renew_subscription_from_invoice
    — extending coverage exactly as a manual renewal would, whether this
    charge landed before the prior period lapsed (the common case) or
    slightly after (still covered by the same grace-period logic)."""
    from . import payments as payment_services

    sub = Subscription.objects.using(using).filter(razorpay_subscription_id=razorpay_subscription_id).first()
    if sub is None:
        return

    invoice = invoicing.create_invoice(
        sub.organization, using=using, subscription=sub, subtotal=amount, status=Invoice.Status.ISSUED
    )
    payment_services.record_payment(
        sub.organization,
        using=using,
        subscription=sub,
        invoice=invoice,
        amount=amount,
        currency=currency,
        payment_method=Payment.Method.OTHER,
        gateway=Payment.Gateway.RAZORPAY,
        transaction_id=transaction_id,
        status=Payment.Status.SUCCESS,
    )
