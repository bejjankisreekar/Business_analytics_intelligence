"""Tenant-scoped models.

These tables are created once in `public` by the normal migration
(so the schema exists as a template), then cloned into every
organization's own PostgreSQL schema — see
apps/organizations/tenant.py. At request time, TenantSchemaMiddleware
points the DB connection's search_path at the current user's
organization schema, so every query here transparently reads and
writes that organization's own isolated copy of these tables.

There is deliberately no `organization` foreign key on any model in
this file: isolation comes from the schema itself, not a column.
"""
import datetime
import uuid

from django.db import models


class PaymentMode(models.TextChoices):
    CASH = "CASH", "Cash"
    BANK = "BANK", "Bank"


class Category(models.Model):
    class Kind(models.TextChoices):
        SALES = "SALES", "Revenue channel"
        EXPENSE = "EXPENSE", "Expense category"
        PURCHASE = "PURCHASE", "Purchase category"
        PRODUCT = "PRODUCT", "Product category"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    kind = models.CharField(max_length=10, choices=Kind.choices)
    name = models.CharField(max_length=100)
    is_active = models.BooleanField(default=True)
    gst_rate = models.DecimalField(
        "GST rate %", max_digits=5, decimal_places=2, default=0,
        help_text="Applied to every sale/purchase logged under this category, for the GST Summary report.",
    )

    class Meta:
        ordering = ["kind", "name"]
        constraints = [
            models.UniqueConstraint(fields=["kind", "name"], name="finance_category_kind_name_uniq"),
        ]

    def __str__(self) -> str:
        return self.name


class Subcategory(models.Model):
    """A specific item under a Category — an employee under 'Salaries &
    Wages', a brand under 'New Phone Sales', a vendor under a purchase
    category. Optional on every entry: pick a Category alone for a quick
    entry, or drill into a Subcategory when the extra detail is worth it.

    `parent` lets a Subcategory itself have children — e.g. Category "OPD
    Consultation" → Subcategory "Cardiology" → child Subcategory "Dr. Rao"
    — for cases where a single level of detail isn't enough. Left null for
    an ordinary (single-level) Subcategory.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    category = models.ForeignKey(Category, on_delete=models.CASCADE, related_name="subcategories")
    parent = models.ForeignKey(
        "self", on_delete=models.CASCADE, null=True, blank=True, related_name="children"
    )
    name = models.CharField(max_length=100)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["category", "name"]
        verbose_name_plural = "Subcategories"
        constraints = [
            models.UniqueConstraint(
                fields=["category", "parent", "name"], name="finance_subcategory_category_parent_name_uniq"
            ),
        ]

    def __str__(self) -> str:
        if self.parent_id:
            return f"{self.category.name} → {self.parent.name} → {self.name}"
        return f"{self.category.name} → {self.name}"


class Customer(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=150)
    phone = models.CharField(max_length=30, blank=True)
    email = models.CharField(max_length=254, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["name"], name="finance_customer_name_uniq"),
        ]

    def __str__(self) -> str:
        return self.name


class Vendor(models.Model):
    """A supplier directory entry. `opening_balance` is what was already
    owed to this vendor before using the system — set on creation it seeds
    a matching Payable so it shows up in Cash Position immediately, the
    same way FinanceSettings.opening_balance seeds the opening cash
    position. Purchase/Payable vendor fields stay free text for quick
    logging; this is a separate, optional directory for tracking details."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=150)
    phone = models.CharField(max_length=30, blank=True)
    details = models.CharField(max_length=255, blank=True)
    opening_balance = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    opening_balance_as_on = models.DateField(default=datetime.date.today)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["name"], name="finance_vendor_name_uniq"),
        ]

    def __str__(self) -> str:
        return self.name


