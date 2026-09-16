import secrets
from decimal import ROUND_HALF_UP, Decimal

from django.core.validators import MaxValueValidator
from django.db import models


class Plan(models.Model):
    """A subscription tier a superadmin defines and offers to clients. Lives
    in the shared `public` schema of each environment's own database — a
    "Pro" plan in dev and a "Pro" plan in prod are separate rows.
    """

    FEATURE_CHOICES = [
        ("business_dashboard", "Business Dashboard"),
        ("sales_analytics", "Sales Analytics"),
        ("expense_analytics", "Expense Analytics"),
        ("purchase_analytics", "Purchase Analytics"),
        ("profit_loss", "Profit & Loss"),
        ("cash_flow", "Cash Flow"),
        ("reports", "Reports"),
        ("export", "Export"),
        ("ai_insights", "AI Insights"),
    ]

    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=110, unique=True)
    is_active = models.BooleanField(default=True)
    show_on_landing_page = models.BooleanField(
        default=True, help_text="Show this plan in the public pricing section on the landing page"
    )

    monthly_price = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    yearly_price = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    monthly_discount_percent = models.PositiveIntegerField(
        default=0, blank=True, validators=[MaxValueValidator(100)],
        help_text="% off the monthly price, shown on the landing page",
    )
    yearly_discount_percent = models.PositiveIntegerField(
        default=0, blank=True, validators=[MaxValueValidator(100)],
        help_text="% off the yearly price, shown on the landing page",
    )
    trial_days = models.PositiveIntegerField(default=0, help_text="0 = no trial period")
    historical_months_limit = models.PositiveIntegerField(
        default=1,
        help_text="How many months back a customer on this plan may create or backdate a transaction.",
    )

    # Razorpay's own Plan object id for this plan's price, one per billing
    # cycle — created lazily the first time an org enables autopay on that
    # cycle (see services.get_or_create_razorpay_plan) and cached here so
    # it's only ever created once per (plan, cycle, environment).
    razorpay_monthly_plan_id = models.CharField(max_length=50, blank=True, editable=False)
    razorpay_yearly_plan_id = models.CharField(max_length=50, blank=True, editable=False)

    user_limit = models.PositiveIntegerField(null=True, blank=True, help_text="Blank = unlimited")
    business_limit = models.PositiveIntegerField(null=True, blank=True, help_text="Blank = unlimited")

    # List of FEATURE_CHOICES keys this plan grants access to. Not yet
    # enforced anywhere in the app (no view checks a plan's features or
    # limits) — this is the data model for that; gating is a follow-up.
    features = models.JSONField(default=list, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["monthly_price", "name"]

    def __str__(self) -> str:
        return self.name

    def has_feature(self, key: str) -> bool:
        return key in (self.features or [])

    def feature_labels(self):
        keys = set(self.features or [])
        return [label for key, label in self.FEATURE_CHOICES if key in keys]

    @property
    def effective_monthly_price(self) -> Decimal:
        return self._discounted(self.monthly_price, self.monthly_discount_percent)

    @property
    def effective_yearly_price(self) -> Decimal:
        return self._discounted(self.yearly_price, self.yearly_discount_percent)

    @staticmethod
    def _discounted(price: Decimal, percent: int) -> Decimal:
        price = price or Decimal("0")
        if not percent:
            return price
        raw = price * (Decimal(100) - percent) / Decimal(100)
        # Rounded to the nearest whole rupee — a discounted price like
        # ₹2399.20 reads as ₹2399 on the pricing page.
        return raw.quantize(Decimal("1"), rounding=ROUND_HALF_UP)


def generate_subscription_id() -> str:
    return f"SUB-{secrets.token_hex(4).upper()}"


class Subscription(models.Model):
    """One subscription period for one organization. A NEW row is created
    every time a client's plan, billing cycle, or pricing changes — existing
    rows are never edited into a different period and never deleted, so this
    table is also the subscription history. Exactly one row per organization
    should have `is_current=True` at a time (the one superadmin/list/detail
    views show as "the" subscription); older rows keep whatever status they
    had when they stopped being current.
    """

    class Status(models.TextChoices):
        TRIAL = "TRIAL", "Trial"
        ACTIVE = "ACTIVE", "Active"
        PAYMENT_DUE = "PAYMENT_DUE", "Payment Due"
        PAST_DUE = "PAST_DUE", "Past Due"
        SUSPENDED = "SUSPENDED", "Suspended"
        EXPIRED = "EXPIRED", "Expired"
        CANCELLED = "CANCELLED", "Cancelled"

    class PaymentStatus(models.TextChoices):
        PAID = "PAID", "Paid"
        PENDING = "PENDING", "Pending"
        FAILED = "FAILED", "Failed"
        NOT_APPLICABLE = "NOT_APPLICABLE", "Not applicable"

    class BillingCycle(models.TextChoices):
        MONTHLY = "MONTHLY", "Monthly"
        YEARLY = "YEARLY", "Yearly"
        CUSTOM = "CUSTOM", "Custom"

    subscription_id = models.CharField(max_length=20, unique=True, editable=False)
    organization = models.ForeignKey(
        "organizations.Organization", on_delete=models.CASCADE, related_name="subscriptions"
    )
    plan = models.ForeignKey(Plan, on_delete=models.PROTECT, related_name="subscriptions")

    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    trial_start_date = models.DateField(null=True, blank=True)
    trial_end_date = models.DateField(null=True, blank=True)

    price = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    discount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    tax = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    final_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    billing_cycle = models.CharField(max_length=10, choices=BillingCycle.choices, default=BillingCycle.MONTHLY)
    payment_status = models.CharField(
        max_length=16, choices=PaymentStatus.choices, default=PaymentStatus.NOT_APPLICABLE
    )
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.TRIAL)

    auto_renewal = models.BooleanField(default=True)
    cancellation_date = models.DateField(null=True, blank=True)
    is_complimentary = models.BooleanField(default=False)
    is_current = models.BooleanField(default=True)
    notes = models.TextField(blank=True)

    # Set by services.renew_subscription_from_invoice() whenever a renewal
    # payment lands more than the grace period (services.RENEWAL_GRACE_DAYS)
    # after the prior period lapsed: the date transactions may be backdated
    # to is raised to the renewal date itself, permanently walling off the
    # unpaid gap from ever being entered. Null means no such gap is in
    # effect — the normal plan.historical_months_limit window is all that
    # applies. Cleared (set back to null) by an on-time renewal, since
    # there's no gap to protect in that case.
    entry_floor_date = models.DateField(null=True, blank=True)

    # Recurring billing via Razorpay Subscriptions — set when the org owner
    # (or superadmin) enables autopay: razorpay_subscription_id is
    # Razorpay's own "sub_..." object, which Razorpay itself charges every
    # billing cycle until cancelled (services.cancel_autopay /
    # PaymentWebhookView's subscription.cancelled handling both clear
    # autopay_enabled). No local scheduler is involved — Razorpay owns the
    # clock; this app only reacts to its webhook events.
    razorpay_subscription_id = models.CharField(max_length=50, blank=True, editable=False)
    autopay_enabled = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["organization", "is_current"])]

    def __str__(self) -> str:
        return f"{self.subscription_id} — {self.organization.name}"

    def save(self, *args, **kwargs):
        self.final_amount = (self.price or 0) - (self.discount or 0) + (self.tax or 0)
        if not self.subscription_id:
            self.subscription_id = generate_subscription_id()
        super().save(*args, **kwargs)


