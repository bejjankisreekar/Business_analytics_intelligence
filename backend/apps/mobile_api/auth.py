"""Stateless bearer-token auth for the mobile app.

Tokens are signed (no table, no migration) and tied to the user's current
password hash, so changing the password signs the phone out. The middleware
sets `request.user` from the token *before* TenantSchemaMiddleware runs, which
is what lets the existing tenant isolation (Postgres rows or Google Sheets)
work unchanged for API calls.
"""

import functools

from django.contrib.auth import get_user_model
from django.core import signing
from django.http import JsonResponse

from apps.billing import services as billing_services

API_PREFIX = "/api/mobile/"
TOKEN_SALT = "mobile-api-token"
TOKEN_MAX_AGE = 60 * 60 * 24 * 30  # 30 days


def make_token(user) -> str:
    return signing.dumps({"uid": str(user.pk), "ph": user.password[-12:]}, salt=TOKEN_SALT)


def user_from_token(token: str):
    try:
        payload = signing.loads(token, salt=TOKEN_SALT, max_age=TOKEN_MAX_AGE)
    except signing.BadSignature:
        return None
    user = get_user_model().objects.filter(pk=payload.get("uid"), is_active=True).first()
    if user is None or user.password[-12:] != payload.get("ph"):
        return None
    return user


class MobileTokenAuthMiddleware:
    """Must sit right after AuthenticationMiddleware."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path.startswith(API_PREFIX):
            header = request.META.get("HTTP_AUTHORIZATION", "")
            if header.startswith("Bearer "):
                user = user_from_token(header[7:].strip())
                if user is not None:
                    request.user = user
        return self.get_response(request)


def _error(message: str, status: int, code: str):
    return JsonResponse({"error": message, "code": code}, status=status)


def api_login_required(view):
    """401 for a missing/expired token; 403 when the account or plan can't
    use the app right now (mirrors TenantLoginRequiredMixin, as JSON)."""

    @functools.wraps(view)
    def wrapper(request, *args, **kwargs):
        user = request.user
        if not user.is_authenticated:
            return _error("Sign in again.", 401, "unauthenticated")
        org = getattr(user, "organization", None)
        if org is None:
            return _error("No organization on this account.", 403, "no_organization")
        if not org.is_service_active or billing_services.is_account_suspended(org):
            return _error("This account is suspended.", 403, "suspended")
        if not billing_services.has_active_access(org):
            return _error("Your plan has lapsed. Renew on the web app.", 403, "billing_locked")
        # Managers only get what the owner ticked; entries live under "Daily Book".
        from apps.accounts.manager_pages import RESTRICTED_ROLES, pages_for

        if user.role in RESTRICTED_ROLES and "daily_report" not in pages_for(user):
            return _error("You do not have access to daily entries.", 403, "forbidden")
        return view(request, *args, **kwargs)

    return wrapper


def billing_login_required(view):
    """Like `api_login_required`, but a lapsed plan can still get through: the billing
    screen is where an unpaid account sees and pays the invoice that lifts the lock."""

    @functools.wraps(view)
    def wrapper(request, *args, **kwargs):
        user = request.user
        if not user.is_authenticated:
            return _error("Sign in again.", 401, "unauthenticated")
        org = getattr(user, "organization", None)
        if org is None:
            return _error("No organization on this account.", 403, "no_organization")
        from apps.accounts.manager_pages import RESTRICTED_ROLES

        if user.role in RESTRICTED_ROLES:
            return _error("Billing is only available to the account owner.", 403, "forbidden")
        return view(request, *args, **kwargs)

    return wrapper
