import calendar
import datetime
import json
import logging
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth import login
from django.utils.http import url_has_allowed_host_and_scheme
from django.db import DatabaseError, transaction
from django.db.models import Count, Max, Q, Sum
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.generic import FormView, TemplateView, View

from apps.accounts.forms import SuperadminLoginForm
from apps.accounts.mixins import SuperAdminRequiredMixin
from apps.accounts.models import User
from apps.billing import invoicing
from apps.billing import services as billing_services
from apps.billing import payments as payment_services
from apps.billing.models import Coupon, CouponRedemption, Invoice, Payment, Plan, Subscription
from apps.organizations import service_control
from apps.organizations.models import DataStorageChangeRequest, Organization, ServiceStatusChange, SupportRequest
from apps.organizations.services import OrganizationSignupError, create_organization_with_tenant_schema_and_admin

from apps.organizations.forms import ApproveStorageChangeForm
from apps.organizations.views import _handle_approved_storage_change

from .audit import tables_ready
from .listing import export_response, paginate, sorted_qs, wants_export
from .forms import (
    ApplyInvoiceCouponForm,
    ClientCreateForm,
    ClientProfileForm,
    ClientUserEditForm,
    CouponForm,
    CreateInvoiceForm,
    SupportReplyForm,
    EditInvoiceForm,
    EditKeyDatesForm,
    EditPaymentForm,
    ExtendTrialForm,
    GenerateInvoiceForm,
    GrantComplimentaryForm,
    PlanForm,
    RecordInvoicePaymentForm,
    RecordPaymentForm,
    ResetClientPasswordForm,
    ServiceActionForm,
    SubscriptionActionForm,
    SubscriptionEditForm,
)

logger = logging.getLogger(__name__)

ENVIRONMENTS = {
    "dev": "Development",
    "prod": "Production",
}

# The client list's bulk actions (see ops_views.OrgBulkActionView).
BULK_ACTIONS = [
    ("extend_trial", "Extend trial by…"),
    ("suspend", "Suspend service"),
    ("resume", "Resume service"),
    ("add_tag", "Add tag…"),
    ("remove_tag", "Remove tag…"),
    ("export", "Export selected (Excel)"),
]


def _env_label_or_404(env):
    label = ENVIRONMENTS.get(env)
    if label is None:
        raise Http404("Unknown environment")
    return label


class SuperadminLoginView(FormView):
    """The console's own sign-in screen. Only super admin credentials work;
    client accounts are rejected exactly like a wrong password."""

    template_name = "superadmin/login.html"
    form_class = SuperadminLoginForm

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and request.user.role == User.Role.SUPER_ADMIN:
            return redirect("superadmin:overview")
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        login(self.request, form.cleaned_data["user"])
        target = self.request.POST.get("next") or self.request.GET.get("next") or ""
        if not (
            target.startswith("/superadmin/")
            and url_has_allowed_host_and_scheme(target, allowed_hosts={self.request.get_host()})
        ):
            target = reverse("superadmin:overview")
        return redirect(target)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["next"] = self.request.GET.get("next", "")
        return context


class PlatformAnalyticsView(SuperAdminRequiredMixin, TemplateView):
    """One page of analytics for the platform itself: revenue, money given
    back, subscriptions and client growth, for one environment."""

    template_name = "superadmin/analytics.html"
    MONTH_CHOICES = (6, 12, 24)

    def get_context_data(self, **kwargs):
        from .analytics import platform_analytics

        env = kwargs["env"]
        context = super().get_context_data(**kwargs)
        context["env_key"] = env
        context["env_label"] = _env_label_or_404(env)
        try:
            months = int(self.request.GET.get("months", 12))
        except ValueError:
            months = 12
        if months not in self.MONTH_CHOICES:
            months = 12
        context["month_choices"] = self.MONTH_CHOICES
        context["month_start"] = timezone.localdate().replace(day=1).isoformat()
        try:
            data = platform_analytics(env, months)
            context["db_error"] = False
        except DatabaseError:
            # Keep the page's layout (all zeros) with a warning banner rather
            # than replacing it with an error message.
            logger.exception("Platform analytics: %s database unavailable", env)
            from .analytics import empty_platform_analytics

            data = empty_platform_analytics(months)
            context["db_error"] = True
        from .growth import empty_growth, growth_metrics

        try:
            growth = empty_growth(months) if context["db_error"] else growth_metrics(env, months)
        except DatabaseError:
            logger.exception("Platform analytics growth: %s database unavailable", env)
            growth = empty_growth(months)
        context["a"] = data
        context["g"] = growth
        context["charts_json"] = json.dumps({**data["charts"], "moves": growth["moves"]}, default=float)
        return context


class EnvironmentOverviewView(SuperAdminRequiredMixin, TemplateView):
    """The console's home: what needs attention right now, the headline
    numbers for production (with development alongside), revenue trend,
    latest signups and the latest console activity.

    Revenue everywhere here is the same figure Analytics uses — money
    collected (including later-refunded payments) minus refunds — so the
    two pages always agree.
    """

    template_name = "superadmin/overview.html"
    MONTHS = 12

    def get_context_data(self, **kwargs):
        from .alerts import safe_attention_items
        from .analytics import empty_platform_analytics, platform_analytics
        from .models import AuditLogEntry

        context = super().get_context_data(**kwargs)
        data, db_ok, attention, recent_orgs, activity = {}, {}, [], {}, []
        for env in ENVIRONMENTS:
            try:
                data[env] = platform_analytics(env, self.MONTHS)
                db_ok[env] = True
            except DatabaseError:
                logger.exception("Superadmin overview: %s database unavailable", env)
                data[env] = empty_platform_analytics(self.MONTHS)
                db_ok[env] = False
            items, ok = safe_attention_items(env) if db_ok[env] else ([], False)
            attention += items
            try:
                recent_orgs[env] = list(Organization.objects.using(env).order_by("-created_at")[:6])
            except DatabaseError:
                recent_orgs[env] = []
            if db_ok[env] and tables_ready(env):
                activity += [
                    {"env": env, "entry": e} for e in AuditLogEntry.objects.using(env).order_by("-created_at")[:8]
                ]

        order = {"critical": 0, "warning": 1, "info": 2}
        attention.sort(key=lambda i: (order[i["severity"]], i["env"] != "prod"))
        activity.sort(key=lambda a: a["entry"].created_at, reverse=True)

        context["a"] = data
        context["db_ok"] = db_ok
        context["attention"] = attention
        context["recent_orgs"] = recent_orgs
        context["activity"] = activity[:8]
        context["charts_json"] = json.dumps({
            env: {
                "labels": d["charts"]["labels"],
                "net": d["charts"].get("net", []),
                "new_clients": d["charts"].get("new_clients", []),
                "pay_status": d["charts"]["pay_status"],
            }
            for env, d in data.items()
        }, default=float)
        return context


