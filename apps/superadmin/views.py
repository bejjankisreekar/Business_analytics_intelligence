import calendar
import datetime
import json
import logging
import os
import subprocess
from decimal import Decimal

from django.contrib import messages
from django.db import DatabaseError, transaction
from django.db.models import Count, Max, Q, Sum
from django.http import FileResponse, Http404, HttpResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.generic import FormView, TemplateView, View

from apps.accounts.mixins import SuperAdminRequiredMixin
from apps.accounts.models import User
from apps.billing import invoicing
from apps.billing import services as billing_services
from apps.billing import payments as payment_services
from apps.billing.models import Coupon, CouponRedemption, Invoice, Payment, Plan, Subscription
from apps.organizations import service_control
from apps.organizations.models import Organization, ServiceStatusChange
from apps.organizations.backup import dump_schema_archive, dump_schema_sql, find_pg_dump
from apps.organizations.utils import SCHEMA_RE, TenantSchemaError, rename_tenant_schema, schema_exists
from apps.organizations.services import OrganizationSignupError, create_organization_with_tenant_schema_and_admin

from .forms import (
    ApplyInvoiceCouponForm,
    ClientCreateForm,
    ClientProfileForm,
    ClientUserEditForm,
    CouponForm,
    CreateInvoiceForm,
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
)

logger = logging.getLogger(__name__)

ENVIRONMENTS = {
    "dev": "Development",
    "prod": "Production",
}


def _env_label_or_404(env):
    label = ENVIRONMENTS.get(env)
    if label is None:
        raise Http404("Unknown environment")
    return label


def _month_keys(months: int):
    """Last `months` (year, month) tuples ending at the current month."""
    today = timezone.localdate()
    keys = []
    y, m = today.year, today.month
    for _ in range(months):
        keys.append((y, m))
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    return list(reversed(keys))


class EnvironmentOverviewView(SuperAdminRequiredMixin, TemplateView):
    """The business's own financial dashboard — revenue, outstanding
    balances, and client counts, per environment. Environment browsing
    itself lives in the top nav / sidebar now; this page is a cross-
    environment summary, not another way to pick one.
    """

    template_name = "superadmin/overview.html"
    REVENUE_MONTHS = 6

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["stats"] = {env: self._env_stats(env) for env in ENVIRONMENTS}
        context["revenue_chart"] = json.dumps(self._revenue_chart_data())
        context["payment_status_chart"] = json.dumps(self._payment_status_chart_data("prod"))
        return context

    def _env_stats(self, env):
        # An environment's database may be unreachable (e.g. no DEV_DB_* on a
        # production host); show zeros for it instead of failing the page.
        try:
            return self._compute_env_stats(env)
        except DatabaseError:
            logger.exception("Superadmin overview: %s database unavailable", env)
            return {
                "total_clients": 0, "active_services": 0, "new_this_month": 0,
                "total_revenue": Decimal("0"), "outstanding": Decimal("0"),
            }

    def _compute_env_stats(self, env):
        month_start = timezone.localdate().replace(day=1)
        total_revenue = (
            Payment.objects.using(env).filter(status=Payment.Status.SUCCESS).aggregate(t=Sum("amount"))["t"]
            or Decimal("0")
        )
        outstanding = (
            Invoice.objects.using(env)
            .exclude(status__in=[Invoice.Status.PAID, Invoice.Status.CANCELLED, Invoice.Status.DRAFT])
            .aggregate(t=Sum("amount_due"))["t"]
            or Decimal("0")
        )
        return {
            "total_clients": Organization.objects.using(env).count(),
            "active_services": Organization.objects.using(env)
            .filter(service_status=Organization.ServiceStatus.ACTIVE)
            .count(),
            "new_this_month": Organization.objects.using(env).filter(created_at__date__gte=month_start).count(),
            "total_revenue": total_revenue,
            "outstanding": outstanding,
        }

    def _revenue_chart_data(self):
        keys = _month_keys(self.REVENUE_MONTHS)
        labels = [f"{calendar.month_abbr[m]} {y}" for y, m in keys]
        series = {}
        for env in ENVIRONMENTS:
            buckets = {k: Decimal("0") for k in keys}
            try:
                payments = list(
                    Payment.objects.using(env).filter(
                        status=Payment.Status.SUCCESS, payment_date__year__gte=keys[0][0]
                    )
                )
            except DatabaseError:
                logger.exception("Superadmin overview: %s database unavailable", env)
                payments = []
            for p in payments:
                k = (p.payment_date.year, p.payment_date.month)
                if k in buckets:
                    buckets[k] += p.amount
            series[env] = [float(buckets[k]) for k in keys]
        return {"labels": labels, "dev": series["dev"], "prod": series["prod"]}

    def _payment_status_chart_data(self, env):
        try:
            counts = {row["status"]: row["c"] for row in Payment.objects.using(env).values("status").annotate(c=Count("id"))}
        except DatabaseError:
            logger.exception("Superadmin overview: %s database unavailable", env)
            counts = {}
        labels = [label for _value, label in Payment.Status.choices]
        values = [counts.get(value, 0) for value, _label in Payment.Status.choices]
        return {"labels": labels, "values": values}


