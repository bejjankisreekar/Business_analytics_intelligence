from django.shortcuts import redirect, render

from . import google_drive_client as drive
from .models import Organization, org_needs_drive_connection

# Paths reachable even while a GOOGLE_SHEETS org is gated on connecting
# Google Drive (see TenantSchemaMiddleware._needs_drive_gate) — without
# these, the owner could never reach the "Connect Google Drive" button
# or sign out.
GATE_EXEMPT_PREFIXES = (
    "/accounts/connect-database/",
    "/accounts/profile/drive/",
    "/accounts/logout/",
    "/static/",
    "/media/",
)


class TenantSchemaMiddleware:
    """Points `apps.finance` at the current user's organization data for
    the duration of the request, depending on Organization.storage_mode:

    - GOOGLE_SHEETS: opens a request-scoped apps.sheets_store.session.
      SheetSession bound to the org's connected spreadsheet, so
      apps.finance code reads/writes that Sheet live. Nothing here ever
      touches our own database for this org's finance data. Gated
      behind connecting Google Drive first (see _needs_drive_gate) —
      provisioning the Sheet itself is deferred to that connection
      completing (GoogleDriveOAuthCallbackView).
    - OUR_DATABASE: sets the "current organization" (apps.organizations.
      tenant_context) so SheetAwareManager/SheetAwareModelMixin
      transparently scope every apps.finance query/save to this org's
      own rows in our shared Postgres tables. No Google account or
      gate involved at all.

    Must sit after AuthenticationMiddleware.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def _needs_drive_gate(self, org) -> bool:
        return drive.is_configured() and org_needs_drive_connection(org)

    def _activate_sheets_session(self, org) -> None:
        from apps.organizations.drive_sync import get_valid_access_token
        from apps.sheets_store.session import SheetSession, set_active_session

        connection = org.cloud_backup
        access_token = get_valid_access_token(connection)
        set_active_session(SheetSession(access_token, connection.external_file_id))

    def __call__(self, request):
        from apps.organizations.drive_sync import DriveSyncError
        from apps.organizations.tenant_context import set_current_organization
        from apps.sheets_store.session import set_active_session as set_active_sheet_session

        org = getattr(getattr(request, "user", None), "organization", None)

        if org and self._needs_drive_gate(org) and not request.path.startswith(GATE_EXEMPT_PREFIXES):
            return redirect("accounts:connect_database_gate")

        try:
            if org and org.storage_mode == Organization.StorageMode.GOOGLE_SHEETS and hasattr(org, "cloud_backup") and org.cloud_backup.external_file_id:
                try:
                    self._activate_sheets_session(org)
                except DriveSyncError:
                    # The connection's token can no longer be refreshed
                    # (revoked at myaccount.google.com, expired, Google
                    # password changed) — don't let that 500 every page
                    # for this org; the owner still needs to reach
                    # Profile to disconnect/reconnect. Fall back to the
                    # same org-scoped (empty, for a GOOGLE_SHEETS org)
                    # Postgres context below rather than leaving nothing
                    # active at all.
                    set_current_organization(org)
            elif org:
                # Also the safety net whenever no Sheets session got
                # activated above for any reason (OUR_DATABASE org, a
                # GOOGLE_SHEETS org not yet connected reaching a
                # GATE_EXEMPT_PREFIXES page, or the token-refresh
                # failure just above) — apps.finance must never fall
                # through to a completely unfiltered query across every
                # organization's rows.
                set_current_organization(org)

            response = self.get_response(request)
        finally:
            set_active_sheet_session(None)
            set_current_organization(None)
        return response

    def process_exception(self, request, exception):
        # A Google Sheets API call can fail — show a clear message
        # instead of a bare 500.
        from apps.sheets_store.client import SheetsAPIError

        org = getattr(getattr(request, "user", None), "organization", None)
        if isinstance(exception, SheetsAPIError) and org:
            return render(
                request,
                "organizations/database_unreachable.html",
                {"org": org},
                status=503,
            )
        return None
