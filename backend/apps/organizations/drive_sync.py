"""Token refresh for an org's connected Google Drive — used both by
apps.organizations.middleware to open a live SheetSession, and by the
initial Sheets provisioning in GoogleDriveOAuthCallbackView. There is no
separate backup/sync step: an org's connected Google Sheet already *is*
its live database, read and written directly on every request (see
apps.sheets_store).
"""
from __future__ import annotations

import datetime

from django.utils import timezone

from . import google_drive_client as drive
from .encryption import decrypt_secret, encrypt_secret
from .models import CloudBackupConnection


class DriveSyncError(RuntimeError):
    pass


def get_valid_access_token(connection: CloudBackupConnection) -> str:
    """A live access token for `connection`, refreshing it first if it's
    expired or about to be (within 2 minutes)."""
    if connection.token_expires_at > timezone.now() + datetime.timedelta(minutes=2):
        return decrypt_secret(connection.access_token_encrypted)

    refresh_token = decrypt_secret(connection.refresh_token_encrypted)
    if not refresh_token:
        # Disconnected (tokens blanked, Sheet pointer kept): nothing to send
        # Google, and a failed request would overwrite the "Disconnected"
        # note with a confusing "Missing required parameter" error.
        raise DriveSyncError("Disconnected — click Connect Google Drive to resume.")
    try:
        tokens = drive.refresh_access_token(refresh_token)
    except drive.GoogleDriveError as exc:
        connection.last_sync_error = str(exc)
        connection.save(update_fields=["last_sync_error"])
        raise DriveSyncError(str(exc)) from exc

    connection.access_token_encrypted = encrypt_secret(tokens["access_token"])
    connection.token_expires_at = timezone.now() + datetime.timedelta(seconds=tokens["expires_in"])
    connection.last_sync_error = ""
    connection.save(update_fields=["access_token_encrypted", "token_expires_at", "last_sync_error"])
    return tokens["access_token"]
