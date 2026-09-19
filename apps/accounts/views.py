from django.contrib import messages
from django.contrib.auth import login, logout, update_session_auth_hash
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import redirect, render
from django.urls import reverse_lazy
from django.views.generic import FormView, View

from apps.billing import services as billing_services
from apps.billing.models import Plan
from apps.organizations.services import (
    OrganizationSignupError,
    create_organization_with_tenant_schema_and_admin,
)

from .forms import (
    ChangePasswordForm,
    LoginForm,
    OrganizationProfileForm,
    OrganizationSignupForm,
    ProfileForm,
)
from .models import User


def _post_login_redirect(user):
    if user.role == User.Role.SUPER_ADMIN:
        return redirect("superadmin:overview")
    return redirect("finance:dashboard")


class LoginView(FormView):
    template_name = "accounts/login.html"
    form_class = LoginForm

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            return _post_login_redirect(request.user)
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        user = form.cleaned_data["user"]
        login(self.request, user)
        return _post_login_redirect(user)


class SignupView(FormView):
    template_name = "accounts/signup.html"
    form_class = OrganizationSignupForm

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            return _post_login_redirect(request.user)
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        data = form.cleaned_data
        try:
            org, admin = create_organization_with_tenant_schema_and_admin(
                org_data={
                    "name": data["organization_name"],
                    "business_type": data["business_type"],
                    "size": data["size"],
                },
                admin_data={
                    "email": data["email"],
                    "username": data["username"],
                    "password": data["password"],
                    "first_name": data["first_name"],
                    "last_name": data["last_name"],
                },
            )
        except OrganizationSignupError as exc:
            messages.error(self.request, str(exc))
            return self.form_invalid(form)

        bootstrap_plan = Plan.objects.filter(is_active=True).order_by("monthly_price").first()
        if bootstrap_plan is not None:
            billing_services.start_trial(org, bootstrap_plan)

        login(self.request, admin)
        messages.success(
            self.request,
            f"Welcome to Business Analytics Intelligence, {org.name}! Your workspace is ready.",
        )
        return redirect("finance:dashboard")


class ChangePasswordView(LoginRequiredMixin, FormView):
    template_name = "accounts/change_password.html"
    form_class = ChangePasswordForm
    success_url = reverse_lazy("accounts:change_password")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def form_valid(self, form):
        user = form.save()
        update_session_auth_hash(self.request, user)
        messages.success(self.request, "Your password has been updated.")
        return super().form_valid(form)


class ProfileView(LoginRequiredMixin, View):
    template_name = "accounts/profile.html"

    def _can_edit_org(self, user):
        return bool(user.organization_id) and user.role in (User.Role.OWNER, User.Role.ADMIN)

    def _forms(self, request, data=None):
        user = request.user
        user_form = ProfileForm(data, instance=user, prefix="user")
        org_form = None
        if self._can_edit_org(user):
            org_form = OrganizationProfileForm(data, instance=user.organization, prefix="org")
        return user_form, org_form

    def get(self, request, *args, **kwargs):
        user_form, org_form = self._forms(request)
        return render(request, self.template_name, {"user_form": user_form, "org_form": org_form})

    def post(self, request, *args, **kwargs):
        user_form, org_form = self._forms(request, request.POST)
        if user_form.is_valid() and (org_form is None or org_form.is_valid()):
            user_form.save()
            if org_form is not None:
                org_form.save()
            messages.success(request, "Your profile has been updated.")
            return redirect("accounts:profile")
        return render(request, self.template_name, {"user_form": user_form, "org_form": org_form})


class LogoutView(LoginRequiredMixin, View):
    def post(self, request, *args, **kwargs):
        logout(request)
        return redirect("core:landing")

    def get(self, request, *args, **kwargs):
        logout(request)
        return redirect("core:landing")
