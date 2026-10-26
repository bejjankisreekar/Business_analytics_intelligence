"""Tenant-scoped models.

Every model below uses `objects = SheetAwareManager()` and mixes in
SheetAwareModelMixin, which route querying and instance.save()/
.delete() one of two ways depending on the owning Organization's
storage_mode:

- GOOGLE_SHEETS: live against that org's own Google Sheet, via
  apps.sheets_store, whenever a SheetSession is active for the request
  (see apps/organizations/middleware.py). Never written to our own
  database. The `organization` column below is never populated for
  these rows.
- OUR_DATABASE: a real row in our own shared Postgres tables,
  transparently scoped to the current request's organization — every
  query auto-filtered, every new row auto-tagged with it (see
  apps.organizations.tenant_context, also set by that same middleware).

Both paths share the exact same `apps.finance` views/services code —
neither the model's own methods nor its callers need to know which
mode a given org is in.
"""
import datetime
import uuid
from decimal import Decimal

from django.db import models

from apps.organizations.models import Organization
from apps.sheets_store.mixin import SheetAwareModelMixin
from apps.sheets_store.queryset import SheetAwareManager


class PaymentMode(models.TextChoices):
    CASH = "CASH", "Cash"
    # Stored as BANK; shown as Online. The bank account is still picked for it.
    BANK = "BANK", "Online"


class Category(SheetAwareModelMixin, models.Model):
    objects = SheetAwareManager()

    class Kind(models.TextChoices):
        SALES = "SALES", "Revenue channel"
        EXPENSE = "EXPENSE", "Expense category"
        PURCHASE = "PURCHASE", "Purchase category"
        PRODUCT = "PRODUCT", "Product category"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="+")
    kind = models.CharField(max_length=10, choices=Kind.choices)
    name = models.CharField(max_length=100)
    is_active = models.BooleanField(default=True)
    gst_rate = models.DecimalField(
        "GST rate %", max_digits=5, decimal_places=2, default=0,
        help_text="Applied to every sale/purchase logged under this category, for the GST Summary report.",
    )

    class Meta:
        base_manager_name = "objects"
        ordering = ["kind", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "kind", "name"], name="finance_category_org_kind_name_uniq"
            ),
        ]

    def __str__(self) -> str:
        return self.name


class Subcategory(SheetAwareModelMixin, models.Model):
    """A specific item under a Category — an employee under 'Salaries &
    Wages', a brand under 'New Phone Sales', a vendor under a purchase
    category. Optional on every entry: pick a Category alone for a quick
    entry, or drill into a Subcategory when the extra detail is worth it.

    `parent` lets a Subcategory itself have children — e.g. Category "OPD
    Consultation" → Subcategory "Cardiology" → child Subcategory "Dr. Rao"
    — for cases where a single level of detail isn't enough. Left null for
    an ordinary (single-level) Subcategory.
    """

    objects = SheetAwareManager()

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="+")
    category = models.ForeignKey(Category, on_delete=models.CASCADE, related_name="subcategories")
    parent = models.ForeignKey(
        "self", on_delete=models.CASCADE, null=True, blank=True, related_name="children"
    )
    name = models.CharField(max_length=100)
    is_active = models.BooleanField(default=True)

    class Meta:
        base_manager_name = "objects"
        ordering = ["category", "name"]
        verbose_name_plural = "Subcategories"
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "category", "parent", "name"],
                name="finance_subcategory_org_category_parent_name_uniq",
            ),
        ]

    def __str__(self) -> str:
        if self.parent_id:
            return f"{self.category.name} → {self.parent.name} → {self.name}"
        return f"{self.category.name} → {self.name}"


class Customer(SheetAwareModelMixin, models.Model):
    objects = SheetAwareManager()

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="+")
    name = models.CharField(max_length=150)
    phone = models.CharField(max_length=30, blank=True)
    email = models.CharField(max_length=254, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        base_manager_name = "objects"
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["organization", "name"], name="finance_customer_org_name_uniq"),
        ]

    def __str__(self) -> str:
        return self.name


