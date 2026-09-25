import secrets

from django import forms
from django.utils.text import slugify

from apps.billing.models import Coupon, Invoice, Payment, Plan, Subscription
from apps.organizations.models import Organization, ServiceStatusChange

PROFILE_FIELDS = [
    "name",
    "business_type",
    "industry",
    "size",
    "contact_person",
    "contact_email",
    "contact_phone",
    "address",
    "city",
    "state",
    "country",
    "tax_id",
    "website",
    "historical_entry_cutoff_date",
    "historical_entry_no_limit",
]


def _style(fields):
    for field in fields.values():
        field.widget.attrs.setdefault("class", "sa-input")


class ClientProfileForm(forms.ModelForm):
    class Meta:
        model = Organization
        fields = PROFILE_FIELDS
        widgets = {
            "historical_entry_cutoff_date": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["historical_entry_cutoff_date"].label = "Backdating cutoff date"
        self.fields["historical_entry_cutoff_date"].help_text = (
            "Entries dated before this are blocked. Leave blank to use the plan's default."
        )
        self.fields["historical_entry_no_limit"].label = "No backdating limit"
        _style(self.fields)
        self.fields["historical_entry_no_limit"].widget.attrs.pop("class", None)


class ClientCreateForm(ClientProfileForm):
    owner_first_name = forms.CharField(max_length=150, label="Owner first name")
    owner_last_name = forms.CharField(max_length=150, required=False, label="Owner last name")
    owner_email = forms.EmailField(label="Owner login email")
    owner_password = forms.CharField(
        required=False,
        widget=forms.PasswordInput,
        label="Owner temporary password",
        help_text="Leave blank to auto-generate a random password.",
    )

    field_order = [
        "name", "business_type", "industry", "size",
        "contact_person", "contact_email", "contact_phone",
        "address", "city", "state", "country", "tax_id", "website",
        "owner_first_name", "owner_last_name", "owner_email", "owner_password",
    ]

    def __init__(self, *args, using="default", **kwargs):
        super().__init__(*args, **kwargs)
        self._using = using

    def clean_owner_email(self):
        from apps.accounts.models import User

        email = self.cleaned_data["owner_email"].strip().lower()
        if User.objects.using(self._using).filter(email=email).exists():
            raise forms.ValidationError("A user with this email already exists.")
        return email

    def generated_password(self):
        return self.cleaned_data.get("owner_password") or secrets.token_urlsafe(9)


class ClientUserEditForm(forms.ModelForm):
    class Meta:
        from apps.accounts.models import User

        model = User
        fields = ["first_name", "last_name", "email", "username"]

    def __init__(self, *args, using="default", **kwargs):
        super().__init__(*args, **kwargs)
        self._using = using
        _style(self.fields)

    def clean_email(self):
        from apps.accounts.models import User

        email = self.cleaned_data["email"].strip().lower()
        if User.objects.using(self._using).exclude(pk=self.instance.pk).filter(email__iexact=email).exists():
            raise forms.ValidationError("This email is already in use.")
        return email

    def clean_username(self):
        from apps.accounts.models import User

        username = (self.cleaned_data.get("username") or "").strip() or None
        if username and User.objects.using(self._using).exclude(pk=self.instance.pk).filter(username__iexact=username).exists():
            raise forms.ValidationError("This username is already taken.")
        return username

    def save(self, commit=True):
        instance = super().save(commit=False)
        if commit:
            instance.save(using=self._using)
        return instance


class ResetClientPasswordForm(forms.Form):
    new_password = forms.CharField(
        required=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
        label="New password",
        help_text="Leave blank to auto-generate a random password.",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _style(self.fields)

    def generated_password(self):
        return self.cleaned_data.get("new_password") or secrets.token_urlsafe(9)


class PlanForm(forms.ModelForm):
    features = forms.MultipleChoiceField(
        choices=Plan.FEATURE_CHOICES, required=False, widget=forms.CheckboxSelectMultiple
    )

    class Meta:
        model = Plan
        fields = [
            "name", "is_active", "show_on_landing_page", "monthly_price", "yearly_price",
            "monthly_discount_percent", "yearly_discount_percent",
            "trial_days", "user_limit", "business_limit", "features",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _style(self.fields)
        # Checkbox lists shouldn't get the generic text-input styling.
        self.fields["features"].widget.attrs.pop("class", None)
        self.fields["is_active"].widget.attrs.pop("class", None)
        self.fields["show_on_landing_page"].widget.attrs.pop("class", None)
        if self.instance and self.instance.pk:
            self.initial.setdefault("features", self.instance.features or [])

    def save(self, commit=True):
        instance = super().save(commit=False)
        if not instance.slug:
            instance.slug = slugify(instance.name)
        instance.features = self.cleaned_data.get("features", [])
        if commit:
            instance.save()
        return instance


class CouponForm(forms.ModelForm):
    class Meta:
        model = Coupon
        fields = [
            "code", "description", "discount_type", "discount_value", "is_active",
            "valid_from", "valid_until", "max_redemptions", "max_redemptions_per_org",
        ]
        widgets = {
            "valid_from": forms.DateInput(attrs={"type": "date"}),
            "valid_until": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _style(self.fields)
        self.fields["is_active"].widget.attrs.pop("class", None)
        self.fields["code"].widget.attrs["placeholder"] = "e.g. SUMMER25"

    def clean_code(self):
        return self.cleaned_data["code"].strip().upper()

    def clean(self):
        cleaned = super().clean()
        discount_type = cleaned.get("discount_type")
        discount_value = cleaned.get("discount_value")
        if discount_type == Coupon.DiscountType.PERCENT and discount_value is not None:
            if discount_value <= 0 or discount_value > 100:
                self.add_error("discount_value", "A percentage discount must be between 0 and 100.")
        if discount_type == Coupon.DiscountType.FLAT and discount_value is not None and discount_value <= 0:
            self.add_error("discount_value", "A flat discount must be more than 0.")
        if discount_type == Coupon.DiscountType.FIXED_PRICE and discount_value is not None and discount_value < 0:
            self.add_error("discount_value", "The fixed price can't be negative.")
        valid_from = cleaned.get("valid_from")
        valid_until = cleaned.get("valid_until")
        if valid_from and valid_until and valid_from > valid_until:
            self.add_error("valid_until", "Valid until can't be before valid from.")
        return cleaned


class SubscriptionActionForm(forms.Form):
    """Creates a NEW subscription record for an org (a plan change, renewal,
    or manual billing correction) — the current one is superseded, not
    edited, so history is preserved."""

    plan = forms.ModelChoiceField(queryset=Plan.objects.none())
    billing_cycle = forms.ChoiceField(choices=Subscription.BillingCycle.choices)
    start_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    price = forms.DecimalField(required=False, min_value=0, help_text="Leave blank to use the plan's price.")
    discount = forms.DecimalField(required=False, min_value=0, initial=0)
    tax = forms.DecimalField(required=False, min_value=0, initial=0)
    status = forms.ChoiceField(choices=Subscription.Status.choices)
    payment_status = forms.ChoiceField(choices=Subscription.PaymentStatus.choices)
    auto_renewal = forms.BooleanField(required=False, initial=True)
    notes = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))

    def __init__(self, *args, using="default", **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["plan"].queryset = Plan.objects.using(using).filter(is_active=True)
        _style(self.fields)
        self.fields["auto_renewal"].widget.attrs.pop("class", None)


class SubscriptionEditForm(forms.ModelForm):
    """Direct in-place correction of the org's current subscription — same
    "fix a wrong value" philosophy as EditKeyDatesForm, not a plan change/
    renewal event (those go through SubscriptionActionForm, which
    supersedes with a new history row instead). Lives on its own
    Subscriptions page so price, discount and the autopay override are all
    editable in one place instead of being scattered across org detail /
    service control."""

    price = forms.DecimalField(min_value=0)
    discount = forms.DecimalField(min_value=0)
    tax = forms.DecimalField(min_value=0)
    autopay_amount = forms.DecimalField(required=False, min_value=0)

    class Meta:
        model = Subscription
        fields = [
            "plan", "billing_cycle", "status", "payment_status",
            "price", "discount", "tax", "autopay_amount", "auto_renewal", "notes",
        ]
        widgets = {"notes": forms.Textarea(attrs={"rows": 2})}

    def __init__(self, *args, using="default", **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["plan"].queryset = Plan.objects.using(using)
        self.fields["autopay_amount"].help_text = (
            "What Razorpay bills every autopay cycle. Tracks the final amount above until you edit it directly."
        )
        _style(self.fields)
        self.fields["auto_renewal"].widget.attrs.pop("class", None)
        # Opt these out of the site-wide custom-dropdown enhancement
        # (base.html's bai.enhanceSelects): its trigger button sizes itself
        # to the selected option's own text ("Complementary" vs "Paid"),
        # not to the grid column, so the four selects on this page visibly
        # differ in width and resize on every selection. A plain <select>
        # with width:100% (already set by _style's sa-input class) doesn't
        # have that problem, so all four line up and stay put.
        for name in ("plan", "billing_cycle", "status", "payment_status"):
            self.fields[name].widget.attrs["data-plain"] = "true"


class EditKeyDatesForm(forms.Form):
    """Direct superadmin correction of an org's backdating cutoff and the
    current subscription's trial/billing dates — edits the existing rows
    in place, unlike SubscriptionActionForm (supersedes with a new row) or
    ExtendTrialForm (only bumps trial_end_date forward). For fixing a date
    that was entered wrong, not for a normal renew/extend/plan-change."""

    historical_entry_cutoff_date = forms.DateField(
        required=False, widget=forms.DateInput(attrs={"type": "date"}),
        label="Backdating cutoff date",
        help_text="Entries dated before this are blocked. Leave blank to use the plan's default.",
    )
    historical_entry_no_limit = forms.BooleanField(required=False, label="No backdating limit")

    trial_start_date = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))
    trial_end_date = forms.DateField(
        required=False, widget=forms.DateInput(attrs={"type": "date"}), label="Trial expiry date"
    )

    start_date = forms.DateField(
        required=False, widget=forms.DateInput(attrs={"type": "date"}), label="Billing start date"
    )
    end_date = forms.DateField(
        required=False, widget=forms.DateInput(attrs={"type": "date"}), label="Billing end date"
    )
    cancellation_date = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _style(self.fields)
        self.fields["historical_entry_no_limit"].widget.attrs.pop("class", None)


class ExtendTrialForm(forms.Form):
    new_trial_end_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}), label="New trial end date")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _style(self.fields)