class OrganizationListView(SuperAdminRequiredMixin, TemplateView):
    """Every client with plan, storage, usage and a health score; filter,
    sort, page through, export, or act on several at once."""

    template_name = "superadmin/org_list.html"
    SORTS = {"name": "name", "created": "created_at", "activity": "last_activity", "users": "user_count"}
    EXPORT_HEADERS = [
        "Client", "Code", "Contact person", "Contact email", "Phone", "Business type", "Storage", "Plan",
        "Subscription status", "Payment status", "Renewal date", "Service", "Users", "Last login",
        "Data stored (MB)", "Entries (30 days)", "Entries (total)", "Health", "Health score", "Tags", "Created",
    ]

    def get(self, request, *args, **kwargs):
        context = self.get_context_data(**kwargs)
        fmt = wants_export(request)
        if fmt:
            return export_response(fmt, f"clients-{kwargs['env']}", self.EXPORT_HEADERS, self._export_rows(context))
        return self.render_to_response(context)

    def _export_rows(self, context):
        for org in context["all_organizations"]:
            sub, u, h = org.current_subscription, org.usage, org.health
            yield [
                org.name, org.organization_code, org.contact_person, org.contact_email, org.contact_phone,
                org.get_business_type_display(), org.get_storage_mode_display(),
                sub.plan.name if sub else "", sub.get_status_display() if sub else "",
                sub.get_payment_status_display() if sub else "", sub.end_date if sub else None,
                org.get_service_status_display(), u["users"], u["last_login"],
                round(u["db_bytes"] / (1024 * 1024), 2) if org.storage_mode == Organization.StorageMode.OUR_DATABASE else "",
                u["entries_30d"],
                u["entries_total"], h["label"], h["score"], ", ".join(t.name for t in org.tag_list),
                org.created_at,
            ]

    def get_context_data(self, **kwargs):
        from .health import is_inactive, score, silent_clients, usage_for
        from .models import ClientTag

        env = kwargs["env"]
        context = super().get_context_data(**kwargs)
        context["env_key"] = env
        context["env_label"] = _env_label_or_404(env)
        get = self.request.GET
        ready = tables_ready(env)

        qs = Organization.objects.using(env).annotate(
            user_count=Count("users", distinct=True),
            last_activity=Max("users__last_login"),
        )

        q = get.get("q", "").strip()
        if q:
            match = (
                Q(name__icontains=q)
                | Q(organization_code__icontains=q)
                | Q(contact_person__icontains=q)
                | Q(contact_email__icontains=q)
                | Q(users__email__icontains=q)
            )
            if ready:
                match |= Q(admin_tags__name__iexact=q)
            qs = qs.filter(match).distinct()

        status = get.get("status", "")
        if status == "active":
            qs = qs.filter(service_status=Organization.ServiceStatus.ACTIVE)
        elif status == "suspended":
            qs = qs.filter(service_status=Organization.ServiceStatus.SUSPENDED)

        business_type = get.get("business_type", "")
        if business_type:
            qs = qs.filter(business_type=business_type)

        storage = get.get("storage", "")
        if storage in Organization.StorageMode.values:
            qs = qs.filter(storage_mode=storage)

        created_from = get.get("created_from", "")
        if created_from:
            qs = qs.filter(created_at__date__gte=created_from)

        plan_id = get.get("plan", "")
        if plan_id:
            qs = qs.filter(subscriptions__is_current=True, subscriptions__plan_id=plan_id)

        tag = get.get("tag", "").strip() if ready else ""
        if tag:
            qs = qs.filter(admin_tags__name=tag)

        qs, sort = sorted_qs(self.request, qs, self.SORTS, "-created")
        organizations = list(qs)

        # One extra query to attach each org's current subscription — cheap
        # (single IN query) and avoids fragile cross-alias annotate/subquery
        # tricks for a "dev"/"prod" alias that isn't "default".
        ids = [o.id for o in organizations]
        subs_by_org = {
            sub.organization_id: sub
            for sub in Subscription.objects.using(env).filter(organization_id__in=ids, is_current=True).select_related("plan")
        }
        tags_by_org = {}
        for t in (ClientTag.objects.using(env).filter(organization_id__in=ids) if ready else []):
            tags_by_org.setdefault(t.organization_id, []).append(t)
        usage = usage_for(env, ids)
        for org in organizations:
            org.current_subscription = subs_by_org.get(org.id)
            org.usage = usage[org.id]
            org.health = score(org, org.current_subscription, org.usage)
            org.tag_list = tags_by_org.get(org.id, [])

        health_counts = {"healthy": 0, "at_risk": 0, "critical": 0}
        for org in organizations:
            health_counts[org.health["level"]] += 1
        # Clients on Google Sheets keep their data in their own Drive, so only
        # own-database clients count towards what we store.
        context["total_db_bytes"] = sum(
            o.usage["db_bytes"] for o in organizations if o.storage_mode == Organization.StorageMode.OUR_DATABASE
        )

        health = get.get("health", "")
        if health in health_counts:
            organizations = [o for o in organizations if o.health["level"] == health]
        if get.get("inactive") == "1":
            organizations = [o for o in organizations if is_inactive(o.usage)]
        silent = silent_clients(env)
        for org in organizations:
            org.silent_days = silent.get(org.id)
        if get.get("missing_entries") == "1":
            organizations = [o for o in organizations if o.silent_days is not None]
        raw_sort = get.get("sort", "")
        if raw_sort.lstrip("-") == "health":
            sort = raw_sort
            organizations.sort(key=lambda o: o.health["score"], reverse=raw_sort.startswith("-"))

        context["all_organizations"] = organizations
        context["organizations"] = paginate(self.request, organizations, context)
        context["total_count"] = len(organizations)
        context["health_counts"] = health_counts
        context["sort"] = sort
        context["q"] = q
        context["status"] = status
        context["business_type"] = business_type
        context["storage"] = storage
        context["plan"] = plan_id
        context["tag"] = tag
        context["health"] = health
        context["inactive"] = get.get("inactive", "")
        context["missing_entries"] = get.get("missing_entries", "")
        context["created_from"] = created_from
        context["filtered"] = any([q, status, business_type, storage, plan_id, tag, health, context["inactive"], context["missing_entries"], created_from])
        context["business_type_choices"] = Organization.BusinessType.choices
        context["storage_choices"] = Organization.StorageMode.choices
        context["plan_choices"] = Plan.objects.using(env).order_by("name")
        context["tag_choices"] = (
            list(ClientTag.objects.using(env).values_list("name", flat=True).distinct().order_by("name"))
            if ready else []
        )
        context["console_tables_ready"] = ready
        context["bulk_actions"] = BULK_ACTIONS
        return context


class OrganizationDetailView(SuperAdminRequiredMixin, TemplateView):
    template_name = "superadmin/org_detail.html"

    def get_context_data(self, **kwargs):
        env = kwargs["env"]
        label = _env_label_or_404(env)
        org = get_object_or_404(Organization.objects.using(env), pk=kwargs["pk"])
        subscription = billing_services.get_current_subscription(org.id, using=env)
        historical_window_start = billing_services.historical_window_start(org, using=env)
        history = (
            Subscription.objects.using(env)
            .filter(organization_id=org.id)
            .exclude(pk=subscription.pk if subscription else None)
            .select_related("plan")
            .order_by("-created_at")
        )

        context = super().get_context_data(**kwargs)
        context["env_key"] = env
        context["env_label"] = label
        context["org"] = org
        context["subscription"] = subscription
        context["subscription_history"] = history
        context["historical_window_start"] = historical_window_start
        context["subscription_form"] = SubscriptionActionForm(
            using=env, initial={"start_date": datetime.date.today(), "billing_cycle": Subscription.BillingCycle.MONTHLY}
        )
        context["extend_trial_form"] = ExtendTrialForm()
        context["complimentary_form"] = GrantComplimentaryForm(using=env, initial={"start_date": datetime.date.today()})
        context["edit_dates_form"] = EditKeyDatesForm(initial={
            "historical_entry_cutoff_date": org.historical_entry_cutoff_date,
            "historical_entry_no_limit": org.historical_entry_no_limit,
            "trial_start_date": subscription.trial_start_date if subscription else None,
            "trial_end_date": subscription.trial_end_date if subscription else None,
            "start_date": subscription.start_date if subscription else None,
            "end_date": subscription.end_date if subscription else None,
            "cancellation_date": subscription.cancellation_date if subscription else None,
        })
        context["team"] = User.objects.using(env).filter(organization_id=org.id).order_by("email")
        context.update(self._console_extras(env, org, subscription))
        return context

    def _console_extras(self, env, org, subscription):
        """Health, usage, money summary, notes/tags and recent activity."""
        from .audit import current_env
        from .health import score, usage_for
        from .models import AuditLogEntry, ClientNote, ClientTag

        usage = usage_for(env, [org.id])[org.id]
        payments = Payment.objects.using(env).filter(organization_id=org.id)
        invoices = Invoice.objects.using(env).filter(organization_id=org.id)
        from .analytics import COLLECTED, OPEN_INVOICE

        money = {
            "paid": payments.filter(status__in=COLLECTED).aggregate(t=Sum("amount"))["t"] or Decimal("0"),
            "refunded": payments.aggregate(t=Sum("refunded_amount"))["t"] or Decimal("0"),
            "outstanding": invoices.filter(status__in=OPEN_INVOICE).aggregate(t=Sum("amount_due"))["t"] or Decimal("0"),
            "invoices": invoices.exclude(status=Invoice.Status.DRAFT).count(),
            "last_payment": payments.filter(status__in=COLLECTED).order_by("-payment_date").first(),
        }

        ready = tables_ready(env)
        activity = [
            {"when": e.created_at, "who": e.actor_email, "what": e.summary or e.action.replace("_", " "),
             "kind": "audit"}
            for e in (AuditLogEntry.objects.using(env).filter(organization_id=org.id)[:15] if ready else [])
        ]
        activity += [
            {"when": c.created_at, "who": c.performed_by_email,
             "what": f"{c.get_action_display()}" + (f" — {c.get_reason_display()}" if c.reason else ""),
             "kind": "service"}
            for c in ServiceStatusChange.objects.using(env).filter(organization_id=org.id).order_by("-created_at")[:10]
        ]
        activity.sort(key=lambda a: a["when"], reverse=True)

        return {
            "usage": usage,
            "health": score(org, subscription, usage),
            "money": money,
            "notes": ClientNote.objects.using(env).filter(organization_id=org.id) if ready else [],
            "tags": ClientTag.objects.using(env).filter(organization_id=org.id) if ready else [],
            "console_tables_ready": ready,
            "tag_colors": ClientTag.Color.choices,
            "activity": activity[:15],
            "can_impersonate": env == current_env(),
        }


class ClientUserEditView(SuperAdminRequiredMixin, View):
    """Edit a client user's name, email and username from superadmin."""

    def post(self, request, env, pk, user_id):
        _env_label_or_404(env)
        org = get_object_or_404(Organization.objects.using(env), pk=pk)
        member = get_object_or_404(User.objects.using(env), pk=user_id, organization_id=org.id)
        back = redirect("superadmin:org_detail", env=env, pk=pk)

        form = ClientUserEditForm(request.POST, instance=member, using=env)
        if not form.is_valid():
            messages.error(request, " ".join(e for errs in form.errors.values() for e in errs))
            return back

        form.save()
        messages.success(request, f"Updated {member.email}.")
        return back