class Vendor(SheetAwareModelMixin, models.Model):
    """A supplier directory entry. `opening_balance` is what was already
    owed to this vendor before using the system — set on creation it seeds
    a matching Payable so it shows up in Cash Position immediately, the
    same way FinanceSettings.opening_balance seeds the opening cash
    position. Purchase/Payable vendor fields stay free text for quick
    logging; this is a separate, optional directory for tracking details."""

    objects = SheetAwareManager()

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="+")
    name = models.CharField(max_length=150)
    phone = models.CharField(max_length=30, blank=True)
    details = models.CharField(max_length=255, blank=True)
    opening_balance = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    opening_balance_as_on = models.DateField(default=datetime.date.today)
    is_active = models.BooleanField(default=True)

    class Meta:
        base_manager_name = "objects"
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["organization", "name"], name="finance_vendor_org_name_uniq"),
        ]

    def __str__(self) -> str:
        return self.name


class BankChoices(models.TextChoices):
    SBI = "SBI", "State Bank of India"
    HDFC = "HDFC", "HDFC Bank"
    ICICI = "ICICI", "ICICI Bank"
    AXIS = "AXIS", "Axis Bank"
    KOTAK = "KOTAK", "Kotak Mahindra Bank"
    PNB = "PNB", "Punjab National Bank"
    BOB = "BOB", "Bank of Baroda"
    CANARA = "CANARA", "Canara Bank"
    UNION = "UNION", "Union Bank of India"
    IDBI = "IDBI", "IDBI Bank"
    YES = "YES", "Yes Bank"
    INDUSIND = "INDUSIND", "IndusInd Bank"
    OTHER = "OTHER", "Other"


class BankAccount(SheetAwareModelMixin, models.Model):
    """One specific bank account the business holds (e.g. "Current A/c —
    ICICI"). Entries logged with PaymentMode.BANK can optionally be tagged
    to one of these, purely as an attribution/reporting layer — it doesn't
    change the overall cash/bank totals computed from payment_mode
    elsewhere, it just lets each account's own opening/closing balance be
    tracked, the same way Vendor/Partner track their own opening_balance."""

    objects = SheetAwareManager()

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="+")
    name = models.CharField(max_length=150, help_text='e.g. "Current Account" or "Salary Account"')
    bank_name = models.CharField(max_length=20, choices=BankChoices.choices, default=BankChoices.OTHER)
    other_bank_name = models.CharField(
        max_length=100, blank=True, help_text="Name of the bank, if not listed above"
    )
    account_number = models.CharField(max_length=50, blank=True, help_text="Optional — full number or last 4 digits")
    opening_balance = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    opening_balance_as_on = models.DateField(default=datetime.date.today)
    is_active = models.BooleanField(default=True)

    class Meta:
        base_manager_name = "objects"
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["organization", "name"], name="finance_bankaccount_org_name_uniq"),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.display_bank})"

    @property
    def display_bank(self) -> str:
        if self.bank_name == BankChoices.OTHER:
            return self.other_bank_name or "Other"
        return self.get_bank_name_display()


