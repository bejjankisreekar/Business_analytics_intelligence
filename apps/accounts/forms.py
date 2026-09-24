import re

from django import forms
from django.contrib.auth import authenticate

from apps.organizations.models import Organization

from .models import User

USERNAME_RE = re.compile(r"^[a-z0-9_.-]+$")


class LoginForm(forms.Form):
    email = forms.CharField(
        max_length=254,
        widget=forms.TextInput(attrs={"placeholder": "you@company.com or username", "autofocus": True}),
    )
    password = forms.CharField(widget=forms.PasswordInput(attrs={"placeholder": "Password"}))

    def clean(self):
        cleaned = super().clean()
        identifier = cleaned.get("email")
        password = cleaned.get("password")
        if identifier:
            identifier = identifier.strip().lower()
        if identifier and password:
            email = identifier
            if "@" not in identifier:
                by_username = User.objects.filter(username=identifier).first()
                if by_username is not None:
                    email = by_username.email
            user = authenticate(email=email, password=password)
            if user is None:
                raise forms.ValidationError("Incorrect email or password.")
            if not user.is_active:
                raise forms.ValidationError("This account has been deactivated.")
            org = user.organization
            # Service stopped for a pending payment: let them in - they only get the Billing page.
            if org is not None and not org.is_service_active and not org.is_payment_hold:
                raise forms.ValidationError(
                    "This organization's access has been suspended. Contact support."
                )
            cleaned["user"] = user
        return cleaned


class OrganizationSignupForm(forms.Form):
    organization_name = forms.CharField(
        max_length=200, widget=forms.TextInput(attrs={"placeholder": "Acme Retail Pvt Ltd"})
    )
    business_type = forms.ChoiceField(
        choices=Organization.BusinessType.choices,
        widget=forms.Select(attrs={"class": "sr-only", "tabindex": "-1", "data-custom-combobox": "true"}),
    )
    size = forms.ChoiceField(
        choices=Organization.OrganizationSize.choices,
        widget=forms.Select(attrs={"class": "sr-only", "tabindex": "-1", "data-custom-combobox": "true"}),
    )

    first_name = forms.CharField(max_length=150, widget=forms.TextInput(attrs={"placeholder": "First name"}))
    last_name = forms.CharField(
        max_length=150, required=False, widget=forms.TextInput(attrs={"placeholder": "Last name"})
    )
    email = forms.EmailField(widget=forms.EmailInput(attrs={"placeholder": "you@company.com"}))
    username = forms.CharField(
        max_length=150, widget=forms.TextInput(attrs={"placeholder": "e.g. sreekar_mobiles"})
    )
    password = forms.CharField(
        min_length=8, widget=forms.PasswordInput(attrs={"placeholder": "Create a password"})
    )
    confirm_password = forms.CharField(
        widget=forms.PasswordInput(attrs={"placeholder": "Confirm password"})
    )

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(email=email).exists():
            raise forms.ValidationError("An account with this email already exists.")
        return email

    def clean_username(self):
        username = self.cleaned_data["username"].strip().lower()
        if not USERNAME_RE.match(username):
            raise forms.ValidationError(
                "Username can only contain lowercase letters, numbers, underscores, dots and hyphens."
            )
        if User.objects.filter(username=username).exists():
            raise forms.ValidationError("This username is already taken.")
        return username

    def clean(self):
        cleaned = super().clean()
        password = cleaned.get("password")
        confirm = cleaned.get("confirm_password")
        if password and confirm and password != confirm:
            raise forms.ValidationError("Passwords do not match.")
        return cleaned


class ForgotPasswordForm(forms.Form):
    email = forms.EmailField(widget=forms.EmailInput(attrs={"placeholder": "you@company.com", "autofocus": True}))

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if not User.objects.filter(email=email).exists():
            raise forms.ValidationError("No account is registered with this email.")
        return email


class ResetPasswordForm(forms.Form):
    otp = forms.CharField(
        max_length=6,
        widget=forms.TextInput(attrs={"placeholder": "6-digit code", "autofocus": True, "inputmode": "numeric"}),
    )
    new_password = forms.CharField(
        min_length=8, widget=forms.PasswordInput(attrs={"placeholder": "New password"})
    )
    confirm_password = forms.CharField(
        widget=forms.PasswordInput(attrs={"placeholder": "Confirm new password"})
    )

    def __init__(self, *args, user, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean_otp(self):
        from .models import PasswordResetOTP

        otp = self.cleaned_data["otp"].strip()
        record = (
            PasswordResetOTP.objects.filter(user=self.user, code=otp).order_by("-created_at").first()
        )
        if record is None or not record.is_valid():
            raise forms.ValidationError("This code is invalid or has expired.")
        self.otp_record = record
        return otp

    def clean(self):
        cleaned = super().clean()
        new_password = cleaned.get("new_password")
        confirm_password = cleaned.get("confirm_password")
        if new_password and confirm_password and new_password != confirm_password:
            raise forms.ValidationError("New password and confirmation do not match.")
        return cleaned

    def save(self):
        self.user.set_password(self.cleaned_data["new_password"])
        self.user.save(update_fields=["password"])
        self.otp_record.is_used = True
        self.otp_record.save(update_fields=["is_used"])
        return self.user


class ChangePasswordForm(forms.Form):
    current_password = forms.CharField(
        widget=forms.PasswordInput(attrs={"placeholder": "Current password", "autofocus": True})
    )
    new_password = forms.CharField(
        min_length=8, widget=forms.PasswordInput(attrs={"placeholder": "New password"})
    )
    confirm_password = forms.CharField(
        widget=forms.PasswordInput(attrs={"placeholder": "Confirm new password"})
    )

    def __init__(self, *args, user, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean_current_password(self):
        current_password = self.cleaned_data["current_password"]
        if not self.user.check_password(current_password):
            raise forms.ValidationError("Your current password is incorrect.")
        return current_password

    def clean(self):
        cleaned = super().clean()
        new_password = cleaned.get("new_password")
        confirm_password = cleaned.get("confirm_password")
        if new_password and confirm_password and new_password != confirm_password:
            raise forms.ValidationError("New password and confirmation do not match.")
        if new_password and cleaned.get("current_password") and new_password == cleaned["current_password"]:
            raise forms.ValidationError("New password must be different from your current password.")
        return cleaned

    def save(self):
        self.user.set_password(self.cleaned_data["new_password"])
        self.user.save(update_fields=["password"])
        return self.user


class ProfileForm(forms.ModelForm):
    class Meta:
        model = User
        fields = [
            "first_name", "last_name", "email", "username",
            "phone", "designation", "date_of_birth",
            "emergency_contact_name", "emergency_contact_phone", "emergency_contact_relation",
        ]
        widgets = {
            "date_of_birth": forms.DateInput(attrs={"type": "date"}),
        }

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.exclude(pk=self.instance.pk).filter(email__iexact=email).exists():
            raise forms.ValidationError("This email is already in use.")
        return email

    def clean_username(self):
        username = (self.cleaned_data.get("username") or "").strip() or None
        if username and User.objects.exclude(pk=self.instance.pk).filter(username__iexact=username).exists():
            raise forms.ValidationError("This username is already taken.")
        return username


class OrganizationProfileForm(forms.ModelForm):
    class Meta:
        model = Organization
        fields = [
            "name", "business_type", "size", "employee_count", "industry", "contact_person",
            "contact_email", "contact_phone", "address", "registered_address", "city", "state",
            "country", "tax_id", "pan_number", "website",
            "bank_account_holder", "bank_account_number", "bank_ifsc", "bank_name",
        ]
