"""Console pages and actions beyond the core client/billing screens: bulk
actions, internal notes & tags, logging in as a client user, global search,
the audit log, payment webhooks, system health, platform admins and coupon
detail."""
import datetime
import logging
import platform
import time

import django
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.db import DatabaseError, IntegrityError, connections, transaction
from django.db.migrations.executor import MigrationExecutor
from django.db.models import Count, Q, Sum
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.generic import TemplateView, View

from apps.accounts.mixins import SuperAdminRequiredMixin
from apps.accounts.models import User
from apps.billing import services as billing_services
from apps.billing.models import Coupon, CouponRedemption, Invoice, Payment, PaymentWebhookEvent, Subscription
from apps.organizations import service_control
from apps.organizations.models import CloudBackupConnection, Organization, ServiceStatusChange

from .audit import client_ip, current_env, humanize_action, record, tables_ready
from .listing import export_response, paginate, sorted_qs, wants_export
from .models import AuditLogEntry, ClientNote, ClientTag
from .views import BULK_ACTIONS, ENVIRONMENTS, _env_label_or_404, coupon_state

logger = logging.getLogger(__name__)

BULK_KEYS = {key for key, _label in BULK_ACTIONS}


def _base_context(env):
    return {"env_key": env, "env_label": _env_label_or_404(env)}


# --------------------------------------------------------------- bulk actions
class OrgBulkActionView(SuperAdminRequiredMixin, View):
    """Apply one action to every client ticked on the client list."""

    def post(self, request, env):
        _env_label_or_404(env)
        action = request.POST.get("action", "")
        ids = request.POST.getlist("ids")
        back = request.POST.get("next") or reverse("superadmin:org_list", args=[env])
        if not back.startswith("/superadmin/"):
            back = reverse("superadmin:org_list", args=[env])
        if action not in BULK_KEYS or not ids:
            messages.error(request, "Pick at least one client and an action.")
            return redirect(back)
        orgs = list(Organization.objects.using(env).filter(pk__in=ids))

        if action == "export":
            from .health import score, usage_for

            usage = usage_for(env, [o.id for o in orgs])
            subs = {s.organization_id: s for s in Subscription.objects.using(env)
                    .filter(organization_id__in=[o.id for o in orgs], is_current=True).select_related("plan")}
            rows = []
            for o in orgs:
                sub, u = subs.get(o.id), usage[o.id]
                h = score(o, sub, u)
                rows.append([o.name, o.organization_code, o.contact_person, o.contact_email,
                             o.get_storage_mode_display(), sub.plan.name if sub else "",
                             sub.get_status_display() if sub else "", o.get_service_status_display(),
                             u["users"], u["last_login"], u["entries_30d"], h["label"], h["score"]])
            return export_response("xlsx", f"clients-selected-{env}", [
                "Client", "Code", "Contact", "Email", "Storage", "Plan", "Subscription", "Service", "Users",
                "Last login", "Entries (30d)", "Health", "Score"], rows)

        done, skipped = 0, 0
        if action == "extend_trial":
            try:
                days = int(request.POST.get("days", "0"))
            except ValueError:
                days = 0
            if not 1 <= days <= 365:
                messages.error(request, "Enter a number of days between 1 and 365.")
                return redirect(back)
            for o in orgs:
                sub = billing_services.get_current_subscription(o.id, using=env)
                if sub is None or sub.status != Subscription.Status.TRIAL:
                    skipped += 1
                    continue
                base = max(sub.trial_end_date or timezone.localdate(), timezone.localdate())
                billing_services.extend_trial(sub, new_trial_end_date=base + datetime.timedelta(days=days), using=env)
                done += 1
            verb = f"Extended the trial by {days} days for"
        elif action in ("suspend", "resume"):
            reason = request.POST.get("reason") or ServiceStatusChange.Reason.ADMINISTRATIVE
            if reason not in ServiceStatusChange.Reason.values:
                reason = ServiceStatusChange.Reason.ADMINISTRATIVE
            for o in orgs:
                if action == "suspend" and o.is_service_active:
                    service_control.suspend_service(o, using=env, admin_email=request.user.email, reason=reason,
                                                    notes="Bulk action")
                    done += 1
                elif action == "resume" and not o.is_service_active:
                    service_control.resume_service(o, using=env, admin_email=request.user.email, notes="Bulk action")
                    done += 1
                else:
                    skipped += 1
            verb = "Suspended service for" if action == "suspend" else "Resumed service for"
        else:  # add_tag / remove_tag
            if not tables_ready(env):
                messages.error(request, NOT_MIGRATED.format(env=env))
                return redirect(back)
            name = request.POST.get("tag", "").strip()[:32]
            if not name:
                messages.error(request, "Enter a tag name.")
                return redirect(back)
            for o in orgs:
                if action == "add_tag":
                    _, created = ClientTag.objects.using(env).get_or_create(
                        organization=o, name=name, defaults={"color": request.POST.get("color") or "slate"})
                else:
                    created = ClientTag.objects.using(env).filter(organization=o, name=name).delete()[0] > 0
                done += 1 if created else 0
                skipped += 0 if created else 1
            verb = f"Tagged “{name}” on" if action == "add_tag" else f"Removed tag “{name}” from"

        msg = f"{verb} {done} client{'s' if done != 1 else ''}."
        if skipped:
            msg += f" {skipped} skipped (not applicable)."
        messages.success(request, msg)
        return redirect(back)


