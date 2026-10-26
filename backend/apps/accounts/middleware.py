from django.shortcuts import redirect

from apps.billing import services as billing_services

# Everything a suspended client may still reach: the suspended screen itself,
# signing out, and static assets for rendering it.
EXEMPT_PREFIXES = (
    "/api/mobile/",  # JSON API: answers 403 itself instead of redirecting to HTML
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


class ManagerPageAccessMiddleware:
    """Manager accounts may only open the finance pages their owner ticked
    (see apps.accounts.manager_pages). Anything else is sent to a page they
    do have. Must sit after AuthenticationMiddleware, so request.user is set."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_view(self, request, view_func, view_args, view_kwargs):
        from apps.accounts.manager_pages import RESTRICTED_ROLES, first_allowed_url, manager_can_open, pages_for

        user = request.user
        match = request.resolver_match
        if not (user.is_authenticated and user.role in RESTRICTED_ROLES) or match is None:
            return None
        if match.namespace != "finance":
            return None
        pages = pages_for(user)
        if manager_can_open(pages, match.url_name):
            return None
        return redirect(first_allowed_url(pages))
