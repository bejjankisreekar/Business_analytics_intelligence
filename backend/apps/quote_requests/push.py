"""Push notifications to the shop owner's phone, through Firebase Cloud Messaging (HTTP v1).

Needs a Firebase service account. Set ONE of these in the server's environment:
    FCM_SERVICE_ACCOUNT_JSON   the whole service-account JSON file, as one line
    FCM_SERVICE_ACCOUNT_FILE   path to that JSON file
Without it, pushes are skipped quietly and everything else (email, the app's own screens) still works.

No extra Python package is used: the service account signs a short-lived login token with the
`cryptography` library the project already has, and `requests` talks to Google.
"""

import base64
import json
import logging
import threading
import time

import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from decouple import config

from .models import DeviceToken

logger = logging.getLogger(__name__)

FCM_SCOPE = "https://www.googleapis.com/auth/firebase.messaging"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
CHANNEL_ID = "customer_requests"      # the Android notification channel the app creates

_lock = threading.Lock()
_cached = {"email": None, "token": None, "expires": 0.0}


def credentials():
    """The service-account details, or None when push is not set up (or the setting is unusable)."""
    raw = config("FCM_SERVICE_ACCOUNT_JSON", default="").strip()
    if not raw:
        path = config("FCM_SERVICE_ACCOUNT_FILE", default="").strip()
        if path:
            try:
                with open(path, encoding="utf-8") as handle:
                    raw = handle.read()
            except OSError:
                logger.warning("FCM_SERVICE_ACCOUNT_FILE could not be read: %s", path)
                return None
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        logger.warning("The Firebase service-account setting is not valid JSON")
        return None
    if not all(data.get(key) for key in ("client_email", "private_key", "project_id")):
        logger.warning("The Firebase service-account JSON is missing client_email, private_key or project_id")
        return None
    return data


def is_enabled() -> bool:
    return credentials() is not None


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _access_token(creds) -> str:
    """A Google access token for FCM, reused until a minute before it expires."""
    with _lock:
        now = time.time()
        if _cached["email"] == creds["client_email"] and _cached["expires"] - 60 > now:
            return _cached["token"]
        header = {"alg": "RS256", "typ": "JWT"}
        claim = {
            "iss": creds["client_email"], "scope": FCM_SCOPE, "aud": GOOGLE_TOKEN_URL,
            "iat": int(now), "exp": int(now) + 3600,
        }
        signing_input = f"{_b64(json.dumps(header).encode())}.{_b64(json.dumps(claim).encode())}"
        key = serialization.load_pem_private_key(creds["private_key"].encode(), password=None)
        signature = key.sign(signing_input.encode(), padding.PKCS1v15(), hashes.SHA256())
        response = requests.post(
            GOOGLE_TOKEN_URL,
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                "assertion": f"{signing_input}.{_b64(signature)}",
            },
            timeout=10,
        )
        response.raise_for_status()
        body = response.json()
        _cached.update(
            email=creds["client_email"], token=body["access_token"], expires=now + int(body.get("expires_in", 3600)),
        )
        return _cached["token"]


def send_to_tokens(tokens, title: str, body: str, data: dict) -> None:
    """Sends one notification to each phone. A phone that has uninstalled the app (Google answers
    "unregistered") is forgotten. Never raises."""
    creds = credentials()
    if creds is None or not tokens:
        return
    try:
        access = _access_token(creds)
    except Exception:
        logger.exception("Could not log in to Firebase Cloud Messaging")
        return

    url = f"https://fcm.googleapis.com/v1/projects/{creds['project_id']}/messages:send"
    gone = []
    for token in tokens:
        message = {
            "token": token,
            "notification": {"title": title, "body": body},
            "data": {key: str(value) for key, value in data.items()},
            "android": {"priority": "HIGH", "notification": {"channel_id": CHANNEL_ID, "sound": "default"}},
        }
        try:
            response = requests.post(
                url, json={"message": message}, headers={"Authorization": f"Bearer {access}"}, timeout=10)
        except requests.RequestException:
            logger.exception("Could not reach Firebase Cloud Messaging")
            continue
        if response.status_code == 404 or "UNREGISTERED" in response.text:
            gone.append(token)
        elif response.status_code >= 300:
            logger.warning("Firebase rejected a notification (%s): %s", response.status_code, response.text[:300])
    if gone:
        DeviceToken.objects.filter(token__in=gone).delete()


def prepare_for_request(req, *, updated: bool):
    """Work out who to notify (this needs the database, so it runs in the request) and return a
    function that sends the notifications (which only talks to Google, so it can run in the
    background). None when push is off or nobody has the app signed in."""
    if not is_enabled():
        return None
    from apps.accounts.models import User

    from .access import user_has_access

    org = req.organization
    devices = DeviceToken.objects.filter(
        user__organization=org, user__is_active=True,
        user__role__in=(User.Role.OWNER, User.Role.ADMIN, User.Role.MANAGER),
    ).select_related("user")
    tokens = [d.token for d in devices if user_has_access(d.user)]
    if not tokens:
        return None

    count = req.items.count()
    title = f"Request #{req.number} updated" if updated else f"New request #{req.number}"
    body = f"{req.customer_name} - {count} item{'s' if count != 1 else ''}"
    data = {"type": "customer_request", "number": req.number, "updated": "1" if updated else "0"}
    return lambda: send_to_tokens(tokens, title, body, data)
