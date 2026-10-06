from django import forms
from django.core.exceptions import ValidationError
from .models import DataStorageChangeRequest, Organization


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

        # Calculate billing impact (placeholder - update based on your plan pricing logic)
        from decimal import Decimal

        # Get current plan's pricing
        subscription = self.organization.subscriptions.filter(is_current=True).first()
        if subscription:
            instance.current_monthly_price = subscription.price or Decimal('0')
            # For now, assume both storage types cost the same
            # Update this logic if you have different pricing per storage type
            instance.requested_monthly_price = subscription.price or Decimal('0')

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