class GrantComplimentaryForm(forms.Form):
    DURATION_CHOICES = [
        ("1_month", "1 month"),
        ("1_year", "1 year"),
        ("custom", "Custom date range"),
    ]

    duration = forms.ChoiceField(choices=DURATION_CHOICES)
    start_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    end_date = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))
    plan = forms.ModelChoiceField(queryset=Plan.objects.none(), required=False, help_text="Leave blank to keep the current plan.")
    notes = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))

    def __init__(self, *args, using="default", **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["plan"].queryset = Plan.objects.using(using).filter(is_active=True)
        _style(self.fields)

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("duration") == "custom" and not cleaned.get("end_date"):
            raise forms.ValidationError("An end date is required for a custom date range.")
        return cleaned


class RecordPaymentForm(forms.Form):
    """Manually record an off-platform payment (bank transfer, cash,
    cheque) — gateway is always MANUAL here; a real Razorpay/Stripe payment
    arrives through the webhook instead."""

    invoice = forms.ModelChoiceField(
        queryset=Invoice.objects.none(), required=False,
        help_text="Optional — leave blank for a payment not tied to a specific invoice.",
    )
    amount = forms.DecimalField(min_value=0.01, max_digits=10, decimal_places=2)
    currency = forms.CharField(max_length=8, initial="INR")
    payment_method = forms.ChoiceField(choices=Payment.Method.choices, initial=Payment.Method.BANK_TRANSFER)
    transaction_id = forms.CharField(
        required=False, max_length=100, label="Reference / transaction ID",
        help_text="Optional — cheque number, UTR, receipt number, etc.",
    )
    payment_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    status = forms.ChoiceField(choices=Payment.Status.choices, initial=Payment.Status.SUCCESS)
    notes = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}), label="Notes / failure reason")

    def __init__(self, *args, using="default", organization=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["invoice"].queryset = (
            Invoice.objects.using(using)
            .filter(organization_id=organization.id)
            .exclude(status=Invoice.Status.CANCELLED)
            .order_by("-invoice_date")
        )
        _style(self.fields)


class ServiceActionForm(forms.Form):
    """Backs every start/stop/suspend/resume action. `confirm` and (for
    stop/suspend) `reason` are enforced here — server-side — not just via a
    JS confirm() dialog, so the requirement can't be bypassed by posting
    directly to the endpoint.
    """

    reason = forms.ChoiceField(choices=[("", "—")] + ServiceStatusChange.Reason.choices, required=False)
    notes = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))
    confirm = forms.BooleanField(
        required=True, label="I confirm this action",
        error_messages={"required": "You must confirm this action before it takes effect."},
    )

    def __init__(self, *args, reason_required=False, **kwargs):
        super().__init__(*args, **kwargs)
        self._reason_required = reason_required
        _style(self.fields)
        self.fields["confirm"].widget.attrs.pop("class", None)

    def clean_reason(self):
        reason = self.cleaned_data.get("reason", "")
        if self._reason_required and not reason:
            raise forms.ValidationError("A reason is required for this action.")
        return reason