class SalesEntry(SheetAwareModelMixin, models.Model):
    objects = SheetAwareManager()

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="+")
    date = models.DateField(db_index=True)
    channel = models.ForeignKey(
        Category, on_delete=models.SET_NULL, null=True, blank=True, related_name="sales_entries"
    )
    subcategory = models.ForeignKey(
        Subcategory, on_delete=models.SET_NULL, null=True, blank=True, related_name="sales_entries"
    )
    product_category = models.ForeignKey(
        Category, on_delete=models.SET_NULL, null=True, blank=True, related_name="sales_as_product_category",
        limit_choices_to={"kind": "PRODUCT"},
    )
    customer = models.ForeignKey(
        Customer, on_delete=models.SET_NULL, null=True, blank=True, related_name="sales_entries"
    )
    quantity = models.PositiveIntegerField(null=True, blank=True)
    # `amount` stays the net figure everything else in the app reads (reports,
    # dashboards, GST, ledgers) — gross_amount/discount are the entry-time inputs
    # a discounted sale is captured from; save() below derives amount from them.
    gross_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    discount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    payment_mode = models.CharField(max_length=10, choices=PaymentMode.choices, default=PaymentMode.CASH)
    bank_account = models.ForeignKey(
        BankAccount, on_delete=models.SET_NULL, null=True, blank=True, related_name="sales_entries"
    )
    note = models.CharField(max_length=255, blank=True)
    created_by_email = models.CharField(max_length=254, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    kickback_bill = models.ForeignKey(
        "KickbackEntry", on_delete=models.SET_NULL, null=True, blank=True, related_name="received_sales",
        help_text="The kickback bill this receipt relates to (optional)",
    )

    class Meta:
        base_manager_name = "objects"
        ordering = ["-date", "-created_at"]
        indexes = [models.Index(fields=["date"])]

    def __str__(self) -> str:
        return f"Sale {self.date} — {self.amount}"

    def save(self, *args, **kwargs):
        # A caller that sets gross_amount (Bulk Entry, import) gets amount
        # derived from it. A caller that only ever set amount directly (e.g.
        # RecordReceivablePaymentView, which has no discount concept) gets
        # gross_amount backfilled to match instead, so Gross/Net never
        # disagree with the amount actually recorded.
        if self.discount is None:
            # The Bulk Entry/single-entry forms both make "discount"
            # optional and leave it blank rather than 0 when untouched
            # (see SalesEntryForm.__init__) — DecimalField.clean() turns
            # that blank into None, not the field's own default=0.
            self.discount = 0
        if self.gross_amount:
            self.amount = self.gross_amount - self.discount
        elif self.amount:
            self.gross_amount = self.amount
        previous_bill_id = None
        if self.pk:
            previous = SalesEntry.objects.filter(pk=self.pk).first()
            previous_bill_id = previous.kickback_bill_id if previous else None
        super().save(*args, **kwargs)
        for bill_id in {previous_bill_id, self.kickback_bill_id}:
            sync_kickback_received(bill_id)
        # A claim payment booked from this entry follows its amount and date, so
        # a change made in the Daily Book shows on the claim.
        for payment in ClaimPayment.objects.filter(sales_entry_id=self.pk):
            if payment.amount != self.amount or payment.date != self.date:
                payment.amount = self.amount
                payment.date = self.date
                payment.save()
                sync_claim_totals(payment.claim_id)

    def delete(self, *args, **kwargs):
        bill_id = self.kickback_bill_id
        claim_ids = list(ClaimPayment.objects.filter(sales_entry_id=self.pk).values_list("claim_id", flat=True))
        super().delete(*args, **kwargs)
        sync_kickback_received(bill_id)
        for claim_id in claim_ids:
            sync_claim_totals(claim_id)


class ExpenseEntry(SheetAwareModelMixin, models.Model):
    objects = SheetAwareManager()

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="+")
    date = models.DateField(db_index=True)
    category = models.ForeignKey(
        Category, on_delete=models.SET_NULL, null=True, blank=True, related_name="expense_entries"
    )
    subcategory = models.ForeignKey(
        Subcategory, on_delete=models.SET_NULL, null=True, blank=True, related_name="expense_entries"
    )
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    payment_mode = models.CharField(max_length=10, choices=PaymentMode.choices, default=PaymentMode.CASH)
    bank_account = models.ForeignKey(
        BankAccount, on_delete=models.SET_NULL, null=True, blank=True, related_name="expense_entries"
    )
    note = models.CharField(max_length=255, blank=True)
    created_by_email = models.CharField(max_length=254, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    paid_to_ledger = models.ForeignKey(
        "GeneralLedger", on_delete=models.SET_NULL, null=True, blank=True, related_name="expense_payments",
        help_text="The general ledger this payment went to (optional)",
    )
    kickback_bill = models.ForeignKey(
        "KickbackEntry", on_delete=models.SET_NULL, null=True, blank=True, related_name="expenses",
        help_text="The kickback bill this payment relates to (optional)",
    )

    class Meta:
        base_manager_name = "objects"
        ordering = ["-date", "-created_at"]
        indexes = [models.Index(fields=["date"])]

    def __str__(self) -> str:
        return f"Expense {self.date} — {self.amount}"

    def save(self, *args, **kwargs):
        previous_bill_id = None
        if self.pk:
            previous = ExpenseEntry.objects.filter(pk=self.pk).first()
            previous_bill_id = previous.kickback_bill_id if previous else None
        super().save(*args, **kwargs)
        for bill_id in {previous_bill_id, self.kickback_bill_id}:
            sync_kickback_total(bill_id)

    def delete(self, *args, **kwargs):
        bill_id = self.kickback_bill_id
        super().delete(*args, **kwargs)
        sync_kickback_total(bill_id)


def sync_kickback_total(bill_id) -> None:
    """A bill's kickback is whatever the Daily Book expenses linked to it add up to."""
    if not bill_id:
        return
    bill = KickbackEntry.objects.filter(pk=bill_id).first()
    if bill is None:
        return
    total = sum((e.amount for e in ExpenseEntry.objects.filter(kickback_bill_id=bill_id)), Decimal("0"))
    if bill.kickback_amount != total:
        bill.kickback_amount = total
        bill.save()


class PurchaseEntry(SheetAwareModelMixin, models.Model):
    objects = SheetAwareManager()

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="+")
    date = models.DateField(db_index=True)
    category = models.ForeignKey(
        Category, on_delete=models.SET_NULL, null=True, blank=True, related_name="purchase_entries"
    )
    subcategory = models.ForeignKey(
        Subcategory, on_delete=models.SET_NULL, null=True, blank=True, related_name="purchase_entries"
    )
    product_category = models.ForeignKey(
        Category, on_delete=models.SET_NULL, null=True, blank=True, related_name="purchases_as_product_category",
        limit_choices_to={"kind": "PRODUCT"},
    )
    vendor = models.CharField(max_length=150, blank=True)
    quantity = models.PositiveIntegerField(null=True, blank=True)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    payment_mode = models.CharField(max_length=10, choices=PaymentMode.choices, default=PaymentMode.CASH)
    bank_account = models.ForeignKey(
        BankAccount, on_delete=models.SET_NULL, null=True, blank=True, related_name="purchase_entries"
    )
    note = models.CharField(max_length=255, blank=True)
    created_by_email = models.CharField(max_length=254, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        base_manager_name = "objects"
        ordering = ["-date", "-created_at"]
        indexes = [models.Index(fields=["date"])]

    def __str__(self) -> str:
        return f"Purchase {self.date} — {self.amount}"


class Receivable(SheetAwareModelMixin, models.Model):
    """Money a customer owes the business — an invoice raised but not yet
    (fully) collected. Recording a payment against this creates a real
    SalesEntry for the amount collected, so cash/bank balances, P&L and the
    Cash Flow statement stay correct without any special-casing — this model
    is purely a tracking layer of what's still outstanding.
    """

    objects = SheetAwareManager()

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="+")
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name="receivables")
    product_category = models.ForeignKey(
        Category, on_delete=models.SET_NULL, null=True, blank=True, related_name="receivables",
        limit_choices_to={"kind": "PRODUCT"},
    )
    invoice_date = models.DateField(default=datetime.date.today)
    due_date = models.DateField()
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    amount_received = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    note = models.CharField(max_length=255, blank=True)
    created_by_email = models.CharField(max_length=254, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        base_manager_name = "objects"
        ordering = ["due_date", "-created_at"]
        indexes = [models.Index(fields=["due_date"])]

    def __str__(self) -> str:
        return f"Receivable {self.customer} — {self.amount}"

    @property
    def balance(self):
        return self.amount - self.amount_received

    @property
    def status(self) -> str:
        if self.amount_received >= self.amount:
            return "PAID"
        if self.amount_received > 0:
            return "PARTIAL"
        if self.due_date < datetime.date.today():
            return "OVERDUE"
        return "OPEN"


class Payable(SheetAwareModelMixin, models.Model):
    """Money the business owes a vendor — a bill received but not yet
    (fully) paid. Recording a payment against this creates a real
    PurchaseEntry for the amount paid, keeping cash/bank balances, P&L and
    the Cash Flow statement correct without any special-casing.
    """

    objects = SheetAwareManager()

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="+")
    vendor = models.CharField(max_length=150)
    product_category = models.ForeignKey(
        Category, on_delete=models.SET_NULL, null=True, blank=True, related_name="payables",
        limit_choices_to={"kind": "PRODUCT"},
    )
    bill_date = models.DateField(default=datetime.date.today)
    due_date = models.DateField()
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    amount_paid = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    note = models.CharField(max_length=255, blank=True)
    created_by_email = models.CharField(max_length=254, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        base_manager_name = "objects"
        ordering = ["due_date", "-created_at"]
        indexes = [models.Index(fields=["due_date"])]

    def __str__(self) -> str:
        return f"Payable {self.vendor} — {self.amount}"

    @property
    def balance(self):
        return self.amount - self.amount_paid

    @property
    def status(self) -> str:
        if self.amount_paid >= self.amount:
            return "PAID"
        if self.amount_paid > 0:
            return "PARTIAL"
        if self.due_date < datetime.date.today():
            return "OVERDUE"
        return "OPEN"


class CashTransfer(SheetAwareModelMixin, models.Model):
    """A movement of money between the cash-in-hand till and the bank
    account — a deposit or a withdrawal. Balance-neutral overall (it moves
    money between two asset accounts), so it never touches the P&L, only
    the cash-vs-bank split shown on the Balance Sheet and Daily Report.
    """

    objects = SheetAwareManager()

    class Direction(models.TextChoices):
        CASH_TO_BANK = "CASH_TO_BANK", "Cash deposited to bank"
        BANK_TO_CASH = "BANK_TO_CASH", "Cash withdrawn from bank"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="+")
    date = models.DateField(db_index=True)
    direction = models.CharField(max_length=20, choices=Direction.choices)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    bank_account = models.ForeignKey(
        BankAccount, on_delete=models.SET_NULL, null=True, blank=True, related_name="transfers",
        help_text="Which bank account this deposit/withdrawal moved to or from (optional)",
    )
    note = models.CharField(max_length=255, blank=True)
    created_by_email = models.CharField(max_length=254, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        base_manager_name = "objects"
        ordering = ["-date", "-created_at"]
        indexes = [models.Index(fields=["date"])]
        verbose_name = "Cash/bank transfer"

    def __str__(self) -> str:
        return f"{self.get_direction_display()} {self.date} — {self.amount}"


class Partner(SheetAwareModelMixin, models.Model):
    """A capital partner / co-owner who can put money into the business or
    take money out of it — distinct from a Vendor (money the business owes)
    or Customer (money owed to the business): this is equity, not a
    payable or receivable."""

    objects = SheetAwareManager()

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="+")
    name = models.CharField(max_length=150)
    phone = models.CharField(max_length=30, blank=True)
    opening_balance = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    opening_balance_as_on = models.DateField(default=datetime.date.today)
    is_active = models.BooleanField(default=True)

    class Meta:
        base_manager_name = "objects"
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["organization", "name"], name="finance_partner_org_name_uniq"),
        ]

    def __str__(self) -> str:
        return self.name


