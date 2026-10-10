import datetime
from decimal import Decimal

from django import forms
from django.db.models import QuerySet

from apps.core.widgets import SearchableChoiceWidget, SearchableModelChoiceWidget

from . import services
from .models import (
    BankAccount,
    BankChoices,
    CashTransfer,
    Category,
    Customer,
    ExpenseEntry,
    FinanceSettings,
    GeneralLedger,
    KickbackEntry,
    LedgerEntry,
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


def _unfetched(model):
    """A placeholder queryset for a ModelChoiceField declared on a form
    class body, when __init__ always overrides it with the real, per-
    request queryset anyway (every field below does). Going through
    `model.objects` (SheetAwareManager) here instead would eagerly fetch
    and decode that model's entire tab the moment this module is first
    imported in a process — for a GOOGLE_SHEETS org, Model.objects is
    eagerly evaluated (not lazy) the instant a queryset is built, so
    whichever org's session happened to be active at that first import
    pays a real Sheets API round trip for a value no one ever uses. This
    bypasses the custom manager entirely, so it can't touch Sheets or
    Postgres at all."""
    return QuerySet(model=model).none()


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


class RequireCategoryMixin:
    """Category is required going forward — an entry with no category can't
    be reported on correctly (it's silently missing from every category
    breakdown). Set `category_field` to the model field name ("channel" for
    Sales, "category" for Expense/Purchase) and `amount_field` to the main
    amount field ("gross_amount" for Sales, "amount" for Expense/Purchase).

    An already-uncategorized row from before this was enforced can still be
    re-saved without being forced to fix it right now — only a genuinely new
    row, or an existing row whose category is being actively cleared to
    blank, gets blocked."""

    category_field = ""
    amount_field = ""

    def clean(self):
        cleaned_data = super().clean()
        value = cleaned_data.get(self.category_field)
        if not value:
            original_id = getattr(self.instance, f"{self.category_field}_id", None)
            already_blank = not self.instance._state.adding and original_id is None
            if not already_blank:
                self.add_error(self.category_field, "Required — pick a category so this entry is reported on correctly.")
        return cleaned_data

    def has_changed(self):
        # A Bulk Entry extra row is "blank" (and so skipped by the formset,
        # per Django's empty_permitted handling) based on has_changed() —
        # which by default is True the moment ANY field differs from its
        # initial, including ones that carry no real data (e.g. the Via
        # dropdown having a non-blank default, or a stray click on Qty/Bank).
        # That turned an untouched-looking row into one that demands
        # Category/Amount just because the user brushed past an unrelated
        # field. What actually makes a row worth saving is Category or
        # Amount having something in it — everything else is just
        # formatting for that real entry, so only those two fields decide
        # whether this row counts as changed at all.
        if self.is_bound and self.category_field and self.amount_field:
            category_filled = bool(self[self.category_field].value())
            amount_filled = bool(self[self.amount_field].value())
            if not category_filled and not amount_filled:
                return False
        return super().has_changed()


class KickbackBillChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, bill):
        parts = [bill.bill_number, bill.patient_name, bill.consulting_doctor, bill.admission_date.strftime("%d %b %Y")]
        return " · ".join(part for part in parts if part)


class DoctorChoiceField(forms.ModelChoiceField):
    """"Consulting doctor" option: the doctor's name, prefixed by the sub-category it sits under."""

    def label_from_instance(self, doctor):
        return f"{doctor.parent.name} · {doctor.name}" if doctor.parent_id else doctor.name


def _ledger_label(ledger) -> str:
    """name - profession - village, skipping any part that's blank."""
    parts = [ledger.name, ledger.profession, ledger.village]
    return " - ".join(part for part in parts if part)


class GeneralLedgerChoiceField(forms.ModelChoiceField):
    """"Paid to" option: name - profession - village."""

    def label_from_instance(self, ledger):
        return _ledger_label(ledger)


class ReferrerChoiceField(forms.ModelMultipleChoiceField):
    """"Referred by" option: name - profession - village."""

    def label_from_instance(self, ledger):
        return _ledger_label(ledger)