class ClientPasswordResetView(SuperAdminRequiredMixin, View):
    """Directly set a client user's password from superadmin. There's no
    email service yet, so this is the only way for a client to recover
    access — the new password is shown once in the success message for the
    operator to relay to the client out-of-band."""

    def post(self, request, env, pk, user_id):
        _env_label_or_404(env)
        org = get_object_or_404(Organization.objects.using(env), pk=pk)
        member = get_object_or_404(User.objects.using(env), pk=user_id, organization_id=org.id)
        back = redirect("superadmin:org_detail", env=env, pk=pk)

        form = ResetClientPasswordForm(request.POST)
        if not form.is_valid():
            messages.error(request, " ".join(e for errs in form.errors.values() for e in errs))
            return back

        password_was_generated = not bool(form.cleaned_data.get("new_password"))
        password = form.generated_password()
        member.set_password(password)
        member.save(using=env, update_fields=["password"])

        if password_was_generated:
            messages.success(request, f"Password reset for {member.email}. New temporary password: {password}")
        else:
            messages.success(request, f"Password reset for {member.email}.")
        return back


class OrganizationCreateView(SuperAdminRequiredMixin, FormView):
    template_name = "superadmin/org_form.html"
    form_class = ClientCreateForm

    def dispatch(self, request, *args, **kwargs):
        self.env = kwargs["env"]
        self.env_label = _env_label_or_404(self.env)
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["using"] = self.env
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["env_key"] = self.env
        context["env_label"] = self.env_label
        context["mode"] = "create"
        return context

    def form_valid(self, form):
        data = form.cleaned_data
        password_was_generated = not bool(data.get("owner_password"))
        password = form.generated_password()

        org_data = {
            "name": data["name"],
            "business_type": data["business_type"],
            "industry": data["industry"],
            "size": data["size"],
            "storage_mode": data["storage_mode"],
            "contact_person": data["contact_person"],
            "contact_email": data["contact_email"],
            "contact_phone": data["contact_phone"],
            "address": data["address"],
            "city": data["city"],
            "state": data["state"],
            "country": data["country"],
            "tax_id": data["tax_id"],
            "website": data["website"],
        }
        admin_data = {
            "email": data["owner_email"],
            "password": password,
            "first_name": data["owner_first_name"],
            "last_name": data["owner_last_name"],
        }

        try:
            org, _admin = create_organization_with_tenant_schema_and_admin(
                org_data=org_data, admin_data=admin_data, using=self.env
            )
        except OrganizationSignupError as exc:
            form.add_error(None, str(exc))
            return self.form_invalid(form)

        bootstrap_plan = (
            Plan.objects.using(self.env).filter(is_active=True).order_by("monthly_price").first()
        )
        if bootstrap_plan is not None:
            billing_services.start_trial(org, bootstrap_plan, using=self.env)

        if password_was_generated:
            messages.success(
                self.request,
                f"Created {org.name}. Owner login: {admin_data['email']} / temporary password: {password}",
            )
        else:
            messages.success(self.request, f"Created {org.name}.")
        return redirect("superadmin:org_detail", env=self.env, pk=org.pk)

    def form_invalid(self, form):
        return self.render_to_response(self.get_context_data(form=form))


class OrganizationEditView(SuperAdminRequiredMixin, View):
    template_name = "superadmin/org_form.html"

    def _get_org(self, env, pk):
        return get_object_or_404(Organization.objects.using(env), pk=pk)

    def get(self, request, env, pk):
        _env_label_or_404(env)
        org = self._get_org(env, pk)
        form = ClientProfileForm(instance=org)
        return self._render(request, env, org, form)

    def post(self, request, env, pk):
        _env_label_or_404(env)
        org = self._get_org(env, pk)
        form = ClientProfileForm(request.POST, instance=org)
        if form.is_valid():
            instance = form.save(commit=False)
            instance.save(using=env)
            messages.success(request, f"Updated {instance.name}.")
            return redirect("superadmin:org_detail", env=env, pk=pk)
        return self._render(request, env, org, form)

    def _render(self, request, env, org, form):
        return render(
            request,
            self.template_name,
            {"form": form, "env_key": env, "env_label": ENVIRONMENTS[env], "mode": "edit", "org": org},
        )


class OrganizationDeleteView(SuperAdminRequiredMixin, View):
    """Delete an organization entirely from the database."""

    def post(self, request, env, pk):
        _env_label_or_404(env)
        org = get_object_or_404(Organization.objects.using(env), pk=pk)
        org_name = org.name

        try:
            with transaction.atomic(using=env):
                org.delete(using=env)
            messages.success(request, f"Deleted organization '{org_name}' entirely.")
        except Exception as e:
            logger.exception("Failed to delete organization %s", pk)
            messages.error(request, f"Failed to delete organization: {str(e)}")

        return redirect("superadmin:org_list", env=env)


class SubscriptionCreateView(SuperAdminRequiredMixin, View):
    """Change plan / renew / manually correct billing — creates a NEW
    current subscription, superseding (not deleting) the previous one."""

    def post(self, request, env, pk):
        _env_label_or_404(env)
        org = get_object_or_404(Organization.objects.using(env), pk=pk)
        form = SubscriptionActionForm(request.POST, using=env)
        if form.is_valid():
            data = form.cleaned_data
            billing_services.create_subscription(
                org,
                data["plan"],
                using=env,
                billing_cycle=data["billing_cycle"],
                start_date=data["start_date"],
                price=data.get("price"),
                discount=data.get("discount") or 0,
                tax=data.get("tax") or 0,
                status=data["status"],
                payment_status=data["payment_status"],
                auto_renewal=data.get("auto_renewal", True),
                notes=data.get("notes", ""),
            )
            messages.success(request, f"Updated {org.name}'s subscription.")
        else:
            messages.error(request, "Couldn't update the subscription — please check the form.")
        return redirect("superadmin:org_detail", env=env, pk=pk)


class ExtendTrialView(SuperAdminRequiredMixin, View):
    def post(self, request, env, pk):
        _env_label_or_404(env)
        org = get_object_or_404(Organization.objects.using(env), pk=pk)
        subscription = billing_services.get_current_subscription(org.id, using=env)
        if subscription is None:
            messages.error(request, f"{org.name} has no current subscription to extend.")
            return redirect("superadmin:org_detail", env=env, pk=pk)

        form = ExtendTrialForm(request.POST)
        if form.is_valid():
            billing_services.extend_trial(
                subscription, new_trial_end_date=form.cleaned_data["new_trial_end_date"], using=env
            )
            messages.success(request, f"Extended {org.name}'s trial.")
        else:
            messages.error(request, "Couldn't extend the trial — please check the date.")
        return redirect("superadmin:org_detail", env=env, pk=pk)


class EditKeyDatesView(SuperAdminRequiredMixin, View):
    """Directly correct the org's backdating cutoff and the current
    subscription's trial/billing dates in place — for fixing a wrong date,
    not a normal renew/extend/plan-change (those go through
    SubscriptionCreateView / ExtendTrialView, which preserve history)."""

    def post(self, request, env, pk):
        _env_label_or_404(env)
        org = get_object_or_404(Organization.objects.using(env), pk=pk)
        back = redirect("superadmin:org_detail", env=env, pk=pk)

        form = EditKeyDatesForm(request.POST)
        if not form.is_valid():
            messages.error(request, "Couldn't update the dates — please check the form.")
            return back

        data = form.cleaned_data
        org.historical_entry_cutoff_date = data["historical_entry_cutoff_date"]
        org.historical_entry_no_limit = data["historical_entry_no_limit"]
        org.save(using=env, update_fields=["historical_entry_cutoff_date", "historical_entry_no_limit"])

        subscription = billing_services.get_current_subscription(org.id, using=env)
        if subscription is not None:
            subscription.trial_start_date = data["trial_start_date"]
            subscription.trial_end_date = data["trial_end_date"]
            subscription.start_date = data["start_date"]
            subscription.end_date = data["end_date"]
            subscription.cancellation_date = data["cancellation_date"]
            subscription.save(
                using=env,
                update_fields=[
                    "trial_start_date", "trial_end_date", "start_date", "end_date",
                    "cancellation_date", "updated_at",
                ],
            )

        messages.success(request, f"Updated {org.name}'s dates.")
        return back


class GrantComplimentaryView(SuperAdminRequiredMixin, View):
    def post(self, request, env, pk):
        _env_label_or_404(env)
        org = get_object_or_404(Organization.objects.using(env), pk=pk)
        form = GrantComplimentaryForm(request.POST, using=env)
        if form.is_valid():
            data = form.cleaned_data
            start_date = data["start_date"]
            if data["duration"] == "1_month":
                end_date = start_date + datetime.timedelta(days=30)
            elif data["duration"] == "1_year":
                end_date = start_date + datetime.timedelta(days=365)
            else:
                end_date = data["end_date"]

            billing_services.grant_complimentary(
                org,
                using=env,
                start_date=start_date,
                end_date=end_date,
                plan=data.get("plan"),
                notes=data.get("notes", ""),
            )
            messages.success(request, f"Granted {org.name} complimentary access through {end_date:%d %b %Y}.")
        else:
            messages.error(request, "Couldn't grant complimentary access — please check the form.")
        return redirect("superadmin:org_detail", env=env, pk=pk)