class PartnerTransaction(SheetAwareModelMixin, models.Model):
    """A partner investing capital into the business or withdrawing their
    capital from it. Real cash/bank movement — unlike CashTransfer (moves
    money between cash and bank, nets to zero) this changes the total
    cash+bank position — but it never touches the P&L, since it's an
    equity movement rather than revenue or an expense."""

    objects = SheetAwareManager()

    class Kind(models.TextChoices):
        INVESTMENT = "INVESTMENT", "Investment"
        WITHDRAWAL = "WITHDRAWAL", "Withdrawal"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="+")
    partner = models.ForeignKey(Partner, on_delete=models.PROTECT, related_name="transactions")
    date = models.DateField(default=datetime.date.today, db_index=True)
    kind = models.CharField(max_length=10, choices=Kind.choices)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    payment_mode = models.CharField(max_length=10, choices=PaymentMode.choices, default=PaymentMode.CASH)
    bank_account = models.ForeignKey(
        BankAccount, on_delete=models.SET_NULL, null=True, blank=True, related_name="partner_transactions"
    )
    note = models.CharField(max_length=255, blank=True)
    created_by_email = models.CharField(max_length=254, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        base_manager_name = "objects"
        ordering = ["-date", "-created_at"]
        indexes = [models.Index(fields=["date"])]

    def __str__(self) -> str:
        return f"{self.get_kind_display()} {self.date} — {self.partner} — {self.amount}"


class FinanceSettings(SheetAwareModelMixin, models.Model):
    """Singleton row (per tenant schema) holding report configuration and
    the opening balances the P&L / Balance Sheet / Cash Flow build on top of.

    `opening_balance` is the opening cash-in-hand position and
    `opening_bank_balance` the opening bank position, as of `opening_date`.
    Together they are treated as the opening owner's equity — i.e. the
    business is assumed to start with cash/bank funded by the owner and no
    other assets or liabilities. That keeps the balance sheet this module
    produces internally consistent without needing full double-entry
    bookkeeping.
    """

    objects = SheetAwareManager()

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="+", unique=True)
    fy_start_month = models.PositiveSmallIntegerField(default=4)
    opening_balance = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    opening_bank_balance = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    opening_date = models.DateField()
    # JSON object {"sale": {"category": "Channel", ...}, ...} of the
    # organization's own bulk-entry column names. Kept as text (not
    # JSONField) so the Sheets store can round-trip it; blank = defaults.
    bulk_column_labels = models.TextField(blank=True, default="")
    kickback_column_labels = models.TextField(blank=True, default="")
    # The category whose sub-categories and items are the hospital's
    # consulting doctors — the Kickbacks "Consulting doctor" dropdown reads it.
    kickback_doctor_category = models.ForeignKey(
        Category, on_delete=models.SET_NULL, null=True, blank=True, related_name="+",
        help_text="Category whose sub-categories and items are the consulting doctors",
    )
    # The sales category that IP billing revenue is booked under when no other
    # category is picked (IP admission receipts and insurance claim payments).
    # Appended last on purpose (Sheets column order).
    ip_revenue_category = models.ForeignKey(
        Category, on_delete=models.SET_NULL, null=True, blank=True, related_name="+",
        help_text="Sales category for IP billing revenue",
    )
    # JSON object {"date": "Bill Date", "channel": "Dept", ...} of which column
    # in the imported revenue file holds each field, remembered for next time.
    # Text, same as the column-label settings above. Appended last on purpose.
    sales_import_mapping = models.TextField(blank=True, default="")

    class Meta:
        base_manager_name = "objects"
        verbose_name = "Finance settings"
        verbose_name_plural = "Finance settings"

    def __str__(self) -> str:
        return f"Finance settings (FY starts month {self.fy_start_month})"


