import datetime
from decimal import Decimal

from django import forms

from .models import (
    BankAccount,
    BankChoices,
    CashTransfer,
    Category,
    Customer,
    ExpenseEntry,
    FinanceSettings,
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


class DateInput(forms.DateInput):
    input_type = "date"


class HistoricalWindowFormMixin:
    """Backend enforcement of the plan's historical-data limit — never
    relies on the frontend alone. Pass `min_date=` (the earliest date this
    org's plan allows, or None for no limit) when constructing the form;
    views resolve it via apps.billing.services.historical_window_start()
    and pass it down to every single-entry form, edit form, and bulk
    formset (via form_kwargs)."""

    def __init__(self, *args, min_date=None, **kwargs):
        self.min_date = min_date
        super().__init__(*args, **kwargs)

    def clean_date(self):
        date = self.cleaned_data["date"]
        # Editing an existing entry whose date isn't being changed should
        # never be blocked by a limit that came into effect after it was
        # created (the plan's window rolling forward over time, or a gap
        # freshly walled off by a late renewal) — only a genuinely NEW or
        # newly-backdated date gets checked. self.instance still holds the
        # pre-edit DB values here: ModelForm.full_clean() calls this before
        # _post_clean() rebuilds the instance from cleaned_data.
        unchanged = self.instance.pk and self.instance.date == date
        if self.min_date and date < self.min_date and not unchanged:
            raise forms.ValidationError(
                f"Historical data limit exceeded — your plan only allows entries from "
                f"{self.min_date:%d %b %Y} onward. Upgrade your plan for a longer history."
            )
        return date


class ClearBankAccountUnlessBankMixin:
    """A bank account only means something when the entry is actually paid
    via Bank — carrying one on a Cash entry would silently count it toward
    that account's ledger even though the money never touched it (see
    services.bank_account_balance_as_of, which filters by bank_account
    alone). Rather than error, this just drops it: switching "Via" back to
    Cash after picking a bank is a normal edit, not a mistake to block."""

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data.get("payment_mode") != PaymentMode.BANK:
            cleaned_data["bank_account"] = None
        return cleaned_data


class SalesEntryForm(ClearBankAccountUnlessBankMixin, HistoricalWindowFormMixin, forms.ModelForm):
    class Meta:
        model = SalesEntry
        fields = [
            "date", "channel", "subcategory", "customer", "quantity", "amount",
            "payment_mode", "bank_account", "note",
        ]
        labels = {
            "subcategory": "Sub-category (optional)",
            "quantity": "Qty (optional)",
            "bank_account": "Bank",
        }
        widgets = {
            "date": DateInput(),
            "note": forms.TextInput(attrs={"placeholder": "Optional note"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["date"].initial = self.initial.get("date", datetime.date.today())
        self.fields["channel"].queryset = Category.objects.filter(kind=Category.Kind.SALES, is_active=True)
        self.fields["channel"].required = False
        self.fields["subcategory"].queryset = Subcategory.objects.filter(
            is_active=True, category__kind=Category.Kind.SALES
        )
        self.fields["subcategory"].required = False
        self.fields["subcategory"].widget.attrs["data-subcategory-for"] = "channel"
        self.fields["customer"].queryset = Customer.objects.filter(is_active=True)
        self.fields["customer"].required = False
        self.fields["quantity"].required = False
        self.fields["quantity"].widget.attrs["placeholder"] = "Qty"
        self.fields["amount"].widget.attrs["placeholder"] = "0.00"
        self.fields["bank_account"].queryset = BankAccount.objects.filter(is_active=True)
        self.fields["bank_account"].required = False
        self.fields["bank_account"].empty_label = "— Bank —"


class ExpenseEntryForm(ClearBankAccountUnlessBankMixin, HistoricalWindowFormMixin, forms.ModelForm):
    class Meta:
        model = ExpenseEntry
        fields = ["date", "category", "subcategory", "amount", "payment_mode", "bank_account", "note"]
        labels = {"subcategory": "Detail — e.g. employee name (optional)", "bank_account": "Bank"}
        widgets = {
            "date": DateInput(),
            "note": forms.TextInput(attrs={"placeholder": "Optional note"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["date"].initial = self.initial.get("date", datetime.date.today())
        self.fields["category"].queryset = Category.objects.filter(kind=Category.Kind.EXPENSE, is_active=True)
        self.fields["subcategory"].queryset = Subcategory.objects.filter(
            is_active=True, category__kind=Category.Kind.EXPENSE
        )
        self.fields["subcategory"].required = False
        self.fields["subcategory"].widget.attrs["data-subcategory-for"] = "category"
        self.fields["amount"].widget.attrs["placeholder"] = "0.00"
        self.fields["bank_account"].queryset = BankAccount.objects.filter(is_active=True)
        self.fields["bank_account"].required = False
        self.fields["bank_account"].empty_label = "— Bank —"


class PurchaseEntryForm(ClearBankAccountUnlessBankMixin, HistoricalWindowFormMixin, forms.ModelForm):
    on_credit = forms.BooleanField(
        required=False,
        label="Not paid yet (on credit)",
        help_text="Adds this to the vendor's payable balance instead of logging an immediate purchase — "
                   "it becomes a purchase entry once paid off from Vendor Ledgers.",
    )

    class Meta:
        model = PurchaseEntry
        fields = [
            "date", "category", "subcategory", "vendor", "quantity", "amount",
            "payment_mode", "bank_account", "note",
        ]
        labels = {
            "subcategory": "Item / detail (optional)",
            "quantity": "Qty (optional)",
            "bank_account": "Bank",
        }
        widgets = {
            "date": DateInput(),
            "note": forms.TextInput(attrs={"placeholder": "Optional note"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["date"].initial = self.initial.get("date", datetime.date.today())

        vendor_names = list(Vendor.objects.filter(is_active=True).order_by("name").values_list("name", flat=True))
        current_vendor = getattr(self.instance, "vendor", "") or self.initial.get("vendor")
        if current_vendor and current_vendor not in vendor_names:
            vendor_names.append(current_vendor)
        self.fields["vendor"] = forms.ChoiceField(
            choices=[("", "---------")] + [(name, name) for name in vendor_names],
            required=False,
            label="Vendor",
        )

        self.fields["category"].queryset = Category.objects.filter(kind=Category.Kind.PURCHASE, is_active=True)
        self.fields["subcategory"].queryset = Subcategory.objects.filter(
            is_active=True, category__kind=Category.Kind.PURCHASE
        )
        self.fields["subcategory"].required = False
        self.fields["subcategory"].widget.attrs["data-subcategory-for"] = "category"
        self.fields["quantity"].required = False
        self.fields["quantity"].widget.attrs["placeholder"] = "Qty"
        self.fields["amount"].widget.attrs["placeholder"] = "0.00"
        self.fields["bank_account"].queryset = BankAccount.objects.filter(is_active=True)
        self.fields["bank_account"].required = False
        self.fields["bank_account"].empty_label = "— Bank —"

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data.get("on_credit") and not cleaned_data.get("vendor"):
            self.add_error("vendor", "Required to log this as on credit — a payable needs a vendor.")
        return cleaned_data


SalesEntryFormSet = forms.modelformset_factory(SalesEntry, form=SalesEntryForm, extra=5, can_delete=True)
ExpenseEntryFormSet = forms.modelformset_factory(ExpenseEntry, form=ExpenseEntryForm, extra=3, can_delete=True)
PurchaseEntryFormSet = forms.modelformset_factory(PurchaseEntry, form=PurchaseEntryForm, extra=3, can_delete=True)


class CashTransferForm(HistoricalWindowFormMixin, forms.ModelForm):
    class Meta:
        model = CashTransfer
        fields = ["date", "direction", "amount", "bank_account", "note"]
        labels = {"bank_account": "Bank account (optional)"}
        widgets = {
            "date": DateInput(),
            "note": forms.TextInput(attrs={"placeholder": "Optional note"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["date"].initial = self.initial.get("date", datetime.date.today())
        self.fields["amount"].widget.attrs["placeholder"] = "0.00"
        self.fields["bank_account"].queryset = BankAccount.objects.filter(is_active=True)
        self.fields["bank_account"].required = False
        self.fields["bank_account"].empty_label = "— Which bank? —"


MONTH_CHOICES = [
    (1, "January"), (2, "February"), (3, "March"), (4, "April"),
    (5, "May"), (6, "June"), (7, "July"), (8, "August"),
    (9, "September"), (10, "October"), (11, "November"), (12, "December"),
]


class FinanceSettingsForm(forms.ModelForm):
    fy_start_month = forms.ChoiceField(choices=MONTH_CHOICES)

    class Meta:
        model = FinanceSettings
        fields = ["fy_start_month", "opening_balance", "opening_bank_balance", "opening_date"]
        widgets = {"opening_date": DateInput()}
        labels = {
            "opening_balance": "Opening cash-in-hand balance",
            "opening_bank_balance": "Opening bank balance",
        }


GST_RATE_CHOICES = [
    (Decimal("0"), "0% (none)"),
    (Decimal("5"), "5%"),
    (Decimal("12"), "12%"),
    (Decimal("18"), "18%"),
    (Decimal("28"), "28%"),
]


class CategoryForm(forms.ModelForm):
    # Only rendered for SALES/PURCHASE categories (see categories.html) —
    # Expense/Product submissions carry no `gst_rate` at all, so empty_value
    # has to be a real Decimal, not TypedChoiceField's default '', or
    # full_clean() would reject Category.gst_rate (a DecimalField) with
    # "Enter a number" and silently fail to save those categories.
    gst_rate = forms.TypedChoiceField(
        choices=GST_RATE_CHOICES, coerce=Decimal, required=False, initial=Decimal("0"),
        empty_value=Decimal("0"),
        label="GST rate", help_text="Used by the GST Summary report — leave at 0% if this category isn't taxed.",
    )

    class Meta:
        model = Category
        fields = ["kind", "name", "gst_rate"]


class CategoryEditForm(forms.ModelForm):
    """Rename + GST rate — kind isn't editable after creation since it
    decides which forms/dropdowns a category shows up in."""

    # Same empty_value fix as CategoryForm — only rendered for SALES/
    # PURCHASE categories in edit_category.html.
    gst_rate = forms.TypedChoiceField(
        choices=GST_RATE_CHOICES, coerce=Decimal, required=False, initial=Decimal("0"),
        empty_value=Decimal("0"),
        label="GST rate", help_text="Used by the GST Summary report — leave at 0% if this category isn't taxed.",
    )

    class Meta:
        model = Category
        fields = ["name", "gst_rate"]


class SubcategoryForm(forms.ModelForm):
    class Meta:
        model = Subcategory
        fields = ["category", "parent", "name"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["category"].queryset = Category.objects.filter(is_active=True)
        self.fields["parent"].queryset = Subcategory.objects.filter(is_active=True, parent__isnull=True)
        self.fields["parent"].required = False


class SubcategoryEditForm(forms.ModelForm):
    """Rename only — which category it belongs to isn't editable after
    creation, matching CategoryEditForm."""

    class Meta:
        model = Subcategory
        fields = ["name"]


class CustomerForm(forms.ModelForm):
    class Meta:
        model = Customer
        fields = ["name", "phone", "email"]
        widgets = {
            "phone": forms.TextInput(attrs={"placeholder": "Optional"}),
            "email": forms.TextInput(attrs={"placeholder": "Optional"}),
        }


class VendorForm(forms.ModelForm):
    class Meta:
        model = Vendor
        fields = ["name", "phone", "details", "opening_balance", "opening_balance_as_on"]
        labels = {
            "phone": "Mobile number (optional)",
            "opening_balance": "Opening balance owed (optional)",
            "opening_balance_as_on": "Opening balance as on",
        }
        widgets = {
            "phone": forms.TextInput(attrs={"placeholder": "Optional"}),
            "details": forms.TextInput(attrs={"placeholder": "Address, notes (optional)"}),
            "opening_balance_as_on": DateInput(),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["phone"].required = False
        self.fields["details"].required = False
        self.fields["opening_balance"].required = False
        self.fields["opening_balance"].widget.attrs["placeholder"] = "0.00"
        self.fields["opening_balance_as_on"].initial = datetime.date.today()


class VendorEditForm(forms.ModelForm):
    class Meta:
        model = Vendor
        fields = ["name", "phone", "details", "opening_balance", "opening_balance_as_on"]
        labels = {
            "phone": "Mobile number (optional)",
            "opening_balance": "Opening balance owed",
            "opening_balance_as_on": "Opening balance as on",
        }
        widgets = {
            "phone": forms.TextInput(attrs={"placeholder": "Optional"}),
            "details": forms.TextInput(attrs={"placeholder": "Address, notes (optional)"}),
            "opening_balance_as_on": DateInput(),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["phone"].required = False
        self.fields["details"].required = False
        self.fields["opening_balance"].required = False


class BankAccountForm(forms.ModelForm):
    class Meta:
        model = BankAccount
        fields = [
            "name", "bank_name", "other_bank_name", "account_number",
            "opening_balance", "opening_balance_as_on",
        ]
        labels = {
            "name": "Account nickname",
            "bank_name": "Bank",
            "other_bank_name": "Bank name (if “Other”)",
            "account_number": "Account number (optional)",
            "opening_balance": "Opening balance (optional)",
            "opening_balance_as_on": "Opening balance as on",
        }
        widgets = {
            "name": forms.TextInput(attrs={"placeholder": "e.g. Current Account"}),
            "bank_name": forms.Select(attrs={"class": "sel-wide"}),
            "other_bank_name": forms.TextInput(attrs={"placeholder": "Bank name"}),
            "account_number": forms.TextInput(attrs={"placeholder": "Optional"}),
            "opening_balance_as_on": DateInput(),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["account_number"].required = False
        self.fields["other_bank_name"].required = False
        self.fields["opening_balance"].required = False
        self.fields["opening_balance"].widget.attrs["placeholder"] = "0.00"
        self.fields["opening_balance_as_on"].initial = datetime.date.today()

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data.get("bank_name") == BankChoices.OTHER and not cleaned_data.get("other_bank_name"):
            self.add_error("other_bank_name", "Required when the bank isn't in the list above.")
        return cleaned_data


class BankAccountEditForm(BankAccountForm):
    pass


class ReceivableForm(forms.ModelForm):
    class Meta:
        model = Receivable
        fields = ["customer", "invoice_date", "due_date", "amount", "note"]
        widgets = {
            "invoice_date": DateInput(),
            "due_date": DateInput(),
            "note": forms.TextInput(attrs={"placeholder": "Optional note"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["invoice_date"].initial = datetime.date.today()
        self.fields["customer"].queryset = Customer.objects.filter(is_active=True)
        self.fields["amount"].widget.attrs["placeholder"] = "0.00"


class PayableForm(forms.ModelForm):
    class Meta:
        model = Payable
        fields = ["vendor", "bill_date", "due_date", "amount", "note"]
        widgets = {
            "vendor": forms.TextInput(attrs={"placeholder": "Vendor / supplier"}),
            "bill_date": DateInput(),
            "due_date": DateInput(),
            "note": forms.TextInput(attrs={"placeholder": "Optional note"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["bill_date"].initial = datetime.date.today()
        self.fields["amount"].widget.attrs["placeholder"] = "0.00"


class _PaidInvoiceEditMixin:
    """Edit rules for a receivable/payable that may already have payments.

    Recording a payment also books a real sale/purchase entry and bumps a
    counter on the invoice, so a few things must stay put once money has
    moved: the counterparty (payments are tied to it), and the invoice total
    can't drop below what has already been settled."""

    paid_field = ""        # amount_received / amount_paid
    party_field = ""       # customer / vendor

    def _lock_when_paid(self):
        if self.instance.pk and getattr(self.instance, self.paid_field) > 0:
            self.fields[self.party_field].disabled = True
            self.fields[self.party_field].help_text = "Locked — payments are already recorded against this."

    def clean_amount(self):
        amount = self.cleaned_data["amount"]
        settled = getattr(self.instance, self.paid_field, 0) or 0
        if amount < settled:
            raise forms.ValidationError(
                f"Can't be less than the {settled} already settled against it."
            )
        return amount


class ReceivableEditForm(_PaidInvoiceEditMixin, ReceivableForm):
    paid_field = "amount_received"
    party_field = "customer"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Keep the current customer selectable even if they've since been paused.
        self.fields["customer"].queryset = Customer.objects.all()
        self._lock_when_paid()


class PayableEditForm(_PaidInvoiceEditMixin, PayableForm):
    paid_field = "amount_paid"
    party_field = "vendor"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._lock_when_paid()


class RecordPaymentForm(ClearBankAccountUnlessBankMixin, forms.Form):
    date = forms.DateField(widget=DateInput(), initial=datetime.date.today)
    amount = forms.DecimalField(max_digits=14, decimal_places=2, min_value=Decimal("0.01"))
    payment_mode = forms.ChoiceField(choices=PaymentMode.choices, initial=PaymentMode.CASH)
    bank_account = forms.ModelChoiceField(
        queryset=BankAccount.objects.filter(is_active=True), required=False, empty_label="— Bank —",
        label="Bank",
    )
    note = forms.CharField(max_length=255, required=False, widget=forms.TextInput(attrs={"placeholder": "Optional note"}))


class PartnerForm(forms.ModelForm):
    class Meta:
        model = Partner
        fields = ["name", "phone", "opening_balance", "opening_balance_as_on"]
        labels = {
            "opening_balance": "Opening balance invested (optional)",
            "opening_balance_as_on": "Opening balance as on",
        }
        widgets = {
            "phone": forms.TextInput(attrs={"placeholder": "Mobile number (optional)"}),
            "opening_balance_as_on": DateInput(),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["phone"].required = False
        self.fields["opening_balance"].required = False
        self.fields["opening_balance"].widget.attrs["placeholder"] = "0.00"


class PartnerTransactionForm(ClearBankAccountUnlessBankMixin, HistoricalWindowFormMixin, forms.ModelForm):
    class Meta:
        model = PartnerTransaction
        fields = ["partner", "date", "kind", "amount", "payment_mode", "bank_account", "note"]
        labels = {"bank_account": "Bank"}
        widgets = {
            "date": DateInput(),
            "note": forms.TextInput(attrs={"placeholder": "Optional note"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["date"].initial = self.initial.get("date", datetime.date.today())
        self.fields["partner"].queryset = Partner.objects.filter(is_active=True)
        self.fields["amount"].widget.attrs["placeholder"] = "0.00"
        self.fields["bank_account"].queryset = BankAccount.objects.filter(is_active=True)
        self.fields["bank_account"].required = False
        self.fields["bank_account"].empty_label = "— Bank —"