class ServiceControlView(SuperAdminRequiredMixin, TemplateView):
    """The dedicated service-control page: current status, the info a
    superadmin needs before resuming (subscription status, outstanding
    amount, last payment, suspension reason), and the audit history."""

    template_name = "superadmin/service_control.html"

    def get_context_data(self, **kwargs):
        env = kwargs["env"]
        context = super().get_context_data(**kwargs)
        context["env_key"] = env
        context["env_label"] = _env_label_or_404(env)
        org = get_object_or_404(Organization.objects.using(env), pk=kwargs["pk"])
        context["org"] = org

        subscription = billing_services.get_current_subscription(org.id, using=env)
        outstanding = (
            Invoice.objects.using(env)
            .filter(organization_id=org.id)
            .exclude(status__in=[Invoice.Status.PAID, Invoice.Status.CANCELLED, Invoice.Status.DRAFT])
            .aggregate(total=Sum("amount_due"))["total"]
            or Decimal("0")
        )
        last_payment = (
            Payment.objects.using(env)
            .filter(organization_id=org.id, status=Payment.Status.SUCCESS)
            .order_by("-payment_date")
            .first()
        )
        history = (
            ServiceStatusChange.objects.using(env).filter(organization_id=org.id).order_by("-created_at")
        )
        last_suspension = history.filter(
            action__in=[ServiceStatusChange.Action.STOP, ServiceStatusChange.Action.SUSPEND]
        ).first()

        context.update(
            {
                "subscription": subscription,
                "outstanding_amount": outstanding,
                "last_payment": last_payment,
                "history": history[:25],
                "last_suspension": last_suspension,
                "action_form": ServiceActionForm(),
            }
        )
        return context


class _BaseServiceActionView(SuperAdminRequiredMixin, View):
    reason_required = False
    control_fn = None  # set by subclass
    success_verb = ""

    def post(self, request, env, pk):
        _env_label_or_404(env)
        org = get_object_or_404(Organization.objects.using(env), pk=pk)
        form = ServiceActionForm(request.POST, reason_required=self.reason_required)
        if form.is_valid():
            data = form.cleaned_data
            self.control_fn(
                org, using=env, admin_email=request.user.email,
                reason=data.get("reason", ""), notes=data.get("notes", ""),
            )
            messages.success(request, f"{org.name}'s service has been {self.success_verb}.")
        else:
            for error in form.errors.get("__all__", []) + form.errors.get("confirm", []) + form.errors.get("reason", []):
                messages.error(request, error)
            if not form.errors:
                messages.error(request, "Couldn't complete this action — please check the form.")
        return redirect("superadmin:service_control", env=env, pk=pk)


class StartServiceView(_BaseServiceActionView):
    success_verb = "started"

    def control_fn(self, org, *, using, admin_email, reason, notes):
        return service_control.start_service(org, using=using, admin_email=admin_email, notes=notes)


class StopServiceView(_BaseServiceActionView):
    reason_required = True
    success_verb = "stopped"

    def control_fn(self, org, *, using, admin_email, reason, notes):
        return service_control.stop_service(org, using=using, admin_email=admin_email, reason=reason, notes=notes)


class SuspendServiceView(_BaseServiceActionView):
    reason_required = True
    success_verb = "suspended"

    def control_fn(self, org, *, using, admin_email, reason, notes):
        return service_control.suspend_service(org, using=using, admin_email=admin_email, reason=reason, notes=notes)


class ResumeServiceView(_BaseServiceActionView):
    success_verb = "resumed"

    def control_fn(self, org, *, using, admin_email, reason, notes):
        return service_control.resume_service(org, using=using, admin_email=admin_email, notes=notes)


class PlanListView(SuperAdminRequiredMixin, TemplateView):
    template_name = "superadmin/plan_list.html"

    def get_context_data(self, **kwargs):
        env = kwargs["env"]
        context = super().get_context_data(**kwargs)
        context["env_key"] = env
        context["env_label"] = _env_label_or_404(env)
        from .analytics import PAYING, monthly_equivalent

        plans = list(Plan.objects.using(env).order_by("monthly_price", "name"))
        stats = {p.pk: {"subs": 0, "paying": 0, "trials": 0, "mrr": Decimal("0")} for p in plans}
        for sub in Subscription.objects.using(env).filter(is_current=True):
            row = stats.get(sub.plan_id)
            if row is None:
                continue
            row["subs"] += 1
            if sub.status == Subscription.Status.TRIAL:
                row["trials"] += 1
            if sub.status in PAYING and not sub.is_complimentary:
                row["paying"] += 1
                row["mrr"] += monthly_equivalent(sub)
        total_mrr = sum((r["mrr"] for r in stats.values()), Decimal("0"))
        for p in plans:
            p.stats = stats[p.pk]
            p.stats["share"] = float(p.stats["mrr"] / total_mrr * 100) if total_mrr else 0
        context["plans"] = plans
        context["total_mrr"] = total_mrr
        return context


class PlanCreateView(SuperAdminRequiredMixin, View):
    template_name = "superadmin/plan_form.html"

    def get(self, request, env):
        _env_label_or_404(env)
        form = PlanForm()
        return render(request, self.template_name, {"form": form, "env_key": env, "env_label": ENVIRONMENTS[env], "mode": "create"})

    def post(self, request, env):
        _env_label_or_404(env)
        form = PlanForm(request.POST)
        if form.is_valid():
            plan = form.save(commit=False)
            plan.save(using=env)
            messages.success(request, f"Created plan {plan.name}.")
            return redirect("superadmin:plan_list", env=env)
        return render(request, self.template_name, {"form": form, "env_key": env, "env_label": ENVIRONMENTS[env], "mode": "create"})


class PlanEditView(SuperAdminRequiredMixin, View):
    template_name = "superadmin/plan_form.html"

    def get(self, request, env, pk):
        _env_label_or_404(env)
        plan = get_object_or_404(Plan.objects.using(env), pk=pk)
        form = PlanForm(instance=plan)
        return render(request, self.template_name, {"form": form, "env_key": env, "env_label": ENVIRONMENTS[env], "mode": "edit", "plan": plan})

    def post(self, request, env, pk):
        _env_label_or_404(env)
        plan = get_object_or_404(Plan.objects.using(env), pk=pk)
        form = PlanForm(request.POST, instance=plan)
        if form.is_valid():
            instance = form.save(commit=False)
            instance.save(using=env)
            messages.success(request, f"Updated plan {instance.name}.")
            return redirect("superadmin:plan_list", env=env)
        return render(request, self.template_name, {"form": form, "env_key": env, "env_label": ENVIRONMENTS[env], "mode": "edit", "plan": plan})


class PlanToggleActiveView(SuperAdminRequiredMixin, View):
    def post(self, request, env, pk):
        _env_label_or_404(env)
        plan = get_object_or_404(Plan.objects.using(env), pk=pk)
        plan.is_active = not plan.is_active
        plan.save(using=env, update_fields=["is_active", "updated_at"])
        state = "activated" if plan.is_active else "deactivated"
        messages.success(request, f"Plan {plan.name} {state}.")
        return redirect("superadmin:plan_list", env=env)


class CancelAutopayView(SuperAdminRequiredMixin, View):
    """Superadmin's equivalent of the org owner's "Turn off autopay" on
    their own Billing page — same billing_services.cancel_autopay() call,
    just reachable from the ops console for support cases."""

    def post(self, request, env, pk):
        _env_label_or_404(env)
        org = get_object_or_404(Organization.objects.using(env), pk=pk)
        billing_services.cancel_autopay(org, using=env)
        messages.success(request, f"Autopay turned off for {org.name}.")
        return redirect("superadmin:service_control", env=env, pk=pk)