class KickbackEntry(SheetAwareModelMixin, models.Model):
    """One hospital bill tracked for the commission/kickback paid out
    against it — a Business/Business Drive plan exclusive (see
    finance:kickbacks). `net_amount` is always derived from
    final_amount - kickback_amount, the same pattern SalesEntry uses
    for its own derived `amount`.
    """

    objects = SheetAwareManager()

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="+")
    bill_number = models.CharField(max_length=50)
    admission_date = models.DateField()
    discharge_date = models.DateField(null=True, blank=True)
    amount_received = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    ref_date = models.DateField("Ref date", null=True, blank=True, help_text="Date the amount was paid")
    consulting_doctor = models.CharField("Consulting doctor", max_length=150, blank=True)
    consulting_doctor_item = models.ForeignKey(
        Subcategory, on_delete=models.SET_NULL, null=True, blank=True, related_name="kickback_bills",
        help_text="The hospital doctor, picked from the doctors' category set in Finance Settings (optional)",
    )
    # Every referrer on the bill, as one comma-separated list of names (a bill
    # can have more than one). Kept as text so Sheets-backed orgs round-trip it.
    referred_by = models.CharField(max_length=500, blank=True)
    final_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    kickback_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    net_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    created_by_email = models.CharField(max_length=254, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    patient_name = models.CharField(max_length=150, blank=True)
    patient_address = models.CharField(max_length=255, blank=True)
    # Insurance details. New fields go at the end of the model on purpose:
    # Sheets-backed tabs are laid out in model field order, so appending
    # keeps every existing column where it already is.
    class PayerType(models.TextChoices):
        CASH = "CASH", "Cash"
        INSURANCE = "INSURANCE", "Insurance"

    payer_type = models.CharField(max_length=12, choices=PayerType.choices, blank=True, default="")
    insurer_name = models.CharField("Insurance name", max_length=150, blank=True)
    policy_number = models.CharField(max_length=60, blank=True)
    preauth_number = models.CharField("Pre-authorisation no.", max_length=60, blank=True)
    # Split of the bill between the insurer and the patient. Both are 0 when
    # not recorded; when both are set they must add up to final_amount (see
    # KickbackEntryForm.clean).
    insurer_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    patient_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    # The hospital's own patient number (MRN / UMRN / UHID), so all of one
    # patient's bills can be found together. Also appended last on purpose.
    mrn = models.CharField("MRN / UMRN", max_length=40, blank=True)
    # Written off from the bill: final_amount = insurer_amount + patient_amount
    # + discount_amount when the split is recorded. Appended last on purpose.
    discount_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    # Admission details. Appended last on purpose (Sheets column order).
    class Gender(models.TextChoices):
        MALE = "M", "Male"
        FEMALE = "F", "Female"
        OTHER = "O", "Other"

    patient_phone = models.CharField("Phone", max_length=20, blank=True)
    patient_age = models.PositiveSmallIntegerField("Age", null=True, blank=True)
    patient_gender = models.CharField("Gender", max_length=1, choices=Gender.choices, blank=True, default="")
    ward = models.CharField("Ward / bed", max_length=60, blank=True)
    diagnosis = models.CharField("Diagnosis", max_length=255, blank=True)

    class Meta:
        base_manager_name = "objects"
        ordering = ["-admission_date", "-created_at"]
        indexes = [models.Index(fields=["admission_date"])]
        verbose_name = "Kickback entry"

    def __str__(self) -> str:
        return f"Bill {self.bill_number} — {self.final_amount}"

    @property
    def insurer_share(self) -> Decimal:
        """What the insurer pays: the bill amount, less the patient's share
        and any discount. Worked out, so it always matches the bill amount."""
        return max(self.final_amount - self.patient_amount - self.discount_amount, Decimal("0"))

    def save(self, *args, **kwargs):
        self.net_amount = self.final_amount - self.kickback_amount
        super().save(*args, **kwargs)


def sync_kickback_received(bill_id) -> None:
    """A bill's received total, last paid date and final amount all come from
    the Daily Book revenue entries linked to it. The final amount is the total
    of those entries; it is not typed in on the bill."""
    if not bill_id:
        return
    bill = KickbackEntry.objects.filter(pk=bill_id).first()
    if bill is None:
        return
    sales = list(SalesEntry.objects.filter(kickback_bill_id=bill_id))
    received = sum((s.amount for s in sales), Decimal("0"))
    latest = max((s.date for s in sales), default=None)
    if bill.amount_received != received or bill.ref_date != latest:
        bill.amount_received = received
        bill.insurer_amount = bill.insurer_share
        bill.ref_date = latest
        bill.save()


class InsuranceClaim(SheetAwareModelMixin, models.Model):
    """One pre-authorisation or claim sent to an insurer or TPA, tracked
    through to the money received. Optionally linked to the patient bill it
    belongs to (a pre-auth can be raised before the bill is final).

    `received_amount`, `deduction_amount` and `last_payment_date` are kept in
    step with the ClaimPayment rows by sync_claim_totals(); never edit them by
    hand.
    """

    objects = SheetAwareManager()

    class ClaimType(models.TextChoices):
        PREAUTH = "PREAUTH", "Pre-authorisation"
        CLAIM = "CLAIM", "Claim"

    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Not submitted"
        SUBMITTED = "SUBMITTED", "Submitted"
        QUERY = "QUERY", "Query raised"
        APPROVED = "APPROVED", "Approved"
        REJECTED = "REJECTED", "Rejected"
        SETTLED = "SETTLED", "Settled"

    # Statuses that still have money to chase. A rejected claim is closed
    # unless someone moves it back to an open status.
    OPEN_STATUSES = (Status.SUBMITTED, Status.QUERY, Status.APPROVED)

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="+")
    bill = models.ForeignKey(
        KickbackEntry, on_delete=models.SET_NULL, null=True, blank=True, related_name="insurance_claims",
    )
    claim_type = models.CharField(max_length=10, choices=ClaimType.choices, default=ClaimType.CLAIM)
    claim_number = models.CharField("Claim / reference no.", max_length=60, blank=True)
    patient_name = models.CharField(max_length=150, blank=True)
    insurer_name = models.CharField("Insurer", max_length=150, blank=True)
    tpa_name = models.CharField("TPA", max_length=150, blank=True)
    policy_number = models.CharField(max_length=60, blank=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.DRAFT)
    claimed_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    approved_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    received_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    deduction_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    submitted_date = models.DateField(null=True, blank=True)
    decision_date = models.DateField(null=True, blank=True)
    last_payment_date = models.DateField(null=True, blank=True)
    remarks = models.CharField("Remarks / query", max_length=500, blank=True)
    created_by_email = models.CharField(max_length=254, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        base_manager_name = "objects"
        ordering = ["-submitted_date", "-created_at"]
        verbose_name = "Insurance claim"

    def __str__(self) -> str:
        label = self.claim_number or self.get_claim_type_display()
        return f"{label} — {self.insurer_display}"

    @property
    def insurer_display(self) -> str:
        return self.tpa_name or self.insurer_name or "—"

    @property
    def outstanding_amount(self) -> Decimal:
        """Approved money not yet received or written off as a deduction."""
        return self.approved_amount - self.received_amount - self.deduction_amount

    @property
    def patient_share(self) -> Decimal:
        """The patient's share of the admission's bill. Zero when the claim has
        no admission."""
        return self.bill.patient_amount if self.bill_id else Decimal("0")

    @property
    def discount_amount(self) -> Decimal:
        """The admission's discount: what is left of its final bill after the
        insurer and patient shares. Zero when the claim has no admission."""
        return self.bill.discount_amount if self.bill_id else Decimal("0")

    @property
    def is_open(self) -> bool:
        return self.status in self.OPEN_STATUSES and self.outstanding_amount > 0

    def days_since_submitted(self, today=None) -> int | None:
        if not self.submitted_date:
            return None
        today = today or datetime.date.today()
        return (today - self.submitted_date).days


class ClaimPayment(SheetAwareModelMixin, models.Model):
    """Money received against a claim. An insurer often pays one claim in
    several transfers, and deducts some of it (TDS or disallowed items)."""

    objects = SheetAwareManager()

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="+")
    claim = models.ForeignKey(InsuranceClaim, on_delete=models.CASCADE, related_name="payments")
    date = models.DateField()
    amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    deduction = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    reference = models.CharField("UTR / cheque no.", max_length=60, blank=True)
    note = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    # The Daily Book revenue entry this money was booked as. Created with the
    # payment and removed with it. Appended last on purpose (Sheets column order).
    sales_entry = models.ForeignKey(
        SalesEntry, on_delete=models.SET_NULL, null=True, blank=True, related_name="claim_payments",
    )

    class Meta:
        base_manager_name = "objects"
        ordering = ["date", "created_at"]
        verbose_name = "Claim payment"

    def __str__(self) -> str:
        return f"{self.date} — received {self.amount}, deducted {self.deduction}"