def generate_invoice_number() -> str:
    return f"INV-{secrets.token_hex(4).upper()}"


class Invoice(models.Model):
    """A bill issued to an organization for a subscription period. Kept
    forever once issued (no delete UI/action anywhere) — this is financial
    history, same philosophy as Subscription.
    """

    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        ISSUED = "ISSUED", "Issued"
        PAID = "PAID", "Paid"
        PARTIALLY_PAID = "PARTIALLY_PAID", "Partially Paid"
        OVERDUE = "OVERDUE", "Overdue"
        CANCELLED = "CANCELLED", "Cancelled"

    invoice_number = models.CharField(max_length=20, unique=True, editable=False)
    organization = models.ForeignKey(
        "organizations.Organization", on_delete=models.CASCADE, related_name="invoices"
    )
    subscription = models.ForeignKey(
        Subscription, on_delete=models.CASCADE, related_name="invoices", null=True, blank=True
    )

    invoice_date = models.DateField()
    due_date = models.DateField()

    currency = models.CharField(max_length=8, default="INR")
    subtotal = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    discount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    tax = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    amount_paid = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    amount_due = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-invoice_date", "-created_at"]
        indexes = [models.Index(fields=["organization", "status"])]

    def __str__(self) -> str:
        return f"{self.invoice_number} — {self.organization.name}"

    def save(self, *args, **kwargs):
        self.total = (self.subtotal or 0) - (self.discount or 0) + (self.tax or 0)
        self.amount_due = self.total - (self.amount_paid or 0)
        if not self.invoice_number:
            self.invoice_number = generate_invoice_number()
        super().save(*args, **kwargs)