class SalesEntryForm(RequireCategoryMixin, ClearBankAccountUnlessBankMixin, HistoricalWindowFormMixin, forms.ModelForm):
    category_field = "channel"
    amount_field = "gross_amount"

    channel = forms.ModelChoiceField(queryset=_unfetched(Category), required=False)
    subcategory = forms.ModelChoiceField(queryset=_unfetched(Subcategory), required=False, label="Sub-category (optional)")
    customer = forms.ModelChoiceField(queryset=_unfetched(Customer), required=False)
    bank_account = forms.ModelChoiceField(queryset=_unfetched(BankAccount), required=False, label="Bank")

    class Meta:
        model = SalesEntry
        fields = [
            "date", "channel", "subcategory", "customer", "quantity", "gross_amount", "discount",
            "payment_mode", "bank_account", "note",
        ]
        labels = {
            "subcategory": "Sub-category (optional)",
            "quantity": "Qty (optional)",
            "gross_amount": "Gross amount",
            "discount": "Discount (optional)",
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
        # Both fields carry a model-level default=0 (so a fresh SalesEntry
        # created without discount info still gets a sane gross/discount) —
        # but that default becomes this form field's fallback "initial" for
        # has_changed() comparisons, which makes a genuinely untouched blank
        # Bulk Entry row look "changed" (0 vs "") and forces full validation
        # on rows the user never filled in. Clearing it here restores the
        # normal blank-row-is-ignored behavior the other fields already have.
        self.fields["gross_amount"].initial = None
        self.fields["gross_amount"].widget.attrs["placeholder"] = "0.00"
        self.fields["discount"].initial = None
        self.fields["discount"].required = False
        self.fields["discount"].widget.attrs["placeholder"] = "0.00"
        self.fields["bank_account"].queryset = BankAccount.objects.filter(is_active=True)
        self.fields["bank_account"].required = False
        self.fields["bank_account"].empty_label = "— Bank —"

    def clean(self):
        cleaned_data = super().clean()
        gross = cleaned_data.get("gross_amount")
        discount = cleaned_data.get("discount") or Decimal("0")
        if gross is not None and discount > gross:
            self.add_error("discount", "Can't exceed the gross amount.")
        return cleaned_data


class ExpenseEntryForm(RequireCategoryMixin, ClearBankAccountUnlessBankMixin, HistoricalWindowFormMixin, forms.ModelForm):
    category_field = "category"
    amount_field = "amount"

    category = forms.ModelChoiceField(queryset=_unfetched(Category))
    subcategory = forms.ModelChoiceField(
        queryset=_unfetched(Subcategory), required=False, label="Detail — e.g. employee name (optional)"
    )
    bank_account = forms.ModelChoiceField(queryset=_unfetched(BankAccount), required=False, label="Bank")
    paid_to_ledger = GeneralLedgerChoiceField(queryset=_unfetched(GeneralLedger), required=False, label="Paid to")
    kickback_bill = KickbackBillChoiceField(queryset=_unfetched(KickbackEntry), required=False, label="Kickback bill")

    class Meta:
        model = ExpenseEntry
        fields = [
            "date", "category", "subcategory", "amount", "payment_mode", "bank_account", "note",
            "paid_to_ledger", "kickback_bill",
        ]
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
        self.fields["paid_to_ledger"].queryset = GeneralLedger.objects.all()
        self.fields["paid_to_ledger"].empty_label = "— Paid to —"
        self.fields["kickback_bill"].queryset = KickbackEntry.objects.all()
        self.fields["kickback_bill"].empty_label = "— Kickback bill —"


class PurchaseEntryForm(RequireCategoryMixin, ClearBankAccountUnlessBankMixin, HistoricalWindowFormMixin, forms.ModelForm):
    category_field = "category"
    amount_field = "amount"

    category = forms.ModelChoiceField(queryset=_unfetched(Category))
    subcategory = forms.ModelChoiceField(
        queryset=_unfetched(Subcategory), required=False, label="Item / detail (optional)"
    )
    bank_account = forms.ModelChoiceField(queryset=_unfetched(BankAccount), required=False, label="Bank")

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
            widget=SearchableChoiceWidget(search_placeholder="Search vendor...")
        )

        self.fields["category"].queryset = Category.objects.filter(kind=Category.Kind.PURCHASE, is_active=True)
        self.fields["subcategory"].queryset = Subcategory.objects.filter(
            is_active=True, category__kind=Category.Kind.PURCHASE
        )
        self.fields["subcategory"].required = False
        self.fields["subcategory"].widget.attrs["data-subcategory-for"] = "category"
        self.fields["quantity"].required = False
        self.fields["amount"].widget.attrs["placeholder"] = "0.00"
        self.fields["bank_account"].queryset = BankAccount.objects.filter(is_active=True)
        self.fields["bank_account"].required = False
        self.fields["bank_account"].empty_label = "— Bank —"