# --------------------------------------------------------------- notes & tags
NOT_MIGRATED = (
    "Notes and tags aren't set up in this environment's database yet — run "
    "`python manage.py migrate --database {env}` first."
)


class _OrgAction(SuperAdminRequiredMixin, View):
    def dispatch(self, request, *args, **kwargs):
        env = kwargs.get("env")
        if request.method == "POST" and env in ENVIRONMENTS and not tables_ready(env):
            messages.error(request, NOT_MIGRATED.format(env=env))
            return redirect("superadmin:org_detail", env=env, pk=kwargs["pk"])
        return super().dispatch(request, *args, **kwargs)

    def org(self, env, pk):
        _env_label_or_404(env)
        return get_object_or_404(Organization.objects.using(env), pk=pk)

    def back(self, env, pk):
        return redirect(reverse("superadmin:org_detail", args=[env, pk]) + "#notes")


class ClientNoteAddView(_OrgAction):
    def post(self, request, env, pk):
        org = self.org(env, pk)
        body = request.POST.get("body", "").strip()
        if not body:
            messages.error(request, "A note can't be empty.")
            return self.back(env, pk)
        ClientNote.objects.using(env).create(
            organization=org, author_email=request.user.email, body=body[:5000],
            is_pinned=request.POST.get("pin") == "1",
        )
        messages.success(request, f"Added a note on {org.name}.")
        return self.back(env, pk)


class ClientNotePinView(_OrgAction):
    def post(self, request, env, pk, note_id):
        org = self.org(env, pk)
        note = get_object_or_404(ClientNote.objects.using(env), pk=note_id, organization=org)
        note.is_pinned = not note.is_pinned
        note.save(using=env, update_fields=["is_pinned"])
        messages.success(request, "Note pinned." if note.is_pinned else "Note unpinned.")
        return self.back(env, pk)


class ClientNoteDeleteView(_OrgAction):
    def post(self, request, env, pk, note_id):
        org = self.org(env, pk)
        get_object_or_404(ClientNote.objects.using(env), pk=note_id, organization=org).delete()
        messages.success(request, f"Deleted a note on {org.name}.")
        return self.back(env, pk)


class ClientTagAddView(_OrgAction):
    def post(self, request, env, pk):
        org = self.org(env, pk)
        name = request.POST.get("name", "").strip()[:32]
        color = request.POST.get("color", "slate")
        if color not in ClientTag.Color.values:
            color = ClientTag.Color.SLATE
        if not name:
            messages.error(request, "Enter a tag name.")
            return self.back(env, pk)
        try:
            with transaction.atomic(using=env):
                ClientTag.objects.using(env).create(organization=org, name=name, color=color)
            messages.success(request, f"Tagged {org.name} “{name}”.")
        except IntegrityError:
            messages.info(request, f"{org.name} already has that tag.")
        return self.back(env, pk)


