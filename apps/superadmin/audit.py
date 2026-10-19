"""The console's audit trail.

AuditLogMiddleware writes one AuditLogEntry for every superadmin change
that succeeded — any POST under /superadmin/ answered with a redirect
(the console's views all redirect after a successful change and re-render
the form, status 200, when it fails). Logging at this one choke point
means a new console action is audited without anyone remembering to.

The entry goes to the database of the environment the action was taken
in (the <env> in the URL). Its summary is the success message the view
showed ("Updated coupon SUMMER25."), and its details are the submitted
form fields minus anything secret.
"""
import logging
import re
import uuid

from django.conf import settings
from django.contrib import messages
from django.db import DatabaseError, transaction

logger = logging.getLogger(__name__)

ENVS = ("dev", "prod")
SECRET_HINTS = ("password", "token", "secret", "csrf", "otp")
MAX_VALUE = 300

# url_name prefix -> what kind of record the integer pk in the URL is.
TARGET_TYPES = (
    ("invoice", "Invoice"),
    ("payment", "Payment"),
    ("plan", "Plan"),
    ("coupon", "Coupon"),
    ("subscription_edit", "Subscription"),
    ("storage_change", "Storage request"),
    ("webhook", "Webhook event"),
    ("admin_", "Admin"),
)


def current_env() -> str:
    """The environment the `default` alias (and so sign-in) points at."""
    return "prod" if settings.IS_PRODUCTION else "dev"


def env_from_path(path: str) -> str | None:
    parts = path.strip("/").split("/")
    return parts[1] if len(parts) > 1 and parts[1] in ENVS else None


def client_ip(request) -> str | None:
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    return (forwarded.split(",")[0].strip() if forwarded else request.META.get("REMOTE_ADDR")) or None


def _clean_details(post) -> dict:
    out = {}
    for key in post:
        if any(h in key.lower() for h in SECRET_HINTS):
            continue
        values = [v[:MAX_VALUE] for v in post.getlist(key)]
        out[key] = values[0] if len(values) == 1 else values
    return out


def _exists(env, org) -> bool:
    from apps.organizations.models import Organization

    try:
        return Organization.objects.using(env).filter(pk=org.pk).exists()
    except DatabaseError:
        return False


def record(*, env, actor_email, action, summary="", organization=None, organization_name="", target_type="",
           target_id="", path="", details=None, ip_address=None):
    """Write one entry; never lets a logging failure break the action itself
    (e.g. an environment not migrated for the audit table yet)."""
    from .models import AuditLogEntry

    try:
        with transaction.atomic(using=env):
            AuditLogEntry.objects.using(env).create(
                actor_email=actor_email,
                action=action[:64],
                summary=summary[:255],
                organization=organization,
                organization_name=(organization_name or (organization.name if organization else ""))[:200],
                target_type=target_type,
                target_id=str(target_id)[:64],
                path=path[:255],
                details=details or {},
                ip_address=ip_address,
            )
    except DatabaseError:
        logger.exception("Couldn't write superadmin audit entry (%s, %s)", env, action)


def _organization(env, kwargs):
    from apps.organizations.models import Organization

    pk = kwargs.get("pk")
    if not isinstance(pk, uuid.UUID):
        return None
    try:
        return Organization.objects.using(env).filter(pk=pk).first()
    except DatabaseError:
        return None


def _target(url_name, kwargs):
    pk = kwargs.get("pk")
    if isinstance(pk, int):
        for prefix, label in TARGET_TYPES:
            if url_name.startswith(prefix):
                return label, pk
        return "", pk
    if kwargs.get("user_id"):
        return "User", kwargs["user_id"]
    if isinstance(pk, uuid.UUID):
        return "Client", pk
    return "", ""


class AuditLogMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        is_candidate = request.method == "POST" and request.path.startswith("/superadmin/")
        actor = None
        if is_candidate:
            user = getattr(request, "user", None)
            if user is not None and user.is_authenticated and getattr(user, "role", "") == "SUPER_ADMIN":
                actor = user.email
            # A superadmin ending an impersonation is signed in as the client
            # when the request arrives; the view stashes who they really are.
            actor = actor or request.session.get("impersonator_email")

        request._sa_audit_actor = actor
        response = self.get_response(request)

        if actor and 300 <= response.status_code < 400 and request.resolver_match:
            self._log(request, actor)
        return response

    def process_view(self, request, view_func, view_args, view_kwargs):
        # Look the client up before the view runs, so a deleted client's
        # entry still names who was deleted.
        if getattr(request, "_sa_audit_actor", None):
            env = view_kwargs.get("env") or env_from_path(request.path) or current_env()
            if env in ENVS:
                request._sa_audit_org = _organization(env, view_kwargs)
        return None

    def _log(self, request, actor):
        match = request.resolver_match
        if match.namespace != "superadmin" or getattr(request, "_sa_audited", False):
            return
        env = match.kwargs.get("env") or env_from_path(request.path) or current_env()
        if env not in ENVS:
            return
        queued = getattr(messages.get_messages(request), "_queued_messages", [])
        summary = " ".join(str(m) for m in queued if m.level >= messages.INFO)
        # Never keep a generated password that a success message showed once.
        summary = re.sub(r"(password:\s*)\S+", r"\g<1>[hidden]", summary, flags=re.I)
        org = getattr(request, "_sa_audit_org", None)
        target_type, target_id = _target(match.url_name, match.kwargs)
        record(
            env=env,
            actor_email=actor,
            action=match.url_name,
            summary=summary,
            organization=org if org is not None and org.pk and _exists(env, org) else None,
            organization_name=org.name if org is not None else "",
            target_type=target_type,
            target_id=target_id,
            path=request.path,
            details=_clean_details(request.POST),
            ip_address=client_ip(request),
        )


def humanize_action(action: str) -> str:
    return ACTION_LABELS.get(action, action.replace("_", " ").capitalize())


ACTION_LABELS = {
    "org_create": "Created client",
    "org_edit": "Edited client",
    "org_delete": "Deleted client",
    "edit_client_user": "Edited client user",
    "reset_client_password": "Reset client password",
    "subscription_create": "Changed subscription",
    "subscription_edit": "Edited subscription",
    "extend_trial": "Extended trial",
    "edit_key_dates": "Edited key dates",
    "grant_complimentary": "Granted complimentary plan",
    "service_start": "Started service",
    "service_stop": "Stopped service",
    "service_suspend": "Suspended service",
    "service_resume": "Resumed service",
    "cancel_autopay": "Cancelled autopay",
    "record_payment": "Recorded payment",
    "invoice_create": "Created invoice",
    "invoice_new": "Generated invoice",
    "client_invoice_new": "Generated invoice",
    "invoice_edit": "Edited invoice",
    "invoice_delete": "Deleted invoice",
    "invoice_record_payment": "Recorded invoice payment",
    "invoice_apply_coupon": "Applied coupon to invoice",
    "payment_edit": "Edited payment",
    "plan_create": "Created plan",
    "plan_edit": "Edited plan",
    "plan_toggle": "Toggled plan",
    "coupon_create": "Created coupon",
    "coupon_edit": "Edited coupon",
    "coupon_toggle": "Toggled coupon",
    "storage_change_detail": "Reviewed storage request",
    "client_note_add": "Added note",
    "client_note_delete": "Deleted note",
    "client_note_pin": "Pinned/unpinned note",
    "client_tag_add": "Added tag",
    "client_tag_remove": "Removed tag",
    "org_bulk_action": "Bulk action",
    "impersonate_start": "Logged in as client user",
    "impersonate_stop": "Returned from client login",
    "admin_create": "Created admin",
    "admin_toggle": "Activated/deactivated admin",
    "send_digest": "Sent alert digest",
}


def tables_ready(env: str) -> bool:
    """Whether this environment's database has the console's own tables
    (audit log, notes, tags) yet — False until `migrate --database <env>`
    has run there, in which case those features hide instead of erroring."""
    from django.db import connections

    from .models import AuditLogEntry, ClientNote, ClientTag

    try:
        with connections[env].cursor() as cursor:
            names = set(connections[env].introspection.table_names(cursor))
    except DatabaseError:
        return False
    return {m._meta.db_table for m in (AuditLogEntry, ClientNote, ClientTag)} <= names
