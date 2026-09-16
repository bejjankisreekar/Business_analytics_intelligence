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
            if user.organization is not None and not user.organization.is_service_active:
                raise forms.ValidationError(
                    "This organization's access has been suspended. Contact support."
                )
            cleaned["user"] = user
        return cleaned


class OrganizationSignupForm(forms.Form):
    organization_name = forms.CharField(
        max_length=200, widget=forms.TextInput(attrs={"placeholder": "Acme Retail Pvt Ltd"})
    )
    business_type = forms.ChoiceField(choices=Organization.BusinessType.choices)
    size = forms.ChoiceField(choices=Organization.OrganizationSize.choices)

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