class SubscriptionListView(SuperAdminRequiredMixin, TemplateView):
    """Every org's current subscription in one place — plan, price/discount,
    final amount, and autopay status/amount — instead of having to open each
    org's own detail page to see or correct any of it."""

    template_name = "superadmin/subscription_list.html"
    SORTS = {
        "client": "organization__name", "plan": "plan__name", "status": "status", "amount": "final_amount",
        "renewal": "end_date", "trial_end": "trial_end_date", "created": "created_at",
    }
    EXPORT_HEADERS = [
        "Subscription ID", "Client", "Code", "Plan", "Cycle", "Status", "Payment status", "Price", "Discount",
        "Tax", "Final amount", "Start", "End / renewal", "Trial start", "Trial end", "Cancelled",
        "Complimentary", "Autopay", "Autopay amount",
    ]

    def get(self, request, *args, **kwargs):
        context = self.get_context_data(**kwargs)
        fmt = wants_export(request)
        if fmt:
            rows = (
                [s.subscription_id, s.organization.name, s.organization.organization_code, s.plan.name,
                 s.get_billing_cycle_display(), s.get_status_display(), s.get_payment_status_display(), s.price,
                 s.discount, s.tax, s.final_amount, s.start_date, s.end_date, s.trial_start_date, s.trial_end_date,
                 s.cancellation_date, s.is_complimentary, s.autopay_enabled, s.autopay_amount]
                for s in context["all_subscriptions"]
            )
            return export_response(fmt, f"subscriptions-{kwargs['env']}", self.EXPORT_HEADERS, rows)
        return self.render_to_response(context)

    def get_context_data(self, **kwargs):
        env = kwargs["env"]
        context = super().get_context_data(**kwargs)
        context["env_key"] = env
        context["env_label"] = _env_label_or_404(env)

        qs = Subscription.objects.using(env).filter(is_current=True).select_related("organization", "plan")

        q = self.request.GET.get("q", "").strip()
        if q:
            qs = qs.filter(
                Q(organization__name__icontains=q)
                | Q(organization__organization_code__icontains=q)
                | Q(subscription_id__icontains=q)
            )

        status = self.request.GET.get("status", "")
        if status:
            qs = qs.filter(status=status)

        cancelled_from = self.request.GET.get("cancelled_from", "")
        if cancelled_from:
            qs = qs.filter(cancellation_date__gte=cancelled_from)

        if self.request.GET.get("paying") == "1":
            from .analytics import PAYING

            qs = qs.filter(status__in=PAYING, is_complimentary=False)

        autopay = self.request.GET.get("autopay", "")
        if autopay == "on":
            qs = qs.filter(autopay_enabled=True)
        elif autopay == "off":
            qs = qs.filter(autopay_enabled=False)

        if self.request.GET.get("trial_ending") == "1":
            today = timezone.localdate()
            qs = qs.filter(trial_end_date__gte=today, trial_end_date__lte=today + datetime.timedelta(days=7))

        plan_id = self.request.GET.get("plan", "")
        if plan_id:
            qs = qs.filter(plan_id=plan_id)

        qs, context["sort"] = sorted_qs(self.request, qs, self.SORTS, "client")
        context["all_subscriptions"] = qs
        context["subscriptions"] = paginate(self.request, qs, context)
        context["summary"] = qs.aggregate(total=Sum("final_amount"), n=Count("pk"))
        context["plan"] = plan_id
        context["plan_choices"] = Plan.objects.using(env).order_by("name")
        context["trial_ending"] = self.request.GET.get("trial_ending", "")
        context["cancelled_from"] = cancelled_from
        context["paying"] = self.request.GET.get("paying", "")
        context["q"] = q
        context["status"] = status
        context["autopay"] = autopay
        context["status_choices"] = Subscription.Status.choices
        return context


class SubscriptionEditView(SuperAdminRequiredMixin, View):
    """Direct in-place correction of one subscription's plan/pricing/status
    fields, including the autopay_amount override services.enable_autopay()
    bills on Razorpay — see SubscriptionEditForm. Editing autopay_amount
    after autopay is already on only takes effect the next time autopay is
    (re-)enabled; it doesn't modify an already-authorized Razorpay mandate."""

    template_name = "superadmin/subscription_form.html"

    def _get(self, env, pk):
        return get_object_or_404(
            Subscription.objects.using(env).select_related("organization", "plan"), pk=pk
        )

    def _plan_prices_json(self, env):
        """id -> {monthly, yearly, monthly_discount, yearly_discount} for
        every plan selectable in the form, so the page's own JS can fill
        Price and Discount from the chosen plan/cycle without a round-trip
        — see subscription_form.html. The discounts are the plan's own
        (list price minus effective price), in rupees."""
        return json.dumps({
            str(p.pk): {
                "monthly": str(p.monthly_price),
                "yearly": str(p.yearly_price),
                "monthly_discount": str(p.monthly_price - p.effective_monthly_price),
                "yearly_discount": str(p.yearly_price - p.effective_yearly_price),
            }
            for p in Plan.objects.using(env).all()
        })

    def get(self, request, env, pk):
        _env_label_or_404(env)
        sub = self._get(env, pk)
        form = SubscriptionEditForm(instance=sub, using=env)
        return render(request, self.template_name, {
            "form": form, "subscription": sub, "env_key": env, "env_label": ENVIRONMENTS[env],
            "plan_prices_json": self._plan_prices_json(env),
        })

    def post(self, request, env, pk):
        _env_label_or_404(env)
        sub = self._get(env, pk)
        form = SubscriptionEditForm(request.POST, instance=sub, using=env)
        if not form.is_valid():
            messages.error(request, "Couldn't save this subscription — please check the form.")
            return render(request, self.template_name, {
                "form": form, "subscription": sub, "env_key": env, "env_label": ENVIRONMENTS[env],
                "plan_prices_json": self._plan_prices_json(env),
            })

        form.save(commit=False).save(using=env)
        messages.success(request, f"Updated {sub.organization.name}'s subscription.")
        return redirect("superadmin:subscription_list", env=env)


class CouponListView(SuperAdminRequiredMixin, TemplateView):
    template_name = "superadmin/coupon_list.html"

    def get_context_data(self, **kwargs):
        env = kwargs["env"]
        context = super().get_context_data(**kwargs)
        context["env_key"] = env
        context["env_label"] = _env_label_or_404(env)
        coupons = list(Coupon.objects.using(env).order_by("-created_at"))
        redemption_counts = {
            row["coupon_id"]: row["n"]
            for row in CouponRedemption.objects.using(env).values("coupon_id").annotate(n=Count("id"))
        }
        discount_totals = {
            row["coupon_id"]: row["t"]
            for row in CouponRedemption.objects.using(env).values("coupon_id").annotate(t=Sum("discount_amount"))
        }
        today = timezone.localdate()
        for coupon in coupons:
            coupon.redemption_count = redemption_counts.get(coupon.id, 0)
            coupon.discount_total = discount_totals.get(coupon.id) or Decimal("0")
            coupon.state = coupon_state(coupon, today)
        context["coupons"] = coupons
        return context


def coupon_state(coupon, today):
    """(label, tone) for a coupon's current usability."""
    if not coupon.is_active:
        return ("Inactive", "neutral")
    if coupon.valid_until and coupon.valid_until < today:
        return ("Expired", "bad")
    if coupon.max_redemptions and getattr(coupon, "redemption_count", 0) >= coupon.max_redemptions:
        return ("Used up", "warn")
    if coupon.valid_from and coupon.valid_from > today:
        return ("Scheduled", "info")
    if coupon.valid_until and (coupon.valid_until - today).days <= 7:
        return ("Expiring soon", "warn")
    return ("Live", "ok")


class CouponCreateView(SuperAdminRequiredMixin, View):
    template_name = "superadmin/coupon_form.html"

    def get(self, request, env):
        _env_label_or_404(env)
        form = CouponForm()
        return render(request, self.template_name, {"form": form, "env_key": env, "env_label": ENVIRONMENTS[env], "mode": "create"})

    def post(self, request, env):
        _env_label_or_404(env)
        form = CouponForm(request.POST)
        if form.is_valid():
            coupon = form.save(commit=False)
            coupon.save(using=env)
            messages.success(request, f"Created coupon {coupon.code}.")
            return redirect("superadmin:coupon_list", env=env)
        return render(request, self.template_name, {"form": form, "env_key": env, "env_label": ENVIRONMENTS[env], "mode": "create"})


class CouponEditView(SuperAdminRequiredMixin, View):
    template_name = "superadmin/coupon_form.html"

    def get(self, request, env, pk):
        _env_label_or_404(env)
        coupon = get_object_or_404(Coupon.objects.using(env), pk=pk)
        form = CouponForm(instance=coupon)
        return render(request, self.template_name, {"form": form, "env_key": env, "env_label": ENVIRONMENTS[env], "mode": "edit", "coupon": coupon})

    def post(self, request, env, pk):
        _env_label_or_404(env)
        coupon = get_object_or_404(Coupon.objects.using(env), pk=pk)
        form = CouponForm(request.POST, instance=coupon)
        if form.is_valid():
            instance = form.save(commit=False)
            instance.save(using=env)
            messages.success(request, f"Updated coupon {instance.code}.")
            return redirect("superadmin:coupon_list", env=env)
        return render(request, self.template_name, {"form": form, "env_key": env, "env_label": ENVIRONMENTS[env], "mode": "edit", "coupon": coupon})


class CouponToggleActiveView(SuperAdminRequiredMixin, View):
    def post(self, request, env, pk):
        _env_label_or_404(env)
        coupon = get_object_or_404(Coupon.objects.using(env), pk=pk)
        coupon.is_active = not coupon.is_active
        coupon.save(using=env, update_fields=["is_active", "updated_at"])
        state = "activated" if coupon.is_active else "deactivated"
        messages.success(request, f"Coupon {coupon.code} {state}.")
        return redirect("superadmin:coupon_list", env=env)