class ClientTagRemoveView(_OrgAction):
    def post(self, request, env, pk, tag_id):
        org = self.org(env, pk)
        tag = get_object_or_404(ClientTag.objects.using(env), pk=tag_id, organization=org)
        tag.delete()
        messages.success(request, f"Removed tag “{tag.name}” from {org.name}.")
        return self.back(env, pk)


# --------------------------------------------------------- log in as a client
IMPERSONATION_MAX_AGE = 60 * 60  # an hour, then the session simply expires


class ImpersonateStartView(SuperAdminRequiredMixin, View):
    """Sign in as one of a client's users to see exactly what they see.

    Only for the environment this server signs people into (sign-in always
    uses the `default` database). The session remembers the superadmin, a
    banner on every page says who is really signed in, both the start and
    the return are in the audit log, and the session expires after an hour.
    """

    def post(self, request, env, pk, user_id):
        _env_label_or_404(env)
        if env != current_env():
            messages.error(request, f"Log in as is only available for {ENVIRONMENTS[current_env()]} on this server.")
            return redirect("superadmin:org_detail", env=env, pk=pk)
        org = get_object_or_404(Organization.objects.using(env), pk=pk)
        member = get_object_or_404(User.objects.using(env), pk=user_id, organization_id=org.id)
        if member.role == User.Role.SUPER_ADMIN or not member.is_active:
            messages.error(request, "That user can't be logged in as.")
            return redirect("superadmin:org_detail", env=env, pk=pk)

        admin = request.user
        # Logged here (not by the middleware) because by the time the
        # response goes out, request.user is the client.
        record(env=env, actor_email=admin.email, action="impersonate_start",
               summary=f"Logged in as {member.email}", organization=org, target_type="User",
               target_id=member.pk, path=request.path, ip_address=client_ip(request))
        request._sa_audited = True
        # Signing in stamps last_login; put the client's own value back so a
        # support visit never counts as the client being active.
        previous_login = member.last_login
        login(request, member, backend="django.contrib.auth.backends.ModelBackend")
        User.objects.filter(pk=member.pk).update(last_login=previous_login)
        request.session["impersonator_id"] = str(admin.pk)
        request.session["impersonator_email"] = admin.email
        request.session["impersonator_return"] = reverse("superadmin:org_detail", args=[env, pk])
        request.session.set_expiry(IMPERSONATION_MAX_AGE)
        return redirect("finance:dashboard")


class ImpersonateStopView(View):
    """Back to the superadmin's own session. Not behind SuperAdminRequiredMixin:
    the person asking is, at this moment, signed in as the client."""

    def post(self, request):
        admin_id = request.session.get("impersonator_id")
        if not admin_id:
            return redirect("accounts:login")
        target = request.session.get("impersonator_return") or reverse("superadmin:overview")
        client_email = request.user.email if request.user.is_authenticated else ""
        admin = User.objects.filter(pk=admin_id, role=User.Role.SUPER_ADMIN, is_active=True).first()
        if admin is None:
            request.session.flush()
            return redirect("accounts:login")
        record(env=current_env(), actor_email=admin.email, action="impersonate_stop",
               summary=f"Returned from {client_email}", path=request.path, ip_address=client_ip(request))
        request._sa_audited = True
        login(request, admin, backend="django.contrib.auth.backends.ModelBackend")
        request.session.set_expiry(None)
        messages.success(request, f"Back to your superadmin session (you were viewing as {client_email}).")
        return redirect(target)


