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
        from apps.accounts.manager_pages import (
            ALWAYS_ALLOWED_URL_NAMES, CUSTOMER_REQUESTS_PAGE, CUSTOMER_REQUESTS_URL_NAMES, RESTRICTED_ROLES,
            _page_for_finance_url, landing_url, manager_can_open, org_pages_for, pages_for,
        )

        user = request.user
        match = request.resolver_match
        if not user.is_authenticated or match is None:
            return None

        # 1. A superadmin may have limited the whole organization to some pages.
        org_pages = org_pages_for(getattr(user, "organization", None))
        if org_pages is not None:
            if match.namespace == "finance":
                page = _page_for_finance_url(match.url_name)
                if match.url_name not in ALWAYS_ALLOWED_URL_NAMES and page not in org_pages:
                    return redirect(landing_url(user))
            elif match.namespace == "quote_requests":
                if match.url_name in CUSTOMER_REQUESTS_URL_NAMES and CUSTOMER_REQUESTS_PAGE not in org_pages:
                    return redirect(landing_url(user))

        # 2. A manager is limited to the pages their owner ticked.
        if user.role in RESTRICTED_ROLES and match.namespace == "finance":
            if not manager_can_open(pages_for(user), match.url_name):
                return redirect(landing_url(user))
        return None
