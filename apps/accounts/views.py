from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.views.generic import FormView, View

from apps.billing import services as billing_services
from apps.billing.models import Plan
from apps.organizations.services import (
    OrganizationSignupError,
    create_organization_with_tenant_schema_and_admin,
)

from .forms import LoginForm, OrganizationSignupForm
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


class LogoutView(LoginRequiredMixin, View):
    def post(self, request, *args, **kwargs):
        logout(request)
        return redirect("core:landing")

    def get(self, request, *args, **kwargs):
        logout(request)
        return redirect("core:landing")