# -------------------------------------------------------------- global search
class GlobalSearchView(SuperAdminRequiredMixin, TemplateView):
    template_name = "superadmin/search.html"
    LIMIT = 8

    def get_context_data(self, **kwargs):
        env = kwargs["env"]
        context = super().get_context_data(**kwargs)
        context.update(_base_context(env))
        q = self.request.GET.get("q", "").strip()
        context["search_q"] = q
        groups = []
        if len(q) >= 2:
            orgs = Organization.objects.using(env).filter(
                Q(name__icontains=q) | Q(organization_code__icontains=q) | Q(contact_person__icontains=q)
                | Q(contact_email__icontains=q) | Q(contact_phone__icontains=q) | Q(tax_id__icontains=q)
            ).order_by("name")
            users = User.objects.using(env).filter(
                Q(email__icontains=q) | Q(first_name__icontains=q) | Q(last_name__icontains=q) | Q(phone__icontains=q)
            ).select_related("organization").order_by("email")
            invoices = Invoice.objects.using(env).filter(
                Q(invoice_number__icontains=q) | Q(organization__name__icontains=q)
            ).select_related("organization").order_by("-invoice_date")
            payments = Payment.objects.using(env).filter(
                Q(payment_id__icontains=q) | Q(transaction_id__icontains=q) | Q(organization__name__icontains=q)
            ).select_related("organization").order_by("-payment_date")
            subs = Subscription.objects.using(env).filter(
                Q(subscription_id__icontains=q) | Q(razorpay_subscription_id__icontains=q)
            ).select_related("organization", "plan")
            coupons = Coupon.objects.using(env).filter(Q(code__icontains=q) | Q(description__icontains=q))
            for key, label, qs in (
                ("clients", "Clients", orgs), ("users", "People", users), ("invoices", "Invoices", invoices),
                ("payments", "Payments", payments), ("subscriptions", "Subscriptions", subs),
                ("coupons", "Coupons", coupons),
            ):
                items = list(qs[: self.LIMIT + 1])
                if items:
                    groups.append({"key": key, "label": label, "items": items[: self.LIMIT],
                                   "more": len(items) > self.LIMIT})
        context["groups"] = groups
        context["result_count"] = sum(len(g["items"]) for g in groups)
        return context


# ------------------------------------------------------------------ audit log
class AuditLogView(SuperAdminRequiredMixin, TemplateView):
    template_name = "superadmin/audit_log.html"
    SORTS = {"when": "created_at", "actor": "actor_email", "action": "action", "client": "organization_name"}

    def get(self, request, *args, **kwargs):
        context = self.get_context_data(**kwargs)
        fmt = wants_export(request)
        if fmt:
            rows = ([e.created_at, e.actor_email, humanize_action(e.action), e.summary, e.organization_name,
                     e.target_type, e.target_id, e.ip_address, e.path] for e in context["all_entries"])
            return export_response(fmt, f"audit-log-{kwargs['env']}", [
                "When", "Who", "Action", "Summary", "Client", "Target", "Target ID", "IP", "Path"], rows)
        return self.render_to_response(context)

    def get_context_data(self, **kwargs):
        env = kwargs["env"]
        context = super().get_context_data(**kwargs)
        context.update(_base_context(env))
        get = self.request.GET
        context["console_tables_ready"] = ready = tables_ready(env)
        qs = AuditLogEntry.objects.using(env).all() if ready else AuditLogEntry.objects.none()
        q = get.get("q", "").strip()
        if q:
            qs = qs.filter(Q(summary__icontains=q) | Q(organization_name__icontains=q) | Q(target_id__icontains=q))
        actor = get.get("actor", "")
        if actor:
            qs = qs.filter(actor_email=actor)
        action = get.get("action", "")
        if action:
            qs = qs.filter(action=action)
        org = get.get("org", "")
        if org:
            qs = qs.filter(organization_id=org)
        date_from, date_to = get.get("from", ""), get.get("to", "")
        if date_from:
            qs = qs.filter(created_at__date__gte=date_from)
        if date_to:
            qs = qs.filter(created_at__date__lte=date_to)
        qs, context["sort"] = sorted_qs(self.request, qs, self.SORTS, "-when")
        context["all_entries"] = qs
        context["entries"] = paginate(self.request, qs, context, default_size=50)
        context["actors"] = (
            AuditLogEntry.objects.using(env).values_list("actor_email", flat=True).distinct().order_by("actor_email")
            if ready else []
        )
        used = set(AuditLogEntry.objects.using(env).values_list("action", flat=True).distinct()) if ready else set()
        context["action_choices"] = sorted(((a, humanize_action(a)) for a in used), key=lambda r: r[1])
        context.update({"q": q, "actor": actor, "action": action, "org": org, "date_from": date_from,
                        "date_to": date_to, "filtered": any([q, actor, action, org, date_from, date_to])})
        return context