def generate_payment_id() -> str:
    return f"PAY-{secrets.token_hex(4).upper()}"


class Payment(models.Model):
    """One payment attempt/transaction against an organization's
    subscription/invoice. Designed to be gateway-agnostic from day one —
    `gateway`/`payment_method`/`transaction_id`/`raw_gateway_response` are
    exactly the fields a real Razorpay or Stripe integration would populate
    later; `gateway=MANUAL` is what a superadmin uses to record an
    off-platform payment (bank transfer, cash, cheque) today.
    """

    class Status(models.TextChoices):
        SUCCESS = "SUCCESS", "Success"
        PENDING = "PENDING", "Pending"
        FAILED = "FAILED", "Failed"
        REFUNDED = "REFUNDED", "Refunded"
        PARTIALLY_REFUNDED = "PARTIALLY_REFUNDED", "Partially Refunded"

    class Method(models.TextChoices):
        CARD = "CARD", "Card"
        UPI = "UPI", "UPI"
        NETBANKING = "NETBANKING", "Netbanking"
        BANK_TRANSFER = "BANK_TRANSFER", "Bank Transfer"
        WALLET = "WALLET", "Wallet"
        CASH = "CASH", "Cash"
        OTHER = "OTHER", "Other"

    class Gateway(models.TextChoices):
        RAZORPAY = "RAZORPAY", "Razorpay"
        STRIPE = "STRIPE", "Stripe"
        MANUAL = "MANUAL", "Manual"
        OTHER = "OTHER", "Other"

    payment_id = models.CharField(max_length=20, unique=True, editable=False)
    organization = models.ForeignKey(
        "organizations.Organization", on_delete=models.CASCADE, related_name="payments"
    )
    subscription = models.ForeignKey(
        Subscription, on_delete=models.CASCADE, related_name="payments", null=True, blank=True
    )
    invoice = models.ForeignKey(
        Invoice, on_delete=models.CASCADE, related_name="payments", null=True, blank=True
    )

    amount = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=8, default="INR")
    payment_method = models.CharField(max_length=16, choices=Method.choices, default=Method.OTHER)
    gateway = models.CharField(max_length=10, choices=Gateway.choices, default=Gateway.MANUAL)
    # The gateway's own id for this transaction (e.g. Razorpay's "pay_xxx").
    # Blank for a manually-recorded payment with no external transaction.
    transaction_id = models.CharField(max_length=100, blank=True)

    payment_date = models.DateTimeField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    failure_reason = models.TextField(blank=True)

    refunded_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    refund_reason = models.TextField(blank=True)
    refunded_at = models.DateTimeField(null=True, blank=True)

    # Full webhook/API payload from the gateway, kept for audit and for
    # reprocessing/debugging once a real gateway is wired up.
    raw_gateway_response = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-payment_date"]
        indexes = [models.Index(fields=["organization", "status"])]
        constraints = [
            # The core webhook-idempotency guarantee at the DB level: the
            # same gateway transaction can never produce two Payment rows.
            models.UniqueConstraint(
                fields=["gateway", "transaction_id"],
                condition=~models.Q(transaction_id=""),
                name="unique_gateway_transaction_id",
            )
        ]

    def __str__(self) -> str:
        return f"{self.payment_id} — {self.organization.name}"

    def save(self, *args, **kwargs):
        if not self.payment_id:
            self.payment_id = generate_payment_id()
        super().save(*args, **kwargs)


