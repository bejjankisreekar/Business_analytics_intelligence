import logging

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login, logout, update_session_auth_hash
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.mail import EmailMultiAlternatives
from django.shortcuts import redirect, render
from django.template.loader import render_to_string
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
    ForgotPasswordForm,
    LoginForm,
    OrganizationProfileForm,
    OrganizationSignupForm,
    ProfileForm,
    ResetPasswordForm,
)
from .models import PasswordResetOTP, User

logger = logging.getLogger(__name__)

RESET_SESSION_KEY = "password_reset_email"


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


class ForgotPasswordView(FormView):
    template_name = "accounts/forgot_password.html"
    form_class = ForgotPasswordForm
    success_url = reverse_lazy("accounts:reset_password")

    def form_valid(self, form):
        email = form.cleaned_data["email"]
        user = User.objects.get(email=email)
        otp = PasswordResetOTP.objects.create(user=user, code=PasswordResetOTP.generate_code())
        try:
            text_body = (
                f"Your {settings.SITE_NAME} password reset code is {otp.code}.\n\n"
                "It expires in 10 minutes. If you didn't request this, you can ignore this email."
            )
            html_body = render_to_string("emails/otp_code.html", {
                "site_name": settings.SITE_NAME,
                "code": otp.code,
                "heading": "Reset your password",
                "intro": "Use this code to finish resetting your password.",
            })
            email_message = EmailMultiAlternatives(
                subject=f"Your {settings.SITE_NAME} password reset code",
                body=text_body,
                from_email=settings.DEFAULT_FROM_EMAIL,
                to=[email],
            )
            email_message.attach_alternative(html_body, "text/html")
            email_message.send()
        except Exception:
            logger.exception("Failed to send password reset OTP email to %s", email)
            form.add_error(None, "We couldn't send the reset code right now. Please try again in a few minutes.")
            return self.form_invalid(form)
        self.request.session[RESET_SESSION_KEY] = email
        messages.success(self.request, f"We've emailed a 6-digit code to {email}.")
        return super().form_valid(form)


class ResetPasswordView(FormView):
    template_name = "accounts/reset_password.html"
    form_class = ResetPasswordForm
    success_url = reverse_lazy("accounts:login")

    def dispatch(self, request, *args, **kwargs):
        if not request.session.get(RESET_SESSION_KEY):
            return redirect("accounts:forgot_password")
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = User.objects.get(email=self.request.session[RESET_SESSION_KEY])
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["email"] = self.request.session.get(RESET_SESSION_KEY)
        return context

    def form_valid(self, form):
        form.save()
        del self.request.session[RESET_SESSION_KEY]
        messages.success(self.request, "Your password has been reset. Sign in with your new password.")
        return super().form_valid(form)


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