# ------------------------------------------------------------------- webhooks
class WebhookListView(SuperAdminRequiredMixin, TemplateView):
    template_name = "superadmin/webhook_list.html"
    SORTS = {"received": "received_at", "type": "event_type", "status": "status"}

    def get_context_data(self, **kwargs):
        env = kwargs["env"]
        context = super().get_context_data(**kwargs)
        context.update(_base_context(env))
        get = self.request.GET
        qs = PaymentWebhookEvent.objects.using(env).select_related("payment", "payment__organization")
        q = get.get("q", "").strip()
        if q:
            qs = qs.filter(Q(event_id__icontains=q) | Q(event_type__icontains=q) | Q(error_message__icontains=q))
        status = get.get("status", "")
        if status:
            qs = qs.filter(status=status)
        gateway = get.get("gateway", "")
        if gateway:
            qs = qs.filter(gateway=gateway)
        context["counts"] = dict(
            PaymentWebhookEvent.objects.using(env).values_list("status").annotate(n=Count("pk")).values_list("status", "n")
        )
        qs, context["sort"] = sorted_qs(self.request, qs, self.SORTS, "-received")
        context["events"] = paginate(self.request, qs, context, default_size=50)
        context["status_choices"] = PaymentWebhookEvent.Status.choices
        context["gateway_choices"] = Payment.Gateway.choices
        context.update({"q": q, "status": status, "gateway": gateway, "filtered": any([q, status, gateway])})
        return context


class WebhookDetailView(SuperAdminRequiredMixin, TemplateView):
    template_name = "superadmin/webhook_detail.html"

    def get_context_data(self, **kwargs):
        import json

        env = kwargs["env"]
        context = super().get_context_data(**kwargs)
        context.update(_base_context(env))
        event = get_object_or_404(
            PaymentWebhookEvent.objects.using(env).select_related("payment", "payment__organization"), pk=kwargs["pk"]
        )
        context["event"] = event
        context["payload_json"] = json.dumps(event.payload, indent=2, sort_keys=True, default=str)
        return context