SalesEntryFormSet = forms.modelformset_factory(SalesEntry, form=SalesEntryForm, extra=5, can_delete=True)
ExpenseEntryFormSet = forms.modelformset_factory(ExpenseEntry, form=ExpenseEntryForm, extra=3, can_delete=True)
PurchaseEntryFormSet = forms.modelformset_factory(PurchaseEntry, form=PurchaseEntryForm, extra=3, can_delete=True)


def sales_import_formset(extra: int):
    """A SalesEntryFormSet sized to hold exactly `extra` unsaved rows — used
    by the CSV/Excel import review screen, where every row comes from the
    uploaded file's `initial=` data rather than a handful of blank ones."""
    return forms.modelformset_factory(SalesEntry, form=SalesEntryForm, extra=extra, can_delete=True)


class CashTransferForm(HistoricalWindowFormMixin, forms.ModelForm):
    bank_account = forms.ModelChoiceField(
        queryset=_unfetched(BankAccount), required=False, label="Bank account (optional)"
    )

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
        self.fields["direction"].widget.attrs["class"] = "sel-wide"
        self.fields["bank_account"].queryset = BankAccount.objects.filter(is_active=True)
        self.fields["bank_account"].required = False
        self.fields["bank_account"].empty_label = "— Which bank? —"
        self.fields["bank_account"].widget.attrs["class"] = "sel-wide"


MONTH_CHOICES = [
    (1, "January"), (2, "February"), (3, "March"), (4, "April"),
    (5, "May"), (6, "June"), (7, "July"), (8, "August"),
    (9, "September"), (10, "October"), (11, "November"), (12, "December"),
]


class FinanceSettingsForm(forms.ModelForm):
    fy_start_month = forms.ChoiceField(choices=MONTH_CHOICES, widget=SearchableChoiceWidget(search_placeholder="Search month..."))

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
        widget=SearchableChoiceWidget(search_placeholder="Search rate...")
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
        widget=SearchableChoiceWidget(search_placeholder="Search rate...")
    )

    class Meta:
        model = Category
        fields = ["name", "gst_rate"]


class SubcategoryForm(forms.ModelForm):
    category = forms.ModelChoiceField(queryset=_unfetched(Category))
    parent = forms.ModelChoiceField(queryset=_unfetched(Subcategory), required=False)

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