class PaymentListView(SuperAdminRequiredMixin, TemplateView):
    template_name = "superadmin/payment_list.html"
    SORTS = {"date": "payment_date", "amount": "amount", "client": "organization__name", "status": "status",
             "id": "payment_id"}
    EXPORT_HEADERS = [
        "Payment ID", "Date", "Client", "Code", "Invoice", "Amount", "Currency", "Method", "Gateway",
        "Transaction ID", "Status", "Refunded", "Refund reason", "Failure reason",
    ]

    def get(self, request, *args, **kwargs):
        context = self.get_context_data(**kwargs)
        fmt = wants_export(request)
        if fmt:
            rows = (
                [p.payment_id, p.payment_date, p.organization.name, p.organization.organization_code,
                 p.invoice.invoice_number if p.invoice else "", p.amount, p.currency, p.get_payment_method_display(),
                 p.get_gateway_display(), p.transaction_id, p.get_status_display(), p.refunded_amount,
                 p.refund_reason, p.failure_reason]
                for p in context["all_payments"]
            )
            return export_response(fmt, f"payments-{kwargs['env']}", self.EXPORT_HEADERS, rows)
        return self.render_to_response(context)

    def get_context_data(self, **kwargs):
        env = kwargs["env"]
        context = super().get_context_data(**kwargs)
        context["env_key"] = env
        context["env_label"] = _env_label_or_404(env)

        qs = Payment.objects.using(env).select_related("organization", "invoice")

        q = self.request.GET.get("q", "").strip()
        if q:
            qs = qs.filter(
                Q(payment_id__icontains=q)
                | Q(transaction_id__icontains=q)
                | Q(organization__name__icontains=q)
                | Q(organization__organization_code__icontains=q)
            )

        status = self.request.GET.get("status", "")
        if status:
            qs = qs.filter(status=status)

        gateway = self.request.GET.get("gateway", "")
        if gateway:
            qs = qs.filter(gateway=gateway)

        method = self.request.GET.get("method", "")
        if method:
            qs = qs.filter(payment_method=method)

        if self.request.GET.get("refunded") == "1":
            qs = qs.filter(refunded_amount__gt=0)

        date_from = self.request.GET.get("from", "")
        if date_from:
            qs = qs.filter(payment_date__date__gte=date_from)
        date_to = self.request.GET.get("to", "")
        if date_to:
            qs = qs.filter(payment_date__date__lte=date_to)

        qs, context["sort"] = sorted_qs(self.request, qs, self.SORTS, "-date")
        context["all_payments"] = qs
        context["payments"] = paginate(self.request, qs, context)
        context["summary"] = qs.aggregate(
            total=Sum("amount"), refunded=Sum("refunded_amount"), n=Count("pk"),
            failed=Count("pk", filter=Q(status=Payment.Status.FAILED)),
        )
        context["refunded"] = self.request.GET.get("refunded", "")
        context["q"] = q
        context["status"] = status
        context["gateway"] = gateway
        context["method"] = method
        context["date_from"] = date_from
        context["date_to"] = date_to
        context["status_choices"] = Payment.Status.choices
        context["gateway_choices"] = Payment.Gateway.choices
        context["method_choices"] = Payment.Method.choices
        return context


class InvoiceListView(SuperAdminRequiredMixin, TemplateView):
    template_name = "superadmin/invoice_list.html"
    SORTS = {"number": "invoice_number", "date": "invoice_date", "due": "due_date", "client": "organization__name",
             "total": "total", "amount_due": "amount_due", "status": "status"}
    EXPORT_HEADERS = [
        "Invoice", "Client", "Code", "Invoice date", "Due date", "Service from", "Service to", "Currency",
        "Subtotal", "Discount", "Tax", "Total", "Paid", "Due", "Status",
    ]

    def get(self, request, *args, **kwargs):
        context = self.get_context_data(**kwargs)
        fmt = wants_export(request)
        if fmt:
            rows = (
                [i.invoice_number, i.organization.name, i.organization.organization_code, i.invoice_date,
                 i.due_date, i.service_billing_start_date, i.service_billing_end_date, i.currency, i.subtotal,
                 i.discount, i.tax, i.total, i.amount_paid, i.amount_due, i.get_status_display()]
                for i in context["all_invoices"]
            )
            return export_response(fmt, f"invoices-{kwargs['env']}", self.EXPORT_HEADERS, rows)
        return self.render_to_response(context)

    def get_context_data(self, **kwargs):
        env = kwargs["env"]
        context = super().get_context_data(**kwargs)
        context["env_key"] = env
        context["env_label"] = _env_label_or_404(env)

        qs = Invoice.objects.using(env).select_related("organization")

        q = self.request.GET.get("q", "").strip()
        if q:
            qs = qs.filter(
                Q(invoice_number__icontains=q)
                | Q(organization__name__icontains=q)
                | Q(organization__organization_code__icontains=q)
            )

        status = self.request.GET.get("status", "")
        if status:
            qs = qs.filter(status=status)

        from .analytics import BILLED, OPEN_INVOICE

        if self.request.GET.get("open") == "1" or self.request.GET.get("overdue") == "1":
            qs = qs.filter(status__in=OPEN_INVOICE)
        if self.request.GET.get("overdue") == "1":
            qs = qs.filter(due_date__lt=timezone.localdate())
        if self.request.GET.get("coupon") == "1":
            qs = qs.filter(status__in=BILLED, coupon_redemption__isnull=False)

        date_from = self.request.GET.get("from", "")
        if date_from:
            qs = qs.filter(invoice_date__gte=date_from)
        date_to = self.request.GET.get("to", "")
        if date_to:
            qs = qs.filter(invoice_date__lte=date_to)

        qs, context["sort"] = sorted_qs(self.request, qs, self.SORTS, "-date")
        context["all_invoices"] = qs
        context["invoices"] = paginate(self.request, qs, context)
        context["summary"] = qs.aggregate(total=Sum("total"), due=Sum("amount_due"), n=Count("pk"))
        context["today"] = timezone.localdate()
        context["open"] = self.request.GET.get("open", "")
        context["overdue"] = self.request.GET.get("overdue", "")
        context["q"] = q
        context["status"] = status
        context["date_from"] = date_from
        context["date_to"] = date_to
        context["status_choices"] = Invoice.Status.choices
        return context


class InvoiceDetailView(SuperAdminRequiredMixin, TemplateView):
    template_name = "superadmin/invoice_detail.html"

    def get_context_data(self, **kwargs):
        env = kwargs["env"]
        context = super().get_context_data(**kwargs)
        context["env_key"] = env
        context["env_label"] = _env_label_or_404(env)
        invoice = get_object_or_404(
            Invoice.objects.using(env).select_related("organization", "subscription", "subscription__plan"),
            pk=kwargs["pk"],
        )
        context["invoice"] = invoice
        context["payments"] = Payment.objects.using(env).filter(invoice_id=invoice.id).order_by("-payment_date")
        context["redemption"] = (
            CouponRedemption.objects.using(env).select_related("coupon").filter(invoice_id=invoice.id).first()
        )
        context["can_apply_coupon"] = (
            context["redemption"] is None
            and not invoice.amount_paid
            and invoice.status not in (Invoice.Status.PAID, Invoice.Status.CANCELLED)
        )
        context["coupon_form"] = ApplyInvoiceCouponForm(using=env)
        context["can_record_payment"] = (
            invoice.amount_due > 0
            and invoice.status in (Invoice.Status.ISSUED, Invoice.Status.PARTIALLY_PAID, Invoice.Status.OVERDUE)
        )
        context["payment_form"] = RecordInvoicePaymentForm(
            amount_due=invoice.amount_due,
            initial={"amount": invoice.amount_due, "payment_date": timezone.localdate()},
        )
        context["edit_invoice_form"] = EditInvoiceForm(instance=invoice, using=env)
        return context


class InvoiceEditView(SuperAdminRequiredMixin, View):
    """Direct correction of an already-issued/already-paid invoice's own
    fields (dates, amounts, status) — for fixing a mistake, not a normal
    billing action. amount_paid/total/amount_due stay derived; see
    EditInvoiceForm."""

    def post(self, request, env, pk):
        _env_label_or_404(env)
        invoice = get_object_or_404(Invoice.objects.using(env), pk=pk)
        form = EditInvoiceForm(request.POST, instance=invoice, using=env)
        if form.is_valid():
            instance = form.save(commit=False)
            instance.save(using=env)
            messages.success(request, f"Updated invoice {instance.invoice_number}.")
        else:
            messages.error(request, "Couldn't update the invoice — please check the form.")
        return redirect("superadmin:invoice_detail", env=env, pk=pk)


class InvoiceDeleteView(SuperAdminRequiredMixin, View):
    """Delete an invoice from the database."""

    def post(self, request, env, pk):
        _env_label_or_404(env)
        invoice = get_object_or_404(Invoice.objects.using(env), pk=pk)
        invoice_number = invoice.invoice_number

        try:
            with transaction.atomic(using=env):
                invoice.delete(using=env)
            messages.success(request, f"Deleted invoice {invoice_number}.")
        except Exception as e:
            logger.exception("Failed to delete invoice %s", pk)
            messages.error(request, f"Failed to delete invoice: {str(e)}")

        return redirect("superadmin:invoice_list", env=env)


class PaymentEditView(SuperAdminRequiredMixin, View):
    """Direct correction of an already-recorded payment — for fixing a
    typo'd amount, wrong date/method, or wrong status after the fact.
    Reachable from both the payments list and an invoice's own page;
    `next` (query string on GET, hidden field on POST) sends the operator
    back to wherever they came from."""

    template_name = "superadmin/payment_edit.html"

    def _get_payment(self, env, pk):
        return get_object_or_404(
            Payment.objects.using(env).select_related("organization", "invoice"), pk=pk
        )

    def _safe_next(self, env, next_url):
        return next_url if next_url and next_url.startswith(f"/superadmin/{env}/") else None

    def get(self, request, env, pk):
        _env_label_or_404(env)
        payment = self._get_payment(env, pk)
        form = EditPaymentForm(instance=payment)
        return render(request, self.template_name, {
            "form": form, "payment": payment, "env_key": env, "env_label": ENVIRONMENTS[env],
            "next": self._safe_next(env, request.GET.get("next", "")) or "",
        })

    def post(self, request, env, pk):
        _env_label_or_404(env)
        payment = self._get_payment(env, pk)
        form = EditPaymentForm(request.POST, instance=payment)
        next_url = self._safe_next(env, request.POST.get("next", ""))
        if not form.is_valid():
            messages.error(request, "Couldn't update the payment — please check the form.")
            return render(request, self.template_name, {
                "form": form, "payment": payment, "env_key": env, "env_label": ENVIRONMENTS[env],
                "next": next_url or "",
            })

        instance = form.save(commit=False)
        instance.save(using=env)
        payment_services.edit_payment(instance, using=env)
        messages.success(request, f"Updated payment {instance.payment_id}.")
        return redirect(next_url or reverse("superadmin:payment_list", args=[env]))