class OrganizationListView(SuperAdminRequiredMixin, TemplateView):
    template_name = "superadmin/org_list.html"

    def get_context_data(self, **kwargs):
        env = kwargs["env"]
        context = super().get_context_data(**kwargs)
        context["env_key"] = env
        context["env_label"] = _env_label_or_404(env)

        qs = Organization.objects.using(env).annotate(
            user_count=Count("users", distinct=True),
            last_activity=Max("users__last_login"),
        )

        q = self.request.GET.get("q", "").strip()
        if q:
            qs = qs.filter(
                Q(name__icontains=q)
                | Q(organization_code__icontains=q)
                | Q(contact_person__icontains=q)
                | Q(contact_email__icontains=q)
            )

        status = self.request.GET.get("status", "")
        if status == "active":
            qs = qs.filter(service_status=Organization.ServiceStatus.ACTIVE)
        elif status == "suspended":
            qs = qs.filter(service_status=Organization.ServiceStatus.SUSPENDED)

        business_type = self.request.GET.get("business_type", "")
        if business_type:
            qs = qs.filter(business_type=business_type)

        plan_id = self.request.GET.get("plan", "")
        if plan_id:
            qs = qs.filter(
                subscriptions__is_current=True, subscriptions__plan_id=plan_id
            )

        organizations = list(qs.order_by("-created_at"))

        # One extra query to attach each org's current subscription — cheap
        # (single IN query) and avoids fragile cross-alias annotate/subquery
        # tricks for a "dev"/"prod" alias that isn't "default".
        current_subs = (
            Subscription.objects.using(env)
            .filter(organization_id__in=[o.id for o in organizations], is_current=True)
            .select_related("plan")
        )
        subs_by_org = {sub.organization_id: sub for sub in current_subs}
        for org in organizations:
            org.current_subscription = subs_by_org.get(org.id)

        context["organizations"] = organizations
        context["q"] = q
        context["status"] = status
        context["business_type"] = business_type
        context["plan"] = plan_id
        context["business_type_choices"] = Organization.BusinessType.choices
        context["plan_choices"] = Plan.objects.using(env).order_by("name")
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
        context["pg_dump_available"] = find_pg_dump() is not None
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
        return context


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


class OrganizationSchemaRenameView(SuperAdminRequiredMixin, View):
    """Rename an organization's Postgres schema and keep `schema_name` in sync."""

    def post(self, request, env, pk):
        _env_label_or_404(env)
        org = get_object_or_404(Organization.objects.using(env), pk=pk)
        new_name = (request.POST.get("schema_name") or "").strip().lower()
        back = redirect("superadmin:org_detail", env=env, pk=pk)

        if new_name == org.schema_name:
            return back
        if not SCHEMA_RE.match(new_name) or new_name == "public" or new_name.startswith("pg_"):
            messages.error(request, "Schema name may only contain lowercase letters, digits and underscores (max 63).")
            return back
        if Organization.objects.using(env).filter(schema_name=new_name).exclude(pk=org.pk).exists():
            messages.error(request, "Another organization already uses that schema name.")
            return back
        if schema_exists(new_name, using=env):
            messages.error(request, f"A schema named '{new_name}' already exists in the {env} database.")
            return back

        old_name = org.schema_name
        try:
            with transaction.atomic(using=env):
                if schema_exists(old_name, using=env):
                    rename_tenant_schema(old_name, new_name, using=env)
                org.schema_name = new_name
                org.save(using=env, update_fields=["schema_name", "updated_at"])
        except (DatabaseError, TenantSchemaError) as exc:
            logger.exception("Schema rename failed for org %s", org.pk)
            messages.error(request, f"Could not rename schema: {exc}")
            return back

        messages.success(request, f"Schema renamed from {old_name} to {new_name}.")
        return back