class SalesEntry(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
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
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    payment_mode = models.CharField(max_length=10, choices=PaymentMode.choices, default=PaymentMode.CASH)
    note = models.CharField(max_length=255, blank=True)
    created_by_email = models.CharField(max_length=254, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date", "-created_at"]
        indexes = [models.Index(fields=["date"])]

    def __str__(self) -> str:
        return f"Sale {self.date} — {self.amount}"


class ExpenseEntry(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    date = models.DateField(db_index=True)
    category = models.ForeignKey(
        Category, on_delete=models.SET_NULL, null=True, blank=True, related_name="expense_entries"
    )
    subcategory = models.ForeignKey(
        Subcategory, on_delete=models.SET_NULL, null=True, blank=True, related_name="expense_entries"
    )
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    payment_mode = models.CharField(max_length=10, choices=PaymentMode.choices, default=PaymentMode.CASH)
    note = models.CharField(max_length=255, blank=True)
    created_by_email = models.CharField(max_length=254, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date", "-created_at"]
        indexes = [models.Index(fields=["date"])]

    def __str__(self) -> str:
        return f"Expense {self.date} — {self.amount}"


class PurchaseEntry(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
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
    note = models.CharField(max_length=255, blank=True)
    created_by_email = models.CharField(max_length=254, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date", "-created_at"]
        indexes = [models.Index(fields=["date"])]

    def __str__(self) -> str:
        return f"Purchase {self.date} — {self.amount}"


class Receivable(models.Model):
    """Money a customer owes the business — an invoice raised but not yet
    (fully) collected. Recording a payment against this creates a real
    SalesEntry for the amount collected, so cash/bank balances, P&L and the
    Cash Flow statement stay correct without any special-casing — this model
    is purely a tracking layer of what's still outstanding.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
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


class Payable(models.Model):
    """Money the business owes a vendor — a bill received but not yet
    (fully) paid. Recording a payment against this creates a real
    PurchaseEntry for the amount paid, keeping cash/bank balances, P&L and
    the Cash Flow statement correct without any special-casing.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
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


class CashTransfer(models.Model):
    """A movement of money between the cash-in-hand till and the bank
    account — a deposit or a withdrawal. Balance-neutral overall (it moves
    money between two asset accounts), so it never touches the P&L, only
    the cash-vs-bank split shown on the Balance Sheet and Daily Report.
    """

    class Direction(models.TextChoices):
        CASH_TO_BANK = "CASH_TO_BANK", "Cash deposited to bank"
        BANK_TO_CASH = "BANK_TO_CASH", "Cash withdrawn from bank"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    date = models.DateField(db_index=True)
    direction = models.CharField(max_length=20, choices=Direction.choices)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    note = models.CharField(max_length=255, blank=True)
    created_by_email = models.CharField(max_length=254, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date", "-created_at"]
        indexes = [models.Index(fields=["date"])]
        verbose_name = "Cash/bank transfer"

    def __str__(self) -> str:
        return f"{self.get_direction_display()} {self.date} — {self.amount}"


class Partner(models.Model):
    """A capital partner / co-owner who can put money into the business or
    take money out of it — distinct from a Vendor (money the business owes)
    or Customer (money owed to the business): this is equity, not a
    payable or receivable."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=150)
    phone = models.CharField(max_length=30, blank=True)
    opening_balance = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    opening_balance_as_on = models.DateField(default=datetime.date.today)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["name"], name="finance_partner_name_uniq"),
        ]

    def __str__(self) -> str:
        return self.name


class PartnerTransaction(models.Model):
    """A partner investing capital into the business or withdrawing their
    capital from it. Real cash/bank movement — unlike CashTransfer (moves
    money between cash and bank, nets to zero) this changes the total
    cash+bank position — but it never touches the P&L, since it's an
    equity movement rather than revenue or an expense."""

    class Kind(models.TextChoices):
        INVESTMENT = "INVESTMENT", "Investment"
        WITHDRAWAL = "WITHDRAWAL", "Withdrawal"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    partner = models.ForeignKey(Partner, on_delete=models.PROTECT, related_name="transactions")
    date = models.DateField(default=datetime.date.today, db_index=True)
    kind = models.CharField(max_length=10, choices=Kind.choices)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    payment_mode = models.CharField(max_length=10, choices=PaymentMode.choices, default=PaymentMode.CASH)
    note = models.CharField(max_length=255, blank=True)
    created_by_email = models.CharField(max_length=254, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date", "-created_at"]
        indexes = [models.Index(fields=["date"])]

    def __str__(self) -> str:
        return f"{self.get_kind_display()} {self.date} — {self.partner} — {self.amount}"


class FinanceSettings(models.Model):
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

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    fy_start_month = models.PositiveSmallIntegerField(default=4)
    opening_balance = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    opening_bank_balance = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    opening_date = models.DateField()

    class Meta:
        verbose_name = "Finance settings"
        verbose_name_plural = "Finance settings"

    def __str__(self) -> str:
        return f"Finance settings (FY starts month {self.fy_start_month})"