def _add_months(d, months):
    """`d` moved forward by whole months, clamped to the target month's last day."""
    index = d.month - 1 + months
    year, month = d.year + index // 12, index % 12 + 1
    return d.replace(year=year, month=month, day=min(d.day, calendar.monthrange(year, month)[1]))


class InvoiceGenerateView(SuperAdminRequiredMixin, View):
    """Generate an invoice for one specific client (optionally with a coupon).
    Reached from the client's page (client fixed) or from the invoice list
    (pick the client). The invoice lands on that client's own Billing page."""

    template_name = "superadmin/invoice_generate.html"

    def _initial_for(self, env, org):
        today = timezone.localdate()
        initial = {"invoice_date": today, "due_date": today + datetime.timedelta(days=7)}
        if org is not None:
            initial["organization"] = org.pk
            sub = billing_services.get_current_subscription(org.id, using=env)
            if sub is not None:
                yearly = sub.billing_cycle == Subscription.BillingCycle.YEARLY
                initial["subtotal"] = sub.plan.yearly_price if yearly else sub.plan.monthly_price
                initial["organization_discount"] = sub.discount or 0
                period_end = _add_months(today, 12 if yearly else 1) - datetime.timedelta(days=1)
                initial["service_billing_start_date"] = today
                initial["service_billing_end_date"] = period_end
        return initial

    def _render(self, request, env, form, org, sub):
        coupons = [
            {"id": c.pk, "code": c.code, "type": c.discount_type, "value": str(c.discount_value)}
            for c in Coupon.objects.using(env).filter(is_active=True)
        ]
        # Organization discount = the discount set on each client's current subscription.
        org_discounts = {
            str(s.organization_id): str(s.discount)
            for s in Subscription.objects.using(env).filter(is_current=True, discount__gt=0)
        }
        org_cycles = {
            str(s.organization_id): s.billing_cycle
            for s in Subscription.objects.using(env).filter(is_current=True)
        }
        return render(request, self.template_name, {
            "env_key": env, "env_label": ENVIRONMENTS[env], "form": form, "org": org, "subscription": sub,
            "coupon_data": coupons, "org_discount_data": org_discounts, "org_cycle_data": org_cycles,
        })

    def get(self, request, env, pk=None):
        _env_label_or_404(env)
        org = None
        if pk is not None:
            org = get_object_or_404(Organization.objects.using(env), pk=pk)
        elif request.GET.get("org"):
            org = Organization.objects.using(env).filter(pk=request.GET["org"]).first()
        form = GenerateInvoiceForm(using=env, initial=self._initial_for(env, org))
        sub = billing_services.get_current_subscription(org.id, using=env) if org else None
        return self._render(request, env, form, org, sub)

    def post(self, request, env, pk=None):
        _env_label_or_404(env)
        fixed_org = get_object_or_404(Organization.objects.using(env), pk=pk) if pk is not None else None
        data = request.POST.copy()
        if fixed_org is not None:
            data["organization"] = str(fixed_org.pk)
        form = GenerateInvoiceForm(data, using=env)
        if not form.is_valid():
            org = fixed_org
            return self._render(request, env, form, org, None)

        cd = form.cleaned_data
        org = cd["organization"]
        subscription = billing_services.get_current_subscription(org.id, using=env)
        org_discount = cd.get("organization_discount") or 0
        extra_discount = cd.get("discount") or 0
        gst_percentage = cd.get("tax") or 0
        # Calculate GST on amount after all discounts
        taxable_amount = max(cd["subtotal"] - org_discount - extra_discount, 0)
        gst_amount = taxable_amount * gst_percentage / 100 if gst_percentage > 0 else 0
        try:
            with transaction.atomic(using=env):
                invoice = invoicing.create_invoice(
                    org, using=env, subscription=subscription, subtotal=cd["subtotal"],
                    discount=org_discount + extra_discount, tax=gst_amount, currency=org.currency,
                    invoice_date=cd["invoice_date"], due_date=cd["due_date"],
                    service_billing_start_date=cd.get("service_billing_start_date"),
                    service_billing_end_date=cd.get("service_billing_end_date"),
                    status=cd["status"],
                )
                if cd.get("coupon"):
                    billing_services.apply_coupon_as_admin(coupon=cd["coupon"], invoice=invoice, using=env)
        except billing_services.CouponError as exc:
            form.add_error("coupon", str(exc))
            return self._render(request, env, form, org, subscription)

        invoice.refresh_from_db(using=env)
        price = f"{org.currency} {invoice.total:,.0f}"
        if invoice.discount > 0:
            price += f" (was {org.currency} {invoice.subtotal:,.0f})"
        messages.success(
            request,
            f"Invoice {invoice.invoice_number} generated for {org.name} - {price}. "
            "It's now on their Billing page.",
        )
        return redirect("superadmin:invoice_detail", env=env, pk=invoice.pk)


class ApplyInvoiceCouponView(SuperAdminRequiredMixin, View):
    """Attach a coupon to an existing, unpaid invoice - the client's Billing
    page then shows the reduced amount."""

    def post(self, request, env, pk):
        _env_label_or_404(env)
        invoice = get_object_or_404(Invoice.objects.using(env).select_related("organization"), pk=pk)
        form = ApplyInvoiceCouponForm(request.POST, using=env)
        if form.is_valid():
            try:
                redemption = billing_services.apply_coupon_as_admin(
                    coupon=form.cleaned_data["coupon"], invoice=invoice, using=env
                )
            except billing_services.CouponError as exc:
                messages.error(request, str(exc))
            else:
                invoice.refresh_from_db(using=env)
                messages.success(
                    request,
                    f"Coupon {redemption.coupon.code} applied - {invoice.organization.name} now pays "
                    f"{invoice.currency} {invoice.total:,.0f} (was {invoice.currency} {invoice.subtotal:,.0f}).",
                )
        else:
            messages.error(request, "Choose a coupon to apply.")
        return redirect("superadmin:invoice_detail", env=env, pk=pk)


class InvoiceRecordPaymentView(SuperAdminRequiredMixin, View):
    """Record a manual (off-platform) payment against one specific invoice.
    Same effect as any other payment: the invoice's paid/due amounts and status
    update, and once it's fully paid the client's subscription renews and a
    payment-pending hold on their service lifts by itself."""

    def post(self, request, env, pk):
        _env_label_or_404(env)
        invoice = get_object_or_404(Invoice.objects.using(env).select_related("organization"), pk=pk)
        if invoice.status in (Invoice.Status.CANCELLED, Invoice.Status.DRAFT, Invoice.Status.PAID) or invoice.amount_due <= 0:
            messages.error(request, "This invoice isn't open for payment.")
            return redirect("superadmin:invoice_detail", env=env, pk=pk)
        form = RecordInvoicePaymentForm(request.POST, amount_due=invoice.amount_due)
        if not form.is_valid():
            messages.error(request, "Couldn't record the payment: " + "; ".join(
                f"{f}: {' '.join(errs)}" for f, errs in form.errors.items()))
            return redirect("superadmin:invoice_detail", env=env, pk=pk)

        cd = form.cleaned_data
        org = invoice.organization
        payment_date = timezone.make_aware(datetime.datetime.combine(cd["payment_date"], datetime.time.min))
        payment, _created = payment_services.record_payment(
            org, using=env,
            subscription=invoice.subscription or billing_services.get_current_subscription(org.id, using=env),
            invoice=invoice, amount=cd["amount"], currency=invoice.currency,
            payment_method=cd["payment_method"], gateway=Payment.Gateway.MANUAL,
            transaction_id=cd.get("transaction_id", ""), status=Payment.Status.SUCCESS,
            payment_date=payment_date,
            raw_response={"recorded_by": request.user.email, "notes": cd.get("notes", "")},
        )
        invoice.refresh_from_db(using=env)
        left = f" {invoice.currency} {invoice.amount_due:,.0f} still due." if invoice.amount_due > 0 else " Invoice fully paid."
        messages.success(
            request, f"Recorded {invoice.currency} {cd['amount']:,.0f} ({payment.payment_id}) against {invoice.invoice_number}.{left}"
        )
        return redirect("superadmin:invoice_detail", env=env, pk=pk)


