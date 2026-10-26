from django import forms
from django.core.exceptions import ValidationError
from .models import DataStorageChangeRequest, Organization


# Each managed-database plan has a Drive twin; switching storage swaps between them.
_TO_DRIVE_PLAN = {"Professional": "Professional Drive", "Business": "Business Drive"}
_TO_DB_PLAN = {v: k for k, v in _TO_DRIVE_PLAN.items()}


def paired_plan_name(plan_name, requested_storage):
    """Plan name an org moves to when switching to `requested_storage`."""
    if requested_storage == Organization.StorageMode.GOOGLE_SHEETS:
        return _TO_DRIVE_PLAN.get(plan_name, plan_name)
    return _TO_DB_PLAN.get(plan_name, plan_name)


def compute_plan_impact(organization, requested_storage, using=None):
    """Current vs. requested plan and their *net* monthly prices (after
    discounts). Current net is what the subscription actually bills
    (price - discount); requested net is the paired plan's discounted price.
    Yearly subscriptions are shown per month. None if there's no subscription."""
    from decimal import Decimal, ROUND_HALF_UP
    from apps.billing.models import Plan, Subscription

    using = using or organization._state.db or 'default'
    sub = (
        Subscription.objects.using(using)
        .filter(organization_id=organization.pk, is_current=True)
        .select_related('plan').first()
    )
    if not sub:
        return None
    yearly = sub.billing_cycle == Subscription.BillingCycle.YEARLY
    per_month = (lambda v: v / 12) if yearly else (lambda v: v)
    money = lambda v: Decimal(v).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

    cur_price, cur_discount = sub.price or Decimal('0'), sub.discount or Decimal('0')
    new_name = paired_plan_name(sub.plan.name, requested_storage)
    new_plan = Plan.objects.using(using).filter(name=new_name).first() or sub.plan
    if sub.is_complimentary or new_plan.pk == sub.plan_id:
        # Nothing to re-price: keep what the subscription bills today.
        new_price, new_discount = cur_price, cur_discount
    else:
        new_price = new_plan.yearly_price if yearly else new_plan.monthly_price
        new_net_full = new_plan.effective_yearly_price if yearly else new_plan.effective_monthly_price
        new_discount = new_price - new_net_full
    return {
        "plan": new_plan,
        "current_plan": sub.plan.name,
        "requested_plan": new_plan.name,
        "current_price": money(per_month(cur_price - cur_discount)),
        "requested_price": money(per_month(new_price - new_discount)),
        # Gross / discount per month, for showing the breakdown
        "current_gross": money(per_month(cur_price)),
        "current_discount": money(per_month(cur_discount)),
        "requested_gross": money(per_month(new_price)),
        "requested_discount": money(per_month(new_discount)),
        # Full-cycle amounts written to the subscription on approval
        "new_price": new_price,
        "new_discount": new_discount,
    }


class RequestStorageChangeForm(forms.ModelForm):
    """Form for clients to request a storage change."""

    requested_storage = forms.ChoiceField(
        choices=Organization.StorageMode.choices,
        widget=forms.RadioSelect,
        label="Where would you like to store your data?"
    )
    reason = forms.CharField(
        widget=forms.Textarea(attrs={'rows': 4, 'placeholder': 'Tell us why you want to change storage providers'}),
        required=False,
        label="Reason for change (optional)"
    )

    class Meta:
        model = DataStorageChangeRequest
        fields = ['requested_storage', 'reason']

    def __init__(self, organization, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.organization = organization

        # Remove current storage from choices
        choices = [
            choice for choice in Organization.StorageMode.choices
            if choice[0] != organization.storage_mode
        ]
        self.fields['requested_storage'].choices = choices

    def clean(self):
        cleaned_data = super().clean()
        requested_storage = cleaned_data.get('requested_storage')

        if requested_storage == self.organization.storage_mode:
            raise ValidationError("You must select a different storage provider.")

        # Check for existing pending/approved request
        existing = DataStorageChangeRequest.objects.filter(
            organization=self.organization,
            status__in=[DataStorageChangeRequest.Status.PENDING, DataStorageChangeRequest.Status.APPROVED]
        ).first()

        if existing:
            raise ValidationError(
                f"You already have a pending storage change request. "
                f"Please wait for it to be reviewed before submitting another."
            )

        return cleaned_data

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.organization = self.organization
        instance.current_storage = self.organization.storage_mode

        impact = compute_plan_impact(self.organization, instance.requested_storage)
        if impact:
            instance.current_plan_name = impact["current_plan"]
            instance.requested_plan_name = impact["requested_plan"]
            instance.current_monthly_price = impact["current_price"]
            instance.requested_monthly_price = impact["requested_price"]
        instance.price_difference = instance.requested_monthly_price - instance.current_monthly_price

        if commit:
            instance.save()
        return instance


class ApproveStorageChangeForm(forms.ModelForm):
    """Form for superadmin to approve/reject storage change requests."""

    class Meta:
        model = DataStorageChangeRequest
        fields = ['status', 'admin_notes']
        widgets = {
            'status': forms.RadioSelect(choices=[
                (DataStorageChangeRequest.Status.APPROVED, 'Approve'),
                (DataStorageChangeRequest.Status.REJECTED, 'Reject'),
            ]),
            'admin_notes': forms.Textarea(attrs={
                'rows': 4,
                'placeholder': 'Add notes about your decision (optional)'
            })
        }

    def clean(self):
        cleaned_data = super().clean()
        status = cleaned_data.get('status')

        if status and status not in [DataStorageChangeRequest.Status.APPROVED, DataStorageChangeRequest.Status.REJECTED]:
            raise ValidationError("Invalid status. Please select either Approve or Reject.")

        return cleaned_data
