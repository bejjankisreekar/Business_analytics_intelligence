import datetime
from decimal import Decimal

from django import forms

from .models import (
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


class SalesEntryForm(HistoricalWindowFormMixin, forms.ModelForm):
    class Meta:
        model = SalesEntry
        fields = [
            "date", "channel", "subcategory", "product_category", "customer", "quantity", "amount",
            "payment_mode", "note",
        ]
        labels = {
            "subcategory": "Brand / product (optional)",
            "product_category": "Product category (optional)",
            "quantity": "Qty (optional)",
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
        self.fields["product_category"].queryset = Category.objects.filter(
            kind=Category.Kind.PRODUCT, is_active=True
        )
        self.fields["product_category"].required = False
        self.fields["customer"].queryset = Customer.objects.filter(is_active=True)
        self.fields["customer"].required = False
        self.fields["quantity"].required = False
        self.fields["quantity"].widget.attrs["placeholder"] = "Qty"
        self.fields["amount"].widget.attrs["placeholder"] = "0.00"


class ExpenseEntryForm(HistoricalWindowFormMixin, forms.ModelForm):
    class Meta:
        model = ExpenseEntry
        fields = ["date", "category", "subcategory", "amount", "payment_mode", "note"]
        labels = {"subcategory": "Detail — e.g. employee name (optional)"}
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


class PurchaseEntryForm(HistoricalWindowFormMixin, forms.ModelForm):
    on_credit = forms.BooleanField(
        required=False,
        label="Not paid yet (on credit)",
        help_text="Adds this to the vendor's payable balance instead of logging an immediate purchase — "
                   "it becomes a purchase entry once paid off from Vendor Ledgers.",
    )

    class Meta:
        model = PurchaseEntry
        fields = [
            "date", "category", "subcategory", "product_category", "vendor", "quantity", "amount",
            "payment_mode", "note",
        ]
        labels = {
            "subcategory": "Item / detail (optional)",
            "product_category": "Product category (optional)",
            "quantity": "Qty (optional)",
        }
        widgets = {
            "date": DateInput(),
            "vendor": forms.TextInput(attrs={"placeholder": "Vendor / supplier"}),
            "note": forms.TextInput(attrs={"placeholder": "Optional note"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["date"].initial = self.initial.get("date", datetime.date.today())
        self.fields["category"].queryset = Category.objects.filter(kind=Category.Kind.PURCHASE, is_active=True)
        self.fields["subcategory"].queryset = Subcategory.objects.filter(
            is_active=True, category__kind=Category.Kind.PURCHASE
        )
        self.fields["subcategory"].required = False
        self.fields["subcategory"].widget.attrs["data-subcategory-for"] = "category"
        self.fields["product_category"].queryset = Category.objects.filter(
            kind=Category.Kind.PRODUCT, is_active=True
        )
        self.fields["product_category"].required = False
        self.fields["quantity"].required = False
        self.fields["quantity"].widget.attrs["placeholder"] = "Qty"
        self.fields["amount"].widget.attrs["placeholder"] = "0.00"

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data.get("on_credit") and not cleaned_data.get("vendor"):
            self.add_error("vendor", "Required to log this as on credit — a payable needs a vendor.")
        return cleaned_data


SalesEntryFormSet = forms.modelformset_factory(SalesEntry, form=SalesEntryForm, extra=5)
ExpenseEntryFormSet = forms.modelformset_factory(ExpenseEntry, form=ExpenseEntryForm, extra=3)
PurchaseEntryFormSet = forms.modelformset_factory(PurchaseEntry, form=PurchaseEntryForm, extra=3)


class CashTransferForm(HistoricalWindowFormMixin, forms.ModelForm):
    class Meta:
        model = CashTransfer
        fields = ["date", "direction", "amount", "note"]
        widgets = {
            "date": DateInput(),
            "note": forms.TextInput(attrs={"placeholder": "Optional note"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["date"].initial = self.initial.get("date", datetime.date.today())
        self.fields["amount"].widget.attrs["placeholder"] = "0.00"


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


class CategoryForm(forms.ModelForm):
    class Meta:
        model = Category
        fields = ["kind", "name"]


class CategoryEditForm(forms.ModelForm):
    """Rename only — kind isn't editable after creation since it decides
    which forms/dropdowns a category shows up in."""

    class Meta:
        model = Category
        fields = ["name"]


class SubcategoryForm(forms.ModelForm):
    class Meta:
        model = Subcategory
        fields = ["category", "name"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["category"].queryset = Category.objects.filter(is_active=True)


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


class ReceivableForm(forms.ModelForm):
    class Meta:
        model = Receivable
        fields = ["customer", "product_category", "invoice_date", "due_date", "amount", "note"]
        labels = {"product_category": "Product category (optional)"}
        widgets = {
            "invoice_date": DateInput(),
            "due_date": DateInput(),
            "note": forms.TextInput(attrs={"placeholder": "Optional note"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["invoice_date"].initial = datetime.date.today()
        self.fields["customer"].queryset = Customer.objects.filter(is_active=True)
        self.fields["product_category"].queryset = Category.objects.filter(
            kind=Category.Kind.PRODUCT, is_active=True
        )
        self.fields["product_category"].required = False
        self.fields["amount"].widget.attrs["placeholder"] = "0.00"


class PayableForm(forms.ModelForm):
    class Meta:
        model = Payable
        fields = ["vendor", "product_category", "bill_date", "due_date", "amount", "note"]
        labels = {"product_category": "Product category (optional)"}
        widgets = {
            "vendor": forms.TextInput(attrs={"placeholder": "Vendor / supplier"}),
            "bill_date": DateInput(),
            "due_date": DateInput(),
            "note": forms.TextInput(attrs={"placeholder": "Optional note"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["bill_date"].initial = datetime.date.today()
        self.fields["product_category"].queryset = Category.objects.filter(
            kind=Category.Kind.PRODUCT, is_active=True
        )
        self.fields["product_category"].required = False
        self.fields["amount"].widget.attrs["placeholder"] = "0.00"


class RecordPaymentForm(forms.Form):
    date = forms.DateField(widget=DateInput(), initial=datetime.date.today)
    amount = forms.DecimalField(max_digits=14, decimal_places=2, min_value=Decimal("0.01"))
    payment_mode = forms.ChoiceField(choices=PaymentMode.choices, initial=PaymentMode.CASH)
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


class PartnerTransactionForm(HistoricalWindowFormMixin, forms.ModelForm):
    class Meta:
        model = PartnerTransaction
        fields = ["partner", "date", "kind", "amount", "payment_mode", "note"]
        widgets = {
            "date": DateInput(),
            "note": forms.TextInput(attrs={"placeholder": "Optional note"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["date"].initial = self.initial.get("date", datetime.date.today())
        self.fields["partner"].queryset = Partner.objects.filter(is_active=True)
        self.fields["amount"].widget.attrs["placeholder"] = "0.00"