class InvoiceDownloadView(SuperAdminRequiredMixin, View):
    def get(self, request, env, pk):
        _env_label_or_404(env)
        invoice = get_object_or_404(
            Invoice.objects.using(env).select_related("organization", "subscription", "subscription__plan"),
            pk=pk,
        )
        try:
            from xhtml2pdf import pisa
        except ImportError:
            messages.error(request, "PDF export isn't available on this server.")
            return redirect("superadmin:invoice_detail", env=env, pk=pk)

        from io import BytesIO

        from django.template.loader import render_to_string

        html = render_to_string(
            "superadmin/pdf/invoice_pdf.html", {"invoice": invoice, "organization": invoice.organization}
        )
        buffer = BytesIO()
        pisa.CreatePDF(src=html, dest=buffer)
        response = HttpResponse(buffer.getvalue(), content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="{invoice.invoice_number}.pdf"'
        return response


class ClientFinancialHistoryView(SuperAdminRequiredMixin, TemplateView):
    template_name = "superadmin/financial_history.html"

    def get_context_data(self, **kwargs):
        env = kwargs["env"]
        context = super().get_context_data(**kwargs)
        context["env_key"] = env
        context["env_label"] = _env_label_or_404(env)
        org = get_object_or_404(Organization.objects.using(env), pk=kwargs["pk"])
        context["org"] = org
        context["invoices"] = (
            Invoice.objects.using(env).filter(organization_id=org.id).order_by("-invoice_date", "-created_at")
        )
        context["payments"] = (
            Payment.objects.using(env).filter(organization_id=org.id).order_by("-payment_date")
        )
        context["record_payment_form"] = RecordPaymentForm(
            using=env, organization=org, initial={"payment_date": timezone.localdate()}
        )
        context["create_invoice_form"] = CreateInvoiceForm(
            using=env, organization=org,
            initial={"invoice_date": timezone.localdate(), "due_date": timezone.localdate() + datetime.timedelta(days=7)},
        )
        return context


class CreateInvoiceView(SuperAdminRequiredMixin, View):
    """Manually generate an invoice for a client. It appears immediately on
    that client's own Billing page (apps.finance) as a pending amount."""

    def post(self, request, env, pk):
        _env_label_or_404(env)
        org = get_object_or_404(Organization.objects.using(env), pk=pk)
        form = CreateInvoiceForm(request.POST, using=env, organization=org)
        if form.is_valid():
            data = form.cleaned_data
            invoice = invoicing.create_invoice(
                org,
                using=env,
                subscription=data.get("subscription"),
                subtotal=data["subtotal"],
                discount=data.get("discount") or 0,
                tax=data.get("tax") or 0,
                currency=org.currency,
                invoice_date=data["invoice_date"],
                due_date=data["due_date"],
                service_billing_start_date=data.get("service_billing_start_date"),
                service_billing_end_date=data.get("service_billing_end_date"),
                status=data["status"],
            )
            messages.success(request, f"Created invoice {invoice.invoice_number} for {org.name}.")
        else:
            messages.error(request, "Couldn't create the invoice — please check the form.")
        return redirect("superadmin:financial_history", env=env, pk=pk)


class RecordPaymentView(SuperAdminRequiredMixin, View):
    """Manually record an off-platform payment against a client — bank
    transfer, cash, cheque. Gateway is always MANUAL; a real gateway
    payment arrives through the webhook (apps.billing.views) instead."""

    def post(self, request, env, pk):
        _env_label_or_404(env)
        org = get_object_or_404(Organization.objects.using(env), pk=pk)
        form = RecordPaymentForm(request.POST, using=env, organization=org)
        if form.is_valid():
            data = form.cleaned_data
            subscription = billing_services.get_current_subscription(org.id, using=env)
            payment_date = timezone.make_aware(
                datetime.datetime.combine(data["payment_date"], datetime.time.min)
            )
            payment, _created = payment_services.record_payment(
                org,
                using=env,
                subscription=subscription,
                invoice=data.get("invoice"),
                amount=data["amount"],
                currency=data["currency"] or "INR",
                payment_method=data["payment_method"],
                gateway=Payment.Gateway.MANUAL,
                transaction_id=data.get("transaction_id", ""),
                status=data["status"],
                payment_date=payment_date,
                failure_reason=data.get("notes", "") if data["status"] == Payment.Status.FAILED else "",
                raw_response={"recorded_by": request.user.email, "notes": data.get("notes", "")},
            )
            messages.success(request, f"Recorded payment {payment.payment_id} for {org.name}.")
        else:
            messages.error(request, "Couldn't record the payment — please check the form.")
        return redirect("superadmin:financial_history", env=env, pk=pk)


class SupportRequestListView(SuperAdminRequiredMixin, TemplateView):
    """Help / Support messages clients have sent in this environment."""

    template_name = "superadmin/support_requests.html"

    def get_context_data(self, **kwargs):
        env = kwargs["env"]
        context = super().get_context_data(**kwargs)
        context["env_key"] = env
        context["env_label"] = _env_label_or_404(env)

        status = self.request.GET.get("status", "OPEN")
        qs = SupportRequest.objects.using(env).select_related("organization")
        if status in SupportRequest.Status.values:
            qs = qs.filter(status=status)
        else:
            status = "ALL"
        context["requests"] = qs.order_by("-created_at")
        context["status_filter"] = status
        context["status_choices"] = [("OPEN", "Open"), ("RESOLVED", "Resolved"), ("ALL", "All")]
        return context


class SupportRequestDetailView(SuperAdminRequiredMixin, View):
    """Read a client's message, reply to it, and mark it resolved or still open."""

    def _render(self, request, env, support_request, form):
        return render(
            request,
            "superadmin/support_detail.html",
            {
                "env_key": env,
                "env_label": ENVIRONMENTS[env],
                "support_request": support_request,
                "organization": support_request.organization,
                "form": form,
            },
        )

    def _get(self, env, pk):
        _env_label_or_404(env)
        return get_object_or_404(SupportRequest.objects.using(env).select_related("organization"), pk=pk)

    def get(self, request, env, pk):
        support_request = self._get(env, pk)
        return self._render(request, env, support_request, SupportReplyForm(instance=support_request))

    def post(self, request, env, pk):
        support_request = self._get(env, pk)
        form = SupportReplyForm(request.POST, instance=support_request)
        if not form.is_valid():
            return self._render(request, env, support_request, form)

        support_request = form.save(commit=False)
        if "admin_reply" in form.changed_data and support_request.admin_reply:
            support_request.replied_by_email = request.user.email
            support_request.replied_at = timezone.now()
        support_request.save(using=env)
        messages.success(request, "Reply saved. The client will see it on their Help / Support page.")
        return redirect("superadmin:support_detail", env=env, pk=pk)


class StorageChangeRequestListView(SuperAdminRequiredMixin, TemplateView):
    """Storage change requests clients have submitted in this environment."""

    template_name = "superadmin/storage_change_requests.html"

    def get_context_data(self, **kwargs):
        env = kwargs["env"]
        context = super().get_context_data(**kwargs)
        context["env_key"] = env
        context["env_label"] = _env_label_or_404(env)

        status = self.request.GET.get("status", "PENDING")
        qs = DataStorageChangeRequest.objects.using(env).select_related("organization")
        if status in DataStorageChangeRequest.Status.values:
            qs = qs.filter(status=status)
        else:
            status = "ALL"
        context["requests"] = qs.order_by("-requested_at")
        context["status_filter"] = status
        context["status_choices"] = [("PENDING", "Pending"), ("APPROVED", "Awaiting client"), ("COMPLETED", "Completed"), ("REJECTED", "Rejected"), ("ALL", "All")]
        return context


class StorageChangeRequestDetailView(SuperAdminRequiredMixin, View):
    """Review a storage change request: approve (switches storage mode and the
    subscription's plan) or reject."""

    def _render(self, request, env, storage_request, form):
        return render(
            request,
            "superadmin/storage_change_detail.html",
            {
                "env_key": env,
                "env_label": ENVIRONMENTS[env],
                "storage_request": storage_request,
                "organization": storage_request.organization,
                "form": form,
            },
        )

    def _get(self, env, pk):
        _env_label_or_404(env)
        return get_object_or_404(DataStorageChangeRequest.objects.using(env).select_related("organization"), pk=pk)

    def get(self, request, env, pk):
        storage_request = self._get(env, pk)
        return self._render(request, env, storage_request, ApproveStorageChangeForm(instance=storage_request))

    def post(self, request, env, pk):
        storage_request = self._get(env, pk)
        if storage_request.status != DataStorageChangeRequest.Status.PENDING:
            messages.error(request, "This request has already been reviewed.")
            return redirect("superadmin:storage_change_detail", env=env, pk=pk)

        form = ApproveStorageChangeForm(request.POST, instance=storage_request)
        if not form.is_valid():
            return self._render(request, env, storage_request, form)

        storage_request = form.save(commit=False)
        storage_request.reviewed_by_email = request.user.email
        storage_request.reviewed_at = timezone.now()
        with transaction.atomic(using=env):
            if storage_request.status == DataStorageChangeRequest.Status.APPROVED:
                if storage_request.requested_storage == Organization.StorageMode.OUR_DATABASE:
                    storage_request.status = DataStorageChangeRequest.Status.COMPLETED
                    storage_request.completed_at = timezone.now()
                # A switch to Drive stays APPROVED until the client connects
                # their Google account (the OAuth callback completes it).
                storage_request.save(using=env)
                _handle_approved_storage_change(storage_request)
                messages.success(request, f"Approved. Storage change started for {storage_request.organization.name}.")
            else:
                storage_request.save(using=env)
                messages.info(request, "Request rejected.")
        return redirect("superadmin:storage_change_requests", env=env)
