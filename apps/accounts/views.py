import logging

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login, logout, update_session_auth_hash
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.mail import EmailMultiAlternatives
from django.http import Http404, JsonResponse
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
    CreateManagerAccountForm,
    ForgotPasswordForm,
    LoginForm,
    OrganizationProfileForm,
    OrganizationSignupForm,
    ProfileForm,
    ResetManagerPasswordForm,
    ResetPasswordForm,
)
from .manager_service import ManagerAccountError, validate_manager_account_creation
from .models import PasswordResetOTP, User

logger = logging.getLogger(__name__)

RESET_SESSION_KEY = "password_reset_email"


def _post_login_redirect(user):
    if user.role == User.Role.SUPER_ADMIN:
        return redirect("superadmin:overview")
    if user.role == User.Role.MANAGER:
        return redirect("finance:daily_report")
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
        org_data = {
            "name": data["organization_name"],
            "business_type": data["business_type"],
            "size": data["size"],
            "storage_mode": data["storage_mode"],
        }
        try:
            org, admin = create_organization_with_tenant_schema_and_admin(
                org_data=org_data,
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
            f"Welcome to Prism Pulse Intelligence & Analytics, {org.name}! Your workspace is ready.",
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


# The profile page's "Switch database" section is hidden for now. Flip to True to bring it back.
SHOW_SWITCH_DATABASE = False


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

    def _context(self, request, user_form, org_form):
        from apps.organizations import google_drive_client as drive
        from apps.organizations.models import Organization

        user = request.user
        can_edit_org = self._can_edit_org(user)
        is_sheets_org = bool(user.organization_id) and user.organization.storage_mode == Organization.StorageMode.GOOGLE_SHEETS
        drive_connection = None
        if can_edit_org and is_sheets_org:
            drive_connection = getattr(user.organization, "cloud_backup", None)

        # After "Disconnect" on a Sheets org the row is kept (it holds the
        # pointer to the live Sheet) but its tokens are blanked — that must
        # read as disconnected, not "Connected", so the Connect button shows.
        drive_disconnected = False
        if drive_connection:
            from apps.organizations.encryption import decrypt_secret

            try:
                drive_disconnected = not decrypt_secret(drive_connection.refresh_token_encrypted)
            except Exception:
                drive_disconnected = True

        # Manager account information
        manager_account = None
        can_create_manager = False
        show_manager_button = False
        disable_manager_button = False
        if can_edit_org:
            manager_account = User.objects.filter(
                organization=user.organization,
                role=User.Role.MANAGER
            ).first()

            from .manager_service import can_organization_have_manager_accounts
            current_subscription = user.organization.subscriptions.filter(is_current=True).first()
            plan_slug = current_subscription.plan.slug.lower() if current_subscription else ""

            # Determine if button should be shown and enabled/disabled
            is_business = "business" in plan_slug
            is_professional = "professional" in plan_slug
            is_eligible = can_organization_have_manager_accounts(user.organization)

            # Always show the button to org owners; it is disabled when the plan
            # doesn't allow manager accounts (Professional allows only 1 account).
            show_manager_button = True
            can_create_manager = is_eligible and not is_professional and manager_account is None
            disable_manager_button = not can_create_manager

        return {
            "user_form": user_form,
            "org_form": org_form,
            "can_edit_org": can_edit_org,
            "can_export_excel": can_edit_org,
            "is_sheets_org": is_sheets_org,
            # OUR_DATABASE -> GOOGLE_SHEETS needs a fresh OAuth consent
            # (a new Sheet is being created), so it goes through the
            # same connect_drive flow as a first-time connection.
            "can_switch_to_sheets": SHOW_SWITCH_DATABASE and can_edit_org and not is_sheets_org and drive.is_configured(),
            # GOOGLE_SHEETS -> OUR_DATABASE needs no OAuth (already
            # connected) — a single POST, see SwitchToOurDatabaseView.
            "can_switch_to_our_database": SHOW_SWITCH_DATABASE and can_edit_org and is_sheets_org and bool(drive_connection) and not drive_disconnected,
            "can_connect_drive": can_edit_org and is_sheets_org and drive.is_configured(),
            "drive_connection": drive_connection,
            "drive_disconnected": drive_disconnected,
            "manager_account": manager_account,
            "can_create_manager": can_create_manager,
            "show_manager_button": show_manager_button,
            "disable_manager_button": disable_manager_button,
        }

    def get(self, request, *args, **kwargs):
        user_form, org_form = self._forms(request)
        return render(request, self.template_name, self._context(request, user_form, org_form))

    def post(self, request, *args, **kwargs):
        user_form, org_form = self._forms(request, request.POST)
        if user_form.is_valid() and (org_form is None or org_form.is_valid()):
            user_form.save()
            if org_form is not None:
                org_form.save()
            messages.success(request, "Your profile has been updated.")
            return redirect("accounts:profile")
        return render(request, self.template_name, self._context(request, user_form, org_form))


class ExportFinanceDataExcelView(LoginRequiredMixin, View):
    """Dumps every apps.finance table for the current user's organization
    into one .xlsx workbook, one sheet per table. Queries go through the
    normal ORM, so TenantSchemaMiddleware's active SheetSession already
    points them at the right Google Sheet for this org.
    """

    def get(self, request, *args, **kwargs):
        from django.http import Http404, JsonResponse, HttpResponse

        from apps.organizations.exports import build_finance_excel_bytes

        user = request.user
        if not user.organization_id or user.role not in (User.Role.OWNER, User.Role.ADMIN):
            raise Http404

        response = HttpResponse(
            build_finance_excel_bytes(),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        response["Content-Disposition"] = (
            f'attachment; filename="{user.organization.organization_code}-data.xlsx"'
        )
        return response


def _can_edit_org(user):
    return bool(user.organization_id) and user.role in (User.Role.OWNER, User.Role.ADMIN)


class ConnectDatabaseGateView(LoginRequiredMixin, View):
    """What a brand-new org sees instead of the dashboard until its
    owner connects Google Drive — TenantSchemaMiddleware redirects
    every other page here while that's true (GATE_EXEMPT_PREFIXES is the
    short list of pages that still work in the meantime: this page
    itself, the connect/callback views, and logout). Visiting this page
    once already connected just bounces straight to the dashboard — the
    gate condition is false by then, so there's nothing left to show.
    """

    def get(self, request, *args, **kwargs):
        from apps.organizations import google_drive_client as drive
        from apps.organizations.models import Organization, org_needs_drive_connection

        user = request.user
        org = user.organization
        if not org or not drive.is_configured() or not org_needs_drive_connection(org):
            return redirect("finance:dashboard")

        return render(
            request,
            "accounts/connect_database_gate.html",
            {"switch_approved": org.storage_mode == Organization.StorageMode.OUR_DATABASE},
        )


class ConnectGoogleDriveView(LoginRequiredMixin, View):
    """Starts the Google OAuth flow so this org's owner/admin can link
    their own Google Drive — see apps.organizations.drive_sync for what
    gets pushed there and when."""

    def get(self, request, *args, **kwargs):
        import secrets

        from django.http import Http404, JsonResponse, HttpResponseRedirect
        from django.urls import reverse

        from apps.organizations import google_drive_client as drive

        # Reachable both by a GOOGLE_SHEETS org connecting Drive for the
        # first time, and by an OUR_DATABASE org switching to it (see
        # GoogleDriveOAuthCallbackView, which branches on storage_mode).
        if not _can_edit_org(request.user) or not drive.is_configured():
            raise Http404

        state = secrets.token_urlsafe(32)
        request.session["drive_oauth_state"] = state
        redirect_uri = request.build_absolute_uri(reverse("accounts:drive_oauth_callback"))
        return HttpResponseRedirect(drive.build_authorization_url(redirect_uri=redirect_uri, state=state))


class GoogleDriveOAuthCallbackView(LoginRequiredMixin, View):
    """Google redirects here with ?code=... after the client approves
    (or ?error=... if they decline). Exchanges the code for tokens,
    stores them, then either:

    - org.storage_mode is already GOOGLE_SHEETS with no Sheet yet
      (brand-new signup, or an org that disconnected before ever
      finishing provisioning): provisions a fresh, empty live Sheet.
    - org.storage_mode is OUR_DATABASE (the owner clicked "Switch to
      Google Drive" on Profile): migrates every row currently in our
      Postgres tables into a brand-new Sheet, then flips storage_mode —
      see apps.organizations.storage_migration.
    """

    def get(self, request, *args, **kwargs):
        import datetime

        from django.urls import reverse
        from django.utils import timezone

        from apps.organizations import google_drive_client as drive
        from apps.organizations.drive_sync import get_valid_access_token
        from apps.organizations.encryption import encrypt_secret
        from apps.organizations.models import CloudBackupConnection, Organization

        user = request.user
        if not _can_edit_org(user):
            return redirect("accounts:profile")

        if request.GET.get("error"):
            messages.error(request, "Google Drive connection was cancelled.")
            return redirect("accounts:profile")

        expected_state = request.session.pop("drive_oauth_state", None)
        if not expected_state or request.GET.get("state") != expected_state:
            messages.error(request, "That Google Drive connection link expired. Please try again.")
            return redirect("accounts:profile")

        redirect_uri = request.build_absolute_uri(reverse("accounts:drive_oauth_callback"))
        try:
            tokens = drive.exchange_code_for_tokens(code=request.GET.get("code", ""), redirect_uri=redirect_uri)
        except drive.GoogleDriveError as exc:
            messages.error(request, str(exc))
            return redirect("accounts:profile")

        connection, _ = CloudBackupConnection.objects.update_or_create(
            organization=user.organization,
            defaults={
                "access_token_encrypted": encrypt_secret(tokens["access_token"]),
                "refresh_token_encrypted": encrypt_secret(tokens["refresh_token"]),
                "token_expires_at": timezone.now() + datetime.timedelta(seconds=tokens["expires_in"]),
                "external_folder_id": "",
                "last_sync_error": "",
            },
        )

        if user.organization.storage_mode == Organization.StorageMode.OUR_DATABASE:
            # Switching an already-live org from our own database to
            # Google Sheets — migrate its existing data into the new
            # Sheet rather than seeding blank defaults.
            from apps.organizations.storage_migration import migrate_our_database_to_google_sheets

            try:
                access_token = get_valid_access_token(connection)
                folder_id = drive.ensure_backup_folder(access_token)
                spreadsheet_id = migrate_our_database_to_google_sheets(
                    user.organization, access_token=access_token, folder_id=folder_id
                )
            except Exception as exc:
                messages.error(request, f"Connected, but couldn't migrate your data to Google Sheets: {exc}")
                return redirect("accounts:profile")
            connection.external_folder_id = folder_id
            connection.external_file_id = spreadsheet_id
            connection.last_synced_at = timezone.now()
            connection.save(update_fields=["external_folder_id", "external_file_id", "last_synced_at"])
            user.organization.storage_mode = Organization.StorageMode.GOOGLE_SHEETS
            user.organization.save(update_fields=["storage_mode"])
            from apps.organizations.models import DataStorageChangeRequest

            DataStorageChangeRequest.objects.filter(
                organization=user.organization,
                status=DataStorageChangeRequest.Status.APPROVED,
            ).update(status=DataStorageChangeRequest.Status.COMPLETED, completed_at=timezone.now())
            messages.success(request, "Switched to Google Drive — your existing data has been copied over.")
            return redirect("accounts:profile")

        # Provisioning a brand-new org — creating its live Sheet — is
        # deferred until this exact moment (see services.create_
        # organization_with_tenant_schema_and_admin), so the gate in
        # TenantSchemaMiddleware has something to wait for. Guarded by
        # external_file_id so reconnecting later doesn't re-run it and
        # silently create a second spreadsheet.
        if not connection.external_file_id:
            from apps.sheets_store.provisioning import provision_sheet_tenant

            try:
                access_token = get_valid_access_token(connection)
                folder_id = drive.ensure_backup_folder(access_token)
                spreadsheet_id = provision_sheet_tenant(
                    user.organization, access_token=access_token, folder_id=folder_id
                )
            except Exception as exc:
                messages.error(request, f"Connected, but couldn't set up your Google Sheet: {exc}")
                return redirect("accounts:profile")
            connection.external_folder_id = folder_id
            connection.external_file_id = spreadsheet_id
            connection.last_synced_at = timezone.now()
            connection.save(update_fields=["external_folder_id", "external_file_id", "last_synced_at"])
        messages.success(request, "Google Drive connected — your database is ready.")
        return redirect("finance:dashboard")


class SwitchToOurDatabaseView(LoginRequiredMixin, View):
    """Switches an already-live GOOGLE_SHEETS org to StorageMode.
    OUR_DATABASE — no Google OAuth round trip needed (already connected),
    so this is a single POST, unlike the reverse direction (see
    ConnectGoogleDriveView / GoogleDriveOAuthCallbackView, which it has
    to go through since creating the new Sheet needs a fresh consent)."""

    def post(self, request, *args, **kwargs):
        from django.http import Http404, JsonResponse

        from apps.organizations import google_drive_client as drive
        from apps.organizations.encryption import decrypt_secret
        from apps.organizations.models import Organization
        from apps.organizations.storage_migration import StorageMigrationError, migrate_google_sheets_to_our_database

        user = request.user
        org = user.organization
        if (
            not _can_edit_org(user)
            or org.storage_mode != Organization.StorageMode.GOOGLE_SHEETS
            or not hasattr(org, "cloud_backup")
        ):
            raise Http404

        try:
            migrate_google_sheets_to_our_database(org)
        except StorageMigrationError as exc:
            messages.error(request, f"Couldn't switch database: {exc}")
            return redirect("accounts:profile")

        org.storage_mode = Organization.StorageMode.OUR_DATABASE
        org.save(update_fields=["storage_mode"])

        connection = org.cloud_backup
        try:
            drive.revoke_token(decrypt_secret(connection.refresh_token_encrypted))
        except Exception:
            pass  # best-effort — the switch itself matters more than revocation succeeding
        connection.delete()

        messages.success(request, "Switched to Prism Pulse's own database — your existing data has been copied over.")
        return redirect("accounts:profile")


class DisconnectGoogleDriveView(LoginRequiredMixin, View):
    def post(self, request, *args, **kwargs):
        from django.http import Http404, JsonResponse
        from django.utils import timezone

        from apps.organizations import google_drive_client as drive
        from apps.organizations.encryption import decrypt_secret, encrypt_secret
        from apps.organizations.models import CloudBackupConnection, Organization

        if not _can_edit_org(request.user):
            raise Http404

        org = request.user.organization
        connection = CloudBackupConnection.objects.filter(organization=org).first()
        if connection is not None:
            try:
                drive.revoke_token(decrypt_secret(connection.refresh_token_encrypted))
            except Exception:
                pass  # best-effort — disconnecting locally matters more than revocation succeeding

            if org.storage_mode == Organization.StorageMode.GOOGLE_SHEETS:
                # This org's Sheet is still its live database — deleting
                # the row would lose the only pointer to it, so a later
                # reconnect would read as "brand new" and provision a
                # second, empty spreadsheet (see GoogleDriveOAuthCallback
                # View's `if not connection.external_file_id` branch),
                # orphaning the client's real data. Invalidate just the
                # tokens instead; external_file_id/external_folder_id
                # stay put, so reconnecting restores access to this
                # same spreadsheet.
                connection.access_token_encrypted = encrypt_secret("")
                connection.refresh_token_encrypted = encrypt_secret("")
                connection.token_expires_at = timezone.now()
                connection.last_sync_error = "Disconnected — click Connect Google Drive to resume."
                connection.save(update_fields=[
                    "access_token_encrypted", "refresh_token_encrypted", "token_expires_at", "last_sync_error",
                ])
            else:
                connection.delete()
        messages.success(request, "Google Drive disconnected.")
        return redirect("accounts:profile")


class ResetManagerPasswordView(LoginRequiredMixin, View):
    """JSON endpoint behind the profile page's "Reset manager password" pop-up."""

    def post(self, request, *args, **kwargs):
        user = request.user
        if not user.organization_id or user.role not in (User.Role.OWNER, User.Role.ADMIN):
            return JsonResponse({"errors": {"__all__": ["You don't have permission to do this."]}}, status=403)
        manager = User.objects.filter(organization=user.organization, role=User.Role.MANAGER).first()
        if manager is None:
            return JsonResponse({"errors": {"__all__": ["No manager account to reset."]}}, status=404)
        form = ResetManagerPasswordForm(request.POST)
        if not form.is_valid():
            return JsonResponse({"errors": {k: [str(e) for e in v] for k, v in form.errors.items()}}, status=400)
        manager.set_password(form.cleaned_data["new_password"])
        manager.save(update_fields=["password"])
        return JsonResponse({"ok": True, "message": f"Password reset for {manager.email}."})


class SuspendedView(LoginRequiredMixin, View):
    """The one screen a suspended organization's users ever see."""

    def get(self, request, *args, **kwargs):
        org = request.user.organization
        if org is None or not billing_services.is_account_suspended(org):
            return redirect("finance:dashboard")
        return render(request, "accounts/suspended.html", {"org": org})


class LogoutView(LoginRequiredMixin, View):
    def post(self, request, *args, **kwargs):
        logout(request)
        return redirect("core:landing")

    def get(self, request, *args, **kwargs):
        logout(request)
        return redirect("core:landing")


class CreateManagerAccountView(LoginRequiredMixin, FormView):
    """Create a new manager account for the current organization.

    Only OWNER and ADMIN users can create manager accounts, and only if:
    1. The organization has an eligible plan (Business or Business Drive)
    2. The organization has available manager account slots
    """
    template_name = "accounts/create_manager.html"
    form_class = CreateManagerAccountForm
    success_url = reverse_lazy("accounts:profile")

    def dispatch(self, request, *args, **kwargs):
        user = request.user
        if not user.organization_id or user.role not in (User.Role.OWNER, User.Role.ADMIN):
            raise Http404
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["organization"] = self.request.user.organization
        return context

    def form_valid(self, form):
        organization = self.request.user.organization

        try:
            validate_manager_account_creation(organization)
        except ManagerAccountError as exc:
            form.add_error(None, str(exc))
            return self.form_invalid(form)

        data = form.cleaned_data
        manager = User.objects.create_user(
            email=data["email"],
            password=data["password"],
            username=data["username"],
            first_name=data["first_name"],
            last_name=data["last_name"],
            organization=organization,
            role=User.Role.MANAGER,
        )

        messages.success(
            self.request,
            f"Manager account '{manager.email}' has been created successfully. "
            "They can now log in with their username/email and password."
        )
        return super().form_valid(form)