class PaymentWebhookEvent(models.Model):
    """Idempotency ledger for inbound payment-gateway webhooks. Every
    delivery is recorded here first, keyed on (gateway, event_id) — a
    retried/duplicate delivery of the same event is recognized and
    short-circuited before it can create a second Payment.
    """

    class Status(models.TextChoices):
        RECEIVED = "RECEIVED", "Received"
        PROCESSED = "PROCESSED", "Processed"
        IGNORED = "IGNORED", "Ignored"
        FAILED = "FAILED", "Failed"

    gateway = models.CharField(max_length=10, choices=Payment.Gateway.choices)
    event_id = models.CharField(max_length=150)
    event_type = models.CharField(max_length=64, blank=True)
    payload = models.JSONField(default=dict)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.RECEIVED)
    payment = models.ForeignKey(
        Payment, on_delete=models.SET_NULL, null=True, blank=True, related_name="webhook_events"
    )
    error_message = models.TextField(blank=True)
    received_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-received_at"]
        constraints = [
            models.UniqueConstraint(fields=["gateway", "event_id"], name="unique_gateway_event_id")
        ]

    def __str__(self) -> str:
        return f"{self.gateway}:{self.event_id}"


def generate_coupon_id() -> str:
    return f"CPN-{secrets.token_hex(3).upper()}"


class Coupon(models.Model):
    """A discount code a superadmin creates; an organization owner redeems
    it against one of their own outstanding invoices on the Billing page.
    Lives in the shared `public` schema, same as Plan/Subscription/Invoice
    — one "SUMMER25" coupon in dev and one in prod are separate rows.
    """

    class DiscountType(models.TextChoices):
        PERCENT = "PERCENT", "Percentage"
        FLAT = "FLAT", "Flat amount"

    coupon_id = models.CharField(max_length=20, unique=True, editable=False)
    code = models.CharField(max_length=40, unique=True, help_text="What the customer types in, e.g. SUMMER25")
    description = models.CharField(max_length=255, blank=True)

    discount_type = models.CharField(max_length=10, choices=DiscountType.choices, default=DiscountType.PERCENT)
    discount_value = models.DecimalField(
        max_digits=10, decimal_places=2,
        help_text="A percentage (0-100) or a flat currency amount, depending on Discount type.",
    )

    is_active = models.BooleanField(default=True)
    valid_from = models.DateField(null=True, blank=True, help_text="Blank = usable immediately")
    valid_until = models.DateField(null=True, blank=True, help_text="Blank = never expires")

    max_redemptions = models.PositiveIntegerField(
        null=True, blank=True, help_text="Total number of times this code may be used, across all organizations. Blank = unlimited."
    )
    max_redemptions_per_org = models.PositiveIntegerField(
        default=1, help_text="How many times a single organization may use this code."
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return self.code

    def save(self, *args, **kwargs):
        if not self.coupon_id:
            self.coupon_id = generate_coupon_id()
        self.code = (self.code or "").strip().upper()
        super().save(*args, **kwargs)

    def is_valid_now(self, *, as_of=None, using: str = "default") -> bool:
        from django.utils import timezone

        as_of = as_of or timezone.localdate()
        if not self.is_active:
            return False
        if self.valid_from and as_of < self.valid_from:
            return False
        if self.valid_until and as_of > self.valid_until:
            return False
        if self.max_redemptions is not None:
            used = CouponRedemption.objects.using(using).filter(coupon=self).count()
            if used >= self.max_redemptions:
                return False
        return True

    def discount_amount_for(self, subtotal) -> Decimal:
        """The rupee discount this coupon applies to `subtotal` — never
        more than the subtotal itself, so a coupon can't push an invoice
        negative."""
        subtotal = Decimal(subtotal)
        if self.discount_type == self.DiscountType.PERCENT:
            amount = (subtotal * self.discount_value / Decimal("100")).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
        else:
            amount = self.discount_value
        return min(amount, subtotal) if subtotal > 0 else Decimal("0.00")


class CouponRedemption(models.Model):
    """One use of a coupon against one invoice — the audit trail, and what
    max_redemptions / max_redemptions_per_org are actually enforced
    against (counting these rows rather than a mutable counter keeps this
    reconstructable and safe under concurrent redemption attempts, backed
    by the OneToOne to Invoice — a given invoice can only ever have one
    coupon applied)."""

    coupon = models.ForeignKey(Coupon, on_delete=models.PROTECT, related_name="redemptions")
    organization = models.ForeignKey(
        "organizations.Organization", on_delete=models.CASCADE, related_name="coupon_redemptions"
    )
    invoice = models.OneToOneField(Invoice, on_delete=models.CASCADE, related_name="coupon_redemption")
    discount_amount = models.DecimalField(max_digits=10, decimal_places=2)
    redeemed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-redeemed_at"]
        indexes = [models.Index(fields=["coupon", "organization"])]

    def __str__(self) -> str:
        return f"{self.coupon.code} on {self.invoice.invoice_number}"