# -------------------------------------------------------------- system health
def _db_check(alias):
    out = {"alias": alias, "ok": False, "latency_ms": None, "error": "", "pending": [], "vendor": "", "name": ""}
    conn = connections[alias]
    out["name"] = conn.settings_dict.get("NAME", "")
    out["host"] = conn.settings_dict.get("HOST", "")
    try:
        t0 = time.perf_counter()
        with conn.cursor() as cur:
            cur.execute("SELECT version()")
            out["vendor"] = (cur.fetchone()[0] or "").split(" on ")[0]
        out["latency_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        executor = MigrationExecutor(conn)
        plan = executor.migration_plan(executor.loader.graph.leaf_nodes())
        out["pending"] = [f"{m.app_label}.{m.name}" for m, _backwards in plan]
        out["ok"] = True
    except Exception as exc:  # noqa: BLE001 — any failure just means "not healthy"
        out["error"] = str(exc).splitlines()[0][:300] if str(exc) else exc.__class__.__name__
    return out


class SystemHealthView(SuperAdminRequiredMixin, TemplateView):
    template_name = "superadmin/system_health.html"

    def get_context_data(self, **kwargs):
        env = kwargs["env"]
        context = super().get_context_data(**kwargs)
        context.update(_base_context(env))
        context["databases"] = [_db_check(alias) for alias in ENVIRONMENTS]
        env_db = next(d for d in context["databases"] if d["alias"] == env)
        now = timezone.now()
        drive, webhooks, last_payment, last_webhook = [], {}, None, None
        if env_db["ok"]:
            try:
                drive = list(
                    CloudBackupConnection.objects.using(env).select_related("organization")
                    .order_by("-last_synced_at")
                )
                webhooks = {
                    "failed_24h": PaymentWebhookEvent.objects.using(env).filter(
                        status=PaymentWebhookEvent.Status.FAILED, received_at__gte=now - datetime.timedelta(hours=24)).count(),
                    "total_24h": PaymentWebhookEvent.objects.using(env).filter(
                        received_at__gte=now - datetime.timedelta(hours=24)).count(),
                }
                last_payment = Payment.objects.using(env).order_by("-payment_date").first()
                last_webhook = PaymentWebhookEvent.objects.using(env).order_by("-received_at").first()
            except DatabaseError:
                logger.exception("System health: %s database read failed", env)
        context["drive_errors"] = [c for c in drive if c.last_sync_error]
        context["drive_ok"] = [c for c in drive if not c.last_sync_error]
        context["drive_stale"] = [c for c in context["drive_ok"]
                                  if c.last_synced_at and (now - c.last_synced_at).days > 7]
        context["webhooks"] = webhooks
        context["last_payment"] = last_payment
        context["last_webhook"] = last_webhook
        from apps.billing import razorpay_client
        from apps.organizations import google_drive_client

        razorpay_ok = _safe_bool(razorpay_client.is_configured)
        drive_ok = _safe_bool(google_drive_client.is_configured)
        context["integrations"] = [
            ("Email (SMTP)", bool(settings.EMAIL_HOST_USER),
             settings.EMAIL_HOST if settings.EMAIL_HOST_USER else "Console backend — emails are only printed"),
            ("Razorpay payments", razorpay_ok, "Keys set" if razorpay_ok else "Keys missing — Pay Now hidden"),
            ("Razorpay webhooks", bool(settings.RAZORPAY_WEBHOOK_SECRET), "Secret set" if settings.RAZORPAY_WEBHOOK_SECRET else "No webhook secret"),
            ("Google Drive / Sheets", drive_ok, "OAuth client set" if drive_ok else "OAuth client missing"),
            ("Alert digest email", True, ", ".join(settings.SUPERADMIN_DIGEST_EMAILS) or "All active superadmins"),
            ("Slack alerts", bool(settings.SUPERADMIN_SLACK_WEBHOOK_URL), "Webhook set" if settings.SUPERADMIN_SLACK_WEBHOOK_URL else "Not configured"),
        ]
        context["runtime"] = [
            ("Signed-in environment", ENVIRONMENTS[current_env()]),
            ("Debug mode", "On" if settings.DEBUG else "Off"),
            ("Django", django.get_version()),
            ("Python", platform.python_version()),
            ("Time zone", settings.TIME_ZONE),
            ("Server time", timezone.localtime(now).strftime("%d %b %Y, %H:%M:%S")),
        ]
        context["debug_on_prod"] = settings.DEBUG and settings.IS_PRODUCTION
        return context


def _safe_bool(fn):
    try:
        return bool(fn())
    except Exception:  # noqa: BLE001
        return False


class SendDigestView(SuperAdminRequiredMixin, View):
    def post(self, request, env):
        _env_label_or_404(env)
        from .digest import send_digest

        result = send_digest(
            env, recipients=[request.user.email] if request.POST.get("me") == "1" else None,
            base_url=request.build_absolute_uri("/"),
        )
        if result["sent_email"] or result["sent_slack"]:
            where = " and ".join(w for w, ok in (("email", result["sent_email"]), ("Slack", result["sent_slack"])) if ok)
            messages.success(request, f"Sent the {ENVIRONMENTS[env].lower()} alert digest by {where} "
                                      f"({result['item_count']} item{'s' if result['item_count'] != 1 else ''}).")
        else:
            messages.error(request, "Couldn't send the digest: " + (result["error"] or "no recipients."))
        return redirect("superadmin:system_health", env=env)


# --------------------------------------------------------------------- admins
class AdminListView(SuperAdminRequiredMixin, TemplateView):
    template_name = "superadmin/admin_list.html"

    def get_context_data(self, **kwargs):
        env = kwargs["env"]
        context = super().get_context_data(**kwargs)
        context.update(_base_context(env))
        admins = list(User.objects.using(env).filter(role=User.Role.SUPER_ADMIN).order_by("-is_active", "email"))
        activity = dict(
            AuditLogEntry.objects.using(env).values_list("actor_email").annotate(n=Count("pk")).values_list("actor_email", "n")
        ) if tables_ready(env) else {}
        for a in admins:
            a.action_count = activity.get(a.email, 0)
        context["admins"] = admins
        context["can_manage"] = env == current_env()
        return context


class AdminCreateView(SuperAdminRequiredMixin, View):
    def post(self, request, env):
        _env_label_or_404(env)
        from django.contrib.auth.password_validation import ValidationError, validate_password

        email = request.POST.get("email", "").strip().lower()
        password = request.POST.get("password", "")
        if not email or "@" not in email:
            messages.error(request, "Enter a valid email address.")
            return redirect("superadmin:admin_list", env=env)
        if User.objects.using(env).filter(email__iexact=email).exists():
            messages.error(request, "Someone with that email already exists.")
            return redirect("superadmin:admin_list", env=env)
        try:
            validate_password(password)
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
            return redirect("superadmin:admin_list", env=env)
        user = User(email=email, role=User.Role.SUPER_ADMIN, is_staff=True,
                    first_name=request.POST.get("first_name", "").strip()[:150],
                    last_name=request.POST.get("last_name", "").strip()[:150])
        user.set_password(password)
        user.save(using=env)
        messages.success(request, f"Added {email} as a superadmin in {ENVIRONMENTS[env].lower()}.")
        return redirect("superadmin:admin_list", env=env)


class AdminToggleView(SuperAdminRequiredMixin, View):
    def post(self, request, env, user_id):
        _env_label_or_404(env)
        admin = get_object_or_404(User.objects.using(env), pk=user_id, role=User.Role.SUPER_ADMIN)
        if admin.email == request.user.email:
            messages.error(request, "You can't deactivate yourself.")
            return redirect("superadmin:admin_list", env=env)
        if admin.is_active and User.objects.using(env).filter(role=User.Role.SUPER_ADMIN, is_active=True).count() <= 1:
            messages.error(request, "There must always be at least one active superadmin.")
            return redirect("superadmin:admin_list", env=env)
        admin.is_active = not admin.is_active
        admin.save(using=env, update_fields=["is_active"])
        messages.success(request, f"{'Reactivated' if admin.is_active else 'Deactivated'} {admin.email}.")
        return redirect("superadmin:admin_list", env=env)


# -------------------------------------------------------------- coupon detail
class CouponDetailView(SuperAdminRequiredMixin, TemplateView):
    template_name = "superadmin/coupon_detail.html"

    def get_context_data(self, **kwargs):
        env = kwargs["env"]
        context = super().get_context_data(**kwargs)
        context.update(_base_context(env))
        coupon = get_object_or_404(Coupon.objects.using(env), pk=kwargs["pk"])
        redemptions = CouponRedemption.objects.using(env).filter(coupon=coupon).select_related(
            "organization", "invoice").order_by("-redeemed_at")
        coupon.redemption_count = redemptions.count()
        context["coupon"] = coupon
        context["state"] = coupon_state(coupon, timezone.localdate())
        context["redemptions"] = paginate(self.request, redemptions, context)
        context["totals"] = redemptions.aggregate(discount=Sum("discount_amount"), clients=Count("organization", distinct=True))
        return context
