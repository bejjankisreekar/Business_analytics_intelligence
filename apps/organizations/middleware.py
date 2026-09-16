from .utils import set_schema_search_path


class TenantSchemaMiddleware:
    """Points the DB connection's search_path at the current user's
    organization schema for the duration of the request, so
    `apps.finance` queries transparently hit that organization's own
    isolated tables. Must sit after AuthenticationMiddleware.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        org = getattr(getattr(request, "user", None), "organization", None)
        set_schema_search_path(org.schema_name if org else None)
        try:
            response = self.get_response(request)
        finally:
            set_schema_search_path(None)
        return response