def sync_claim_totals(claim_id) -> None:
    """Brings a claim's received/deduction totals and last payment date up to
    date with its payments. An open claim with an approved amount that is now
    fully paid becomes Settled; a settled claim that is owed money again (a
    payment was removed) goes back to Approved."""
    if not claim_id:
        return
    claim = InsuranceClaim.objects.filter(pk=claim_id).first()
    if claim is None:
        return
    payments = list(ClaimPayment.objects.filter(claim_id=claim_id))
    received = sum((p.amount for p in payments), Decimal("0"))
    deducted = sum((p.deduction for p in payments), Decimal("0"))
    latest = max((p.date for p in payments), default=None)
    claim.received_amount = received
    claim.deduction_amount = deducted
    claim.last_payment_date = latest
    settled = claim.approved_amount > 0 and claim.outstanding_amount <= 0
    if claim.status in InsuranceClaim.OPEN_STATUSES and settled:
        claim.status = InsuranceClaim.Status.SETTLED
    elif claim.status == InsuranceClaim.Status.SETTLED and claim.outstanding_amount > 0:
        claim.status = InsuranceClaim.Status.APPROVED
    claim.save()


class GeneralLedger(SheetAwareModelMixin, models.Model):
    """A free-standing ledger for any account the business wants to track
    on its own — not tied to a customer, vendor, cash or bank. Balance is
    opening_balance + sum(debit) - sum(credit) over its LedgerEntry rows."""

    objects = SheetAwareManager()

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="+")
    name = models.CharField(max_length=150)
    opening_balance = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    opening_date = models.DateField(default=datetime.date.today)
    profession = models.CharField(max_length=100, blank=True)
    village = models.CharField(max_length=100, blank=True)
    mandal = models.CharField(max_length=100, blank=True)
    district = models.CharField(max_length=100, blank=True)
    state = models.CharField(max_length=100, blank=True)
    note = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        base_manager_name = "objects"
        ordering = ["name"]
        verbose_name = "General ledger"

    def __str__(self) -> str:
        return self.name

    @property
    def address(self) -> str:
        return ", ".join(part for part in (self.village, self.mandal, self.district, self.state) if part)


class LedgerEntry(SheetAwareModelMixin, models.Model):
    objects = SheetAwareManager()

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="+")
    ledger = models.ForeignKey(GeneralLedger, on_delete=models.CASCADE, related_name="entries")
    date = models.DateField(db_index=True)
    particular = models.CharField(max_length=255, blank=True)
    debit = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    credit = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        base_manager_name = "objects"
        ordering = ["date", "created_at"]
        verbose_name = "Ledger entry"

    def __str__(self) -> str:
        return f"{self.ledger} {self.date} — Dr {self.debit} / Cr {self.credit}"
