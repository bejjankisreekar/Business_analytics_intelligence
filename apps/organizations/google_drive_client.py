"""Thin wrapper around Google's OAuth2 token endpoints and the Drive API
v3 — the only place either is touched, mirroring apps.billing.
razorpay_client's shape. Uses `requests` directly rather than Google's
client SDKs, to keep this a small, auditable surface: three HTTP calls
(authorize, token exchange/refresh, upload) against documented REST
endpoints.

Scope is drive.file only — this app can see/write just the files and
folders *it* creates, never the rest of a client's Drive. That's also
why no separate "list the client's folders" flow exists: the backup
folder is always one this app created and remembers the id of.
"""
from __future__ import annotations

import json

import requests
from django.conf import settings

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
DRIVE_FILES_URL = "https://www.googleapis.com/drive/v3/files"
DRIVE_UPLOAD_URL = "https://www.googleapis.com/upload/drive/v3/files"
SCOPE = "https://www.googleapis.com/auth/drive.file"

BACKUP_FOLDER_NAME = "Prism Pulse Backups"


class GoogleDriveError(RuntimeError):
    pass


def is_configured() -> bool:
    return bool(settings.GOOGLE_OAUTH_CLIENT_ID and settings.GOOGLE_OAUTH_CLIENT_SECRET)


def build_authorization_url(*, redirect_uri: str, state: str) -> str:
    params = {
        "client_id": settings.GOOGLE_OAUTH_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": SCOPE,
        "access_type": "offline",
        # Forces Google to hand back a refresh_token every time, even for
        # an account that connected before — without this, reconnecting
        # silently stops giving us one after the first authorization.
        "prompt": "consent",
        "state": state,
    }
    return AUTH_URL + "?" + "&".join(f"{k}={requests.utils.quote(str(v))}" for k, v in params.items())


def exchange_code_for_tokens(*, code: str, redirect_uri: str) -> dict:
    """Returns {"access_token", "refresh_token", "expires_in"} or raises
    GoogleDriveError with a client-facing message."""
    resp = requests.post(
        TOKEN_URL,
        data={
            "code": code,
            "client_id": settings.GOOGLE_OAUTH_CLIENT_ID,
            "client_secret": settings.GOOGLE_OAUTH_CLIENT_SECRET,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        },
        timeout=10,
    )
    if not resp.ok:
        raise GoogleDriveError(f"Google rejected the connection: {resp.text[:300]}")
    data = resp.json()
    if "refresh_token" not in data:
        raise GoogleDriveError(
            "Google didn't return a refresh token — please disconnect any prior access at "
            "myaccount.google.com/permissions and try connecting again."
        )
    return data


def refresh_access_token(refresh_token: str) -> dict:
    """Returns {"access_token", "expires_in"} for a stored refresh_token."""
    resp = requests.post(
        TOKEN_URL,
        data={
            "refresh_token": refresh_token,
            "client_id": settings.GOOGLE_OAUTH_CLIENT_ID,
            "client_secret": settings.GOOGLE_OAUTH_CLIENT_SECRET,
            "grant_type": "refresh_token",
        },
        timeout=10,
    )
    if not resp.ok:
        raise GoogleDriveError(f"Couldn't refresh your Google Drive connection: {resp.text[:300]}")
    return resp.json()


def revoke_token(token: str) -> None:
    """Best-effort: tells Google this app no longer needs access. Raises
    on a network-level failure; the caller treats that as non-fatal,
    since disconnecting locally matters more than revocation succeeding.
    """
    requests.post("https://oauth2.googleapis.com/revoke", params={"token": token}, timeout=10)


def _headers(access_token: str) -> dict:
    return {"Authorization": f"Bearer {access_token}"}


def ensure_backup_folder(access_token: str) -> str:
    """Finds this app's backup folder in the client's Drive, creating it
    the first time. Returns the folder's file id."""
    query = (
        f"name='{BACKUP_FOLDER_NAME}' and mimeType='application/vnd.google-apps.folder' "
        "and trashed=false"
    )
    resp = requests.get(
        DRIVE_FILES_URL, headers=_headers(access_token), params={"q": query, "fields": "files(id)"}, timeout=10
    )
    if not resp.ok:
        raise GoogleDriveError(f"Couldn't look up your Drive backup folder: {resp.text[:300]}")
    files = resp.json().get("files", [])
    if files:
        return files[0]["id"]

    resp = requests.post(
        DRIVE_FILES_URL,
        headers=_headers(access_token),
        json={"name": BACKUP_FOLDER_NAME, "mimeType": "application/vnd.google-apps.folder"},
        timeout=10,
    )
    if not resp.ok:
        raise GoogleDriveError(f"Couldn't create your Drive backup folder: {resp.text[:300]}")
    return resp.json()["id"]


def _find_file(access_token: str, *, folder_id: str, filename: str) -> str | None:
    query = f"name='{filename}' and '{folder_id}' in parents and trashed=false"
    resp = requests.get(
        DRIVE_FILES_URL, headers=_headers(access_token), params={"q": query, "fields": "files(id)"}, timeout=10
    )
    if not resp.ok:
        raise GoogleDriveError(f"Couldn't look up '{filename}' in your Drive backup folder: {resp.text[:300]}")
    files = resp.json().get("files", [])
    return files[0]["id"] if files else None


def upload_or_replace_file(
    access_token: str,
    *,
    folder_id: str,
    filename: str,
    content: bytes,
    mimetype: str,
    convert_to_sheet: bool = False,
) -> str:
    """Creates `filename` in `folder_id`, or overwrites it in place if it
    already exists from a previous sync. Returns the file id.

    `convert_to_sheet=True` uploads `content` as `mimetype` (an .xlsx
    workbook) but has Drive *import* it into a real, native Google
    Sheet rather than storing the raw file — this is what makes the
    result something the client can actually open and edit at
    sheets.google.com, not a file they'd have to download first. Drive
    does this import automatically whenever the target file's type is
    `application/vnd.google-apps.spreadsheet`, on both create (set via
    metadata) and update (triggered by the uploaded Content-Type
    differing from the file's native type).
    """
    existing_id = _find_file(access_token, folder_id=folder_id, filename=filename)

    if existing_id:
        resp = requests.patch(
            f"{DRIVE_UPLOAD_URL}/{existing_id}",
            headers={**_headers(access_token), "Content-Type": mimetype},
            params={"uploadType": "media"},
            data=content,
            timeout=60,
        )
        if not resp.ok:
            raise GoogleDriveError(f"Couldn't update '{filename}' in your Drive: {resp.text[:300]}")
        return resp.json()["id"]

    metadata = {"name": filename, "parents": [folder_id]}
    if convert_to_sheet:
        metadata["mimeType"] = "application/vnd.google-apps.spreadsheet"
    resp = requests.post(
        DRIVE_UPLOAD_URL,
        headers=_headers(access_token),
        params={"uploadType": "multipart"},
        files={
            "metadata": (None, json.dumps(metadata), "application/json"),
            "file": (filename, content, mimetype),
        },
        timeout=60,
    )
    if not resp.ok:
        raise GoogleDriveError(f"Couldn't upload '{filename}' to your Drive: {resp.text[:300]}")
    return resp.json()["id"]