class OrganizationSchemaBackupView(SuperAdminRequiredMixin, View):
    """Download a restorable SQL backup (structure + data) of the tenant schema."""

    def get(self, request, env, pk):
        _env_label_or_404(env)
        org = get_object_or_404(Organization.objects.using(env), pk=pk)
        if not schema_exists(org.schema_name, using=env):
            messages.error(request, f"Schema {org.schema_name} does not exist in the {env} database.")
            return redirect("superadmin:org_detail", env=env, pk=pk)

        stamp = timezone.now().strftime("%Y%m%d_%H%M%S")
        if request.GET.get("format") == "custom":
            try:
                path = dump_schema_archive(org.schema_name, using=env)
            except (TenantSchemaError, OSError, subprocess.SubprocessError) as exc:
                logger.exception("pg_dump backup failed for org %s", org.pk)
                messages.error(request, f"Could not create pg_restore backup: {exc}")
                return redirect("superadmin:org_detail", env=env, pk=pk)
            # Unlink now; the open handle keeps the data readable on POSIX, and
            # on Windows the temp file is removed by the OS temp cleanup.
            handle = open(path, "rb")
            try:
                os.unlink(path)
            except OSError:
                pass
            return FileResponse(
                handle, as_attachment=True, filename=f"{org.schema_name}_{stamp}.dump",
                content_type="application/octet-stream",
            )
        response = StreamingHttpResponse(
            dump_schema_sql(org.schema_name, using=env), content_type="application/sql; charset=utf-8"
        )
        response["Content-Disposition"] = f'attachment; filename="{org.schema_name}_{stamp}.sql"'
        response["Cache-Control"] = "no-store"
        return response


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
        context["plans"] = Plan.objects.using(env).order_by("monthly_price", "name")
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
        for coupon in coupons:
            coupon.redemption_count = redemption_counts.get(coupon.id, 0)
        context["coupons"] = coupons
        return context


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

        date_from = self.request.GET.get("from", "")
        if date_from:
            qs = qs.filter(payment_date__date__gte=date_from)
        date_to = self.request.GET.get("to", "")
        if date_to:
            qs = qs.filter(payment_date__date__lte=date_to)

        context["payments"] = qs.order_by("-payment_date")
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

        date_from = self.request.GET.get("from", "")
        if date_from:
            qs = qs.filter(invoice_date__gte=date_from)
        date_to = self.request.GET.get("to", "")
        if date_to:
            qs = qs.filter(invoice_date__lte=date_to)

        context["invoices"] = qs.order_by("-invoice_date", "-created_at")
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
        return initial

    def _render(self, request, env, form, org, sub):
        coupons = [
            {"id": c.pk, "code": c.code, "type": c.discount_type, "value": str(c.discount_value)}
            for c in Coupon.objects.using(env).filter(is_active=True)
        ]
        return render(request, self.template_name, {
            "env_key": env, "env_label": ENVIRONMENTS[env], "form": form, "org": org, "subscription": sub,
            "coupon_data": coupons,
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
        try:
            with transaction.atomic(using=env):
                invoice = invoicing.create_invoice(
                    org, using=env, subscription=subscription, subtotal=cd["subtotal"],
                    discount=cd.get("discount") or 0, tax=cd.get("tax") or 0, currency=org.currency,
                    invoice_date=cd["invoice_date"], due_date=cd["due_date"], status=cd["status"],
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