class KickbackEntryForm(forms.ModelForm):
    class Meta:
        model = KickbackEntry
        fields = [
            "bill_number", "patient_name", "patient_address", "admission_date", "discharge_date",
            "consulting_doctor_item", "final_amount",
        ]
        labels = {
            "bill_number": "Bill number",
            "patient_name": "Patient name",
            "patient_address": "Address",
            "admission_date": "Admission date",
            "discharge_date": "Discharge date (optional)",
            "consulting_doctor_item": "Consulting doctor",
            "final_amount": "Final amount",
        }
        widgets = {
            "admission_date": DateInput(),
            "discharge_date": DateInput(),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # The doctor dropdown lists the items under the category mapped in
        # Finance Settings; with no mapping yet it stays empty.
        self.doctor_category = services.get_finance_settings().kickback_doctor_category
        doctors = Subcategory.objects.none()
        if self.doctor_category:
            doctors = Subcategory.objects.filter(category=self.doctor_category, is_active=True).select_related("parent")
        self.fields["consulting_doctor_item"] = DoctorChoiceField(queryset=doctors, required=False, label="Consulting doctor")
        # "Referred by" is a checklist of ledgers — a bill can have several.
        # Names on an older bill that match no ledger are kept as typed.
        self.fields["referred_by_names"] = ReferrerChoiceField(
            queryset=GeneralLedger.objects.all(), required=False, label="Referred by",
            widget=forms.CheckboxSelectMultiple,
        )
        typed = [name.strip() for name in self.instance.referred_by.split(",") if name.strip()]
        ledgers = {ledger.name: ledger.pk for ledger in GeneralLedger.objects.filter(name__in=typed)}
        self.fields["referred_by_names"].initial = [ledgers[name] for name in typed if name in ledgers]
        self._typed_referrers = [name for name in typed if name not in ledgers]
        # A bill saved before the dropdown existed keeps its typed doctor name
        # until someone picks a doctor or clears a linked one.
        self._was_linked = self.instance.consulting_doctor_item_id is not None
        self.fields["discharge_date"].required = False
        self.fields["patient_name"].required = False
        self.fields["patient_address"].required = False
        self.fields["final_amount"].required = False
        self.fields["final_amount"].widget.attrs["placeholder"] = "0.00"

    def save(self, commit=True):
        entry = super().save(commit=False)
        doctor = self.cleaned_data.get("consulting_doctor_item")
        if doctor:
            entry.consulting_doctor = doctor.name
        elif self._was_linked:
            entry.consulting_doctor = ""
        referrers = [ledger.name for ledger in self.cleaned_data.get("referred_by_names") or []]
        entry.referred_by = ", ".join(referrers + self._typed_referrers)
        if commit:
            entry.save()
        return entry


class GeneralLedgerForm(forms.ModelForm):
    class Meta:
        model = GeneralLedger
        fields = [
            "name", "profession", "opening_balance", "opening_date",
            "village", "mandal", "district", "state", "note",
        ]
        labels = {
            "note": "Note",
            "name": "Ledger name",
            "profession": "Profession",
            "opening_balance": "Opening balance",
            "opening_date": "Opening balance as on",
            "village": "Village",
            "mandal": "Mandal",
            "district": "District",
            "state": "State",
        }
        widgets = {
            "opening_date": DateInput(),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ("profession", "opening_balance", "village", "mandal", "district", "state", "note"):
            self.fields[name].required = False
        self.fields["opening_balance"].widget.attrs["placeholder"] = "0.00"
        self.fields["opening_date"].initial = datetime.date.today()


class LedgerEntryForm(forms.ModelForm):
    class Meta:
        model = LedgerEntry
        fields = ["date", "particular", "debit", "credit"]
        labels = {"particular": "Particulars", "debit": "Debit (paid)", "credit": "Credit (received)"}
        widgets = {
            "date": DateInput(),
            "particular": forms.TextInput(attrs={"placeholder": "What this entry is for"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["particular"].required = False
        self.fields["debit"].required = False
        self.fields["credit"].required = False
        self.fields["debit"].widget.attrs["placeholder"] = "0.00"
        self.fields["credit"].widget.attrs["placeholder"] = "0.00"
        self.fields["date"].initial = datetime.date.today()

    def clean(self):
        cleaned = super().clean()
        debit = cleaned.get("debit") or Decimal("0")
        credit = cleaned.get("credit") or Decimal("0")
        if debit < 0 or credit < 0:
            raise forms.ValidationError("Debit and credit can't be negative.")
        if debit == 0 and credit == 0:
            raise forms.ValidationError("Enter an amount in either Debit (paid) or Credit (received).")
        if debit > 0 and credit > 0:
            raise forms.ValidationError("An entry is either a debit or a credit — use two entries for both.")
        cleaned["debit"] = debit
        cleaned["credit"] = credit
        return cleaned


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
    customer = forms.ModelChoiceField(queryset=_unfetched(Customer))

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
    payment_mode = forms.ChoiceField(choices=PaymentMode.choices, initial=PaymentMode.CASH, widget=SearchableChoiceWidget(search_placeholder="Search mode..."))
    # queryset=_unfetched(...) here, not BankAccount.objects.filter(...) — a
    # plain forms.Form field's keyword arguments are evaluated once, at
    # class-body (import) time, same risk as a ModelForm's auto-generated
    # FK fields elsewhere in this module: for a GOOGLE_SHEETS org this
    # would otherwise freeze in whichever org's bank accounts happened to
    # be active at that first import, and serve that same frozen list to
    # every org's form from then on. __init__ below sets the real,
    # per-request queryset instead.
    bank_account = forms.ModelChoiceField(
        queryset=_unfetched(BankAccount), required=False, empty_label="— Bank —",
        label="Bank",
        widget=SearchableModelChoiceWidget(search_placeholder="Search bank...")
    )
    note = forms.CharField(max_length=255, required=False, widget=forms.TextInput(attrs={"placeholder": "Optional note"}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["bank_account"].queryset = BankAccount.objects.filter(is_active=True)


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
    partner = forms.ModelChoiceField(queryset=_unfetched(Partner))
    bank_account = forms.ModelChoiceField(queryset=_unfetched(BankAccount), required=False, label="Bank")

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

