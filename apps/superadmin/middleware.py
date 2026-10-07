import logging

from django.db import DatabaseError
from django.shortcuts import render

logger = logging.getLogger(__name__)

ENV_LABELS = {"dev": "Development", "prod": "Production"}


class EnvironmentDatabaseErrorMiddleware:
    """When a superadmin page can't read its environment's database (it's
    unreachable, or not migrated to the current schema) show a plain
    "this environment isn't available" page instead of Django's technical
    error screen. The real exception is still logged for developers."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_exception(self, request, exception):
        if not isinstance(exception, DatabaseError) or not request.path.startswith("/superadmin/"):
            return None
        parts = request.path.strip("/").split("/")
        env = parts[1] if len(parts) > 1 and parts[1] in ENV_LABELS else None
        if env is None or not request.user.is_authenticated:
            return None
        logger.exception("Superadmin %s database error on %s", env, request.path, exc_info=exception)
        other = "dev" if env == "prod" else "prod"
        return render(
            request,
            "superadmin/env_unavailable.html",
            {
                "env_key": env,
                "env_label": ENV_LABELS[env],
                "other_env_key": other,
                "other_env_label": ENV_LABELS[other],
            },
            status=503,
        )