class CreateInvoiceForm(forms.Form):
    """Manually generate an invoice for a client. It shows up as pending
    (ISSUED) on the client's own Billing page as soon as it's created."""

    subscription = forms.ModelChoiceField(
        queryset=Subscription.objects.none(), required=False,
        help_text="Optional — link this invoice to a specific subscription period.",
    )
    subtotal = forms.DecimalField(min_value=0, max_digits=10, decimal_places=2)
    discount = forms.DecimalField(required=False, min_value=0, max_digits=10, decimal_places=2, initial=0)
    tax = forms.DecimalField(required=False, min_value=0, max_digits=10, decimal_places=2, initial=0)
    invoice_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    due_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    status = forms.ChoiceField(choices=Invoice.Status.choices, initial=Invoice.Status.ISSUED)

    def __init__(self, *args, using="default", organization=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["subscription"].queryset = (
            Subscription.objects.using(using).filter(organization_id=organization.id).order_by("-created_at")
        )
        _style(self.fields)


class EditInvoiceForm(forms.ModelForm):
    """Direct superadmin correction of an already-issued (or already-paid)
    invoice's own fields. Deliberately excludes amount_paid/total/amount_due
    — those stay derived (total from subtotal/discount/tax on save(),
    amount_paid from the sum of the invoice's actual Payment rows) rather
    than editable directly, so they can't drift out of sync with the
    numbers that produced them. To correct amount_paid, edit the payment
    itself instead (see EditPaymentForm)."""

    class Meta:
        model = Invoice
        fields = ["subscription", "subtotal", "discount", "tax", "currency", "invoice_date", "due_date", "status"]
        widgets = {
            "invoice_date": forms.DateInput(attrs={"type": "date"}),
            "due_date": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args, using="default", **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["subscription"].queryset = (
            Subscription.objects.using(using)
            .filter(organization_id=self.instance.organization_id)
            .order_by("-created_at")
        )
        self.fields["subscription"].required = False
        _style(self.fields)


class EditPaymentForm(forms.ModelForm):
    """Direct superadmin correction of an already-recorded payment — e.g.
    fixing a typo'd amount, wrong date, or wrong method after the fact.
    Saving re-syncs the linked invoice's amount_paid/status (see
    apps.billing.payments.edit_payment) but deliberately does not replay
    renewal/access side effects — see that function's docstring."""

    class Meta:
        model = Payment
        fields = [
            "amount", "currency", "payment_method", "gateway", "transaction_id",
            "payment_date", "status", "failure_reason",
        ]
        widgets = {
            "payment_date": forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["payment_date"].input_formats = ["%Y-%m-%dT%H:%M"]
        _style(self.fields)


class GenerateInvoiceForm(forms.Form):
    """Generate an invoice for one specific client. The amount starts at the
    client's current plan price, and an optional coupon can bring it down (a
    "Pay 999" coupon turns a 2999 subscription into a 999 invoice). The client
    sees the invoice - and the reduced amount - on their own Billing page."""

    organization = forms.ModelChoiceField(queryset=Organization.objects.none(), label="Client")
    subtotal = forms.DecimalField(min_value=0, max_digits=10, decimal_places=2, label="Amount (before coupon)")
    coupon = forms.ModelChoiceField(
        queryset=Coupon.objects.none(), required=False, empty_label="No coupon",
        help_text="Optional. Applied right away, so the client is billed the reduced amount.",
    )
    discount = forms.DecimalField(required=False, min_value=0, max_digits=10, decimal_places=2, initial=0,
                                  label="Extra discount (\u20b9)")
    tax = forms.DecimalField(required=False, min_value=0, max_digits=10, decimal_places=2, initial=0)
    invoice_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    due_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    status = forms.ChoiceField(
        choices=[(Invoice.Status.ISSUED, "Issued - visible and payable now"),
                 (Invoice.Status.DRAFT, "Draft - not counted as due yet")],
        initial=Invoice.Status.ISSUED,
    )

    def __init__(self, *args, using="default", **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["organization"].queryset = Organization.objects.using(using).order_by("name")
        self.fields["organization"].label_from_instance = lambda o: f"{o.name} \u2014 {o.organization_code}"
        self.fields["coupon"].queryset = Coupon.objects.using(using).filter(is_active=True).order_by("code")
        self.fields["coupon"].label_from_instance = lambda c: f"{c.code} \u2014 {c.summary()}"
        _style(self.fields)


class ApplyInvoiceCouponForm(forms.Form):
    coupon = forms.ModelChoiceField(queryset=Coupon.objects.none())

    def __init__(self, *args, using="default", **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["coupon"].queryset = Coupon.objects.using(using).filter(is_active=True).order_by("code")
        self.fields["coupon"].label_from_instance = lambda c: f"{c.code} \u2014 {c.summary()}"
        _style(self.fields)


class RecordInvoicePaymentForm(forms.Form):
    """Record an off-platform payment (bank transfer, cash, cheque, UPI...)
    against one specific invoice. The amount starts at what's still due and
    may be smaller for a part-payment, but never larger."""

    amount = forms.DecimalField(min_value=0.01, max_digits=10, decimal_places=2, label="Amount received")
    payment_method = forms.ChoiceField(choices=Payment.Method.choices, initial=Payment.Method.BANK_TRANSFER)
    payment_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    transaction_id = forms.CharField(
        required=False, max_length=100, label="Reference / transaction ID",
        help_text="Optional - UTR, cheque number, receipt number, etc.",
    )
    notes = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))

    def __init__(self, *args, amount_due=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.amount_due = amount_due
        _style(self.fields)

    def clean_amount(self):
        amount = self.cleaned_data["amount"]
        if self.amount_due is not None and amount > self.amount_due:
            raise forms.ValidationError(f"This is more than the amount still due ({self.amount_due}).")
        return amount
