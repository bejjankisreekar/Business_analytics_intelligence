import secrets
from decimal import Decimal

from django.conf import settings
from django.db import models

from apps.organizations.models import Organization


def _new_token() -> str:
    return secrets.token_urlsafe(16)


class QuoteRequestSettings(models.Model):
    """Per-organization switch for the open link. Off until the owner turns it on."""

    organization = models.OneToOneField(
        Organization, on_delete=models.CASCADE, related_name="quote_request_settings"
    )
    is_enabled = models.BooleanField(default=False)

    # Telling the owner that a customer sent (or changed) a list.
    notify_email_enabled = models.BooleanField(default=True)
    # Blank means the owner's own login email.
    notify_email = models.EmailField(blank=True)
    # The shop's WhatsApp number: customers get a button that opens a ready-made message to it.
    notify_whatsapp = models.CharField(max_length=20, blank=True)

    def __str__(self) -> str:
        return f"{self.organization_id}: {'on' if self.is_enabled else 'off'}"


class ProductSuggestion(models.Model):
    """A product name offered as autocomplete on the open page.

    A copy of the shop's inventory names (product categories and subcategories),
    refreshed whenever the owner opens Customer Requests. Keeping our own copy
    means the logged-out page never has to read the shop's finance data.
    """

    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="quote_product_suggestions"
    )
    name = models.CharField(max_length=200)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["organization", "name"], name="uniq_quote_product_suggestion"),
        ]

    def __str__(self) -> str:
        return self.name


class QuoteRequest(models.Model):
    """One customer's list, submitted through the organization's open link."""

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        QUOTED = "QUOTED", "Quoted"

    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="quote_requests"
    )
    # Human-friendly per-organization number (#1, #2, ...) shown to both sides.
    number = models.PositiveIntegerField()
    # The only way a customer (who has no login) can get back to their reply.
    tracking_token = models.CharField(max_length=32, unique=True, default=_new_token, editable=False)
    # A second, view-only link the customer can hand to family or friends (Share list). It
    # shows the list and the quote but not the contact details, and cannot edit or pay.
    # Created the first time it is needed, so existing requests need no data migration.
    share_token = models.CharField(max_length=32, blank=True, db_index=True, editable=False, default="")

    customer_name = models.CharField(max_length=150)
    customer_phone = models.CharField(max_length=20)
    # Optional: where to deliver. Left blank by customers who collect from the shop.
    customer_address = models.CharField(max_length=255, blank=True)

    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    created_at = models.DateTimeField(auto_now_add=True)
    # Set when the customer changes their list (allowed only until the quote is sent).
    edited_at = models.DateTimeField(null=True, blank=True)
    quoted_at = models.DateTimeField(null=True, blank=True)

    class PaymentStatus(models.TextChoices):
        PENDING = "PENDING", "Payment pending"
        PAID = "PAID", "Paid"

    # Marked by hand by the shop once a quote is out; no payment gateway involved.
    # Once PAID the order can be delivered.
    payment_status = models.CharField(max_length=10, choices=PaymentStatus.choices, default=PaymentStatus.PENDING)
    paid_at = models.DateTimeField(null=True, blank=True)
    # How it was paid ("CASH" / "BANK") and the Daily Book sale booked for it, so the
    # sale can be taken back out if the payment is reverted.
    payment_mode = models.CharField(max_length=10, blank=True)
    sales_entry_id = models.UUIDField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["organization", "number"], name="uniq_quote_request_number"),
        ]
        indexes = [models.Index(fields=["organization", "status", "-created_at"])]

    def __str__(self) -> str:
        return f"#{self.number} {self.customer_name}"

    @property
    def total(self) -> Decimal:
        """Sum of line totals for rows the shop can supply."""
        return sum((i.line_total or Decimal("0") for i in self.items.filter(available=True)), Decimal("0"))


class Unit(models.TextChoices):
    """What a quantity is counted in. Order is the order of the dropdown."""

    KG = "kg", "kg"
    G = "g", "grams"
    PC = "pc", "pc"
    DOZEN = "dozen", "dozen"
    LITRE = "litre", "litre"
    ML = "ml", "ml"
    PACKET = "packet", "packet"
    BOX = "box", "box"
    BOTTLE = "bottle", "bottle"
    BAG = "bag", "bag"
    PLATE = "plate", "plate"
    PORTION = "portion", "portion"
    METRE = "metre", "metre"


class QuoteRequestItem(models.Model):
    request = models.ForeignKey(QuoteRequest, on_delete=models.CASCADE, related_name="items")
    serial = models.PositiveIntegerField()
    product_name = models.CharField(max_length=200)
    qty = models.DecimalField(max_digits=10, decimal_places=3)
    unit = models.CharField(max_length=10, choices=Unit.choices, default=Unit.KG)

    # Filled in by the shop when quoting.
    available = models.BooleanField(default=True)
    # Rate per one unit; the row amount is price x quantity (see line_total).
    price = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    remark = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["serial"]

    def __str__(self) -> str:
        return f"{self.serial}. {self.product_name}"

    @property
    def rate_unit(self) -> str:
        """The unit as read after "per": "per gram", "per kg", "per litre"."""
        return "gram" if self.unit == Unit.G else self.get_unit_display()

    @property
    def line_total(self):
        """Rate x requested quantity, or None if not priced."""
        if self.price is None:
            return None
        return (self.price * self.qty).quantize(Decimal("0.01"))


class DeviceToken(models.Model):
    """A phone signed in to the mobile app that can be sent push notifications (Firebase token)."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="push_devices")
    token = models.CharField(max_length=255, unique=True)
    platform = models.CharField(max_length=20, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_seen_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"{self.user_id}: {self.platform or 'device'}"
