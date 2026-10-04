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

from django.db import models

from apps.organizations.models import Organization
from apps.sheets_store.mixin import SheetAwareModelMixin
from apps.sheets_store.queryset import SheetAwareManager


class PaymentMode(models.TextChoices):
    CASH = "CASH", "Cash"
    BANK = "BANK", "Bank"


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
        super().save(*args, **kwargs)


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

    class Meta:
        base_manager_name = "objects"
        ordering = ["-date", "-created_at"]
        indexes = [models.Index(fields=["date"])]

    def __str__(self) -> str:
        return f"Expense {self.date} — {self.amount}"


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

    class Meta:
        base_manager_name = "objects"
        verbose_name = "Finance settings"
        verbose_name_plural = "Finance settings"

    def __str__(self) -> str:
        return f"Finance settings (FY starts month {self.fy_start_month})"
