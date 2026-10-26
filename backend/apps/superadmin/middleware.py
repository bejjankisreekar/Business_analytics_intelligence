import logging

from django.conf import settings
from django.db import DatabaseError
from django.http import Http404
from django.shortcuts import redirect, render

logger = logging.getLogger(__name__)

# What the superadmin host may serve: the console (with its own sign-in),
# sign-out and assets. Client sign-in/signup pages are deliberately absent.
ADMIN_HOST_PREFIXES = ("/superadmin/", "/accounts/logout/", "/static/", "/media/", "/robots.txt")


class SuperadminHostMiddleware:
    """Pins the superadmin console to its own hostname (settings.SUPERADMIN_HOST,
    e.g. admin.findayintelligence.com). On that host only the console and
    sign-in are served — anything else redirects to the console. On every other
    host /superadmin/ is a 404. A blank SUPERADMIN_HOST disables all of this."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        admin_host = settings.SUPERADMIN_HOST
        if not admin_host:
            return self.get_response(request)
        host = request.get_host().split(":")[0].lower()
        if host == admin_host:
            # While impersonating a client the whole client app must load here.
            if request.session.get("impersonator_id"):
                return self.get_response(request)
            if not request.path.startswith(ADMIN_HOST_PREFIXES):
                return redirect("/superadmin/login/")
        elif request.path.startswith("/superadmin/"):
            raise Http404
        return self.get_response(request)


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
