from django.shortcuts import redirect

from apps.billing import services as billing_services

# Everything a suspended client may still reach: the suspended screen itself,
# signing out, and static assets for rendering it.
EXEMPT_PREFIXES = (
    "/accounts/suspended/",
    "/accounts/logout/",
    "/static/",
    "/media/",
)


class SuspendedAccountMiddleware:
    """Signed-in users of a suspended organization are sent to the single
    "account suspended" page whatever URL they ask for. Must sit after
    AuthenticationMiddleware (and before TenantSchemaMiddleware, so no
    tenant data is touched for them)."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = request.user
        if (
            user.is_authenticated
            and getattr(user, "organization_id", None)
            and not request.path.startswith(EXEMPT_PREFIXES)
            and billing_services.is_account_suspended(user.organization)
        ):
            return redirect("accounts:suspended")
        return self.get_response(request)
