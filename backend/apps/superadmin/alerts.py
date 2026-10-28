"""What needs a superadmin's attention in one environment right now — the
overview's "Needs attention" panel, the sidebar's count badges and the
alert digest all read from here, so they always agree.
"""
import datetime
from decimal import Decimal

from django.db import DatabaseError
from django.db.models import Sum
from django.urls import reverse
from django.utils import timezone

from apps.billing.models import Invoice, Payment, PaymentWebhookEvent, Subscription
from apps.organizations.models import CloudBackupConnection, DataStorageChangeRequest, SupportRequest

from .analytics import OPEN_INVOICE

FAILED_PAYMENT_DAYS = 30
TRIAL_WARNING_DAYS = 7
WEBHOOK_FAILURE_DAYS = 7


def nav_counts(env: str) -> dict:
    """Small per-section counts for the sidebar badges. Zeros when the
    environment's database can't be read."""
    today = timezone.localdate()
    try:
        return {
            "storage_requests": DataStorageChangeRequest.objects.using(env)
            .filter(status=DataStorageChangeRequest.Status.PENDING).count(),
            "support_requests": SupportRequest.objects.using(env)
            .filter(status=SupportRequest.Status.OPEN).count(),
            "overdue_invoices": Invoice.objects.using(env)
            .filter(status__in=OPEN_INVOICE, due_date__lt=today).count(),
            "failed_payments": Payment.objects.using(env).filter(
                status=Payment.Status.FAILED,
                payment_date__gte=timezone.now() - datetime.timedelta(days=FAILED_PAYMENT_DAYS),
            ).count(),
            "failed_webhooks": PaymentWebhookEvent.objects.using(env).filter(
                status=PaymentWebhookEvent.Status.FAILED,
                received_at__gte=timezone.now() - datetime.timedelta(days=WEBHOOK_FAILURE_DAYS),
            ).count(),
        }
    except DatabaseError:
        return {"storage_requests": 0, "overdue_invoices": 0, "failed_payments": 0, "failed_webhooks": 0}


def attention_items(env: str, *, include_activity: bool = True) -> list[dict]:
    """[{severity, title, detail, count, amount, href}] most urgent first.
    Only items with something in them are returned. Activity notices (clients
    who stopped posting entries) are on-screen only: the alert digest passes
    include_activity=False so nothing about them is ever emailed or posted."""
    today = timezone.localdate()
    now = timezone.now()
    items = []

    def add(severity, key, title, detail, count, href, amount=None):
        if count:
            items.append({
                "severity": severity, "key": key, "title": title, "detail": detail,
                "count": count, "amount": amount, "href": href, "env": env,
            })

    payments = Payment.objects.using(env)
    failed = payments.filter(
        status=Payment.Status.FAILED,
        payment_date__gte=now - datetime.timedelta(days=FAILED_PAYMENT_DAYS),
    )
    add("critical", "failed_payments", "Failed payments", f"In the last {FAILED_PAYMENT_DAYS} days",
        failed.count(), reverse("superadmin:payment_list", args=[env]) + "?status=FAILED",
        failed.aggregate(t=Sum("amount"))["t"] or Decimal("0"))

    overdue = Invoice.objects.using(env).filter(status__in=OPEN_INVOICE, due_date__lt=today)
    add("critical", "overdue_invoices", "Overdue invoices", "Past their due date and still unpaid",
        overdue.count(), reverse("superadmin:invoice_list", args=[env]) + "?overdue=1",
        overdue.aggregate(t=Sum("amount_due"))["t"] or Decimal("0"))

    past_due = Subscription.objects.using(env).filter(
        is_current=True, status__in=[Subscription.Status.PAST_DUE, Subscription.Status.PAYMENT_DUE]
    )
    add("warning", "past_due", "Subscriptions awaiting payment", "Payment due or past due",
        past_due.count(), reverse("superadmin:subscription_list", args=[env]) + "?status=PAST_DUE")

    trials = Subscription.objects.using(env).filter(
        is_current=True, status=Subscription.Status.TRIAL,
        trial_end_date__gte=today, trial_end_date__lte=today + datetime.timedelta(days=TRIAL_WARNING_DAYS),
    )
    add("warning", "trials_ending", "Trials ending soon", f"Within {TRIAL_WARNING_DAYS} days — follow up to convert",
        trials.count(), reverse("superadmin:subscription_list", args=[env]) + "?status=TRIAL&trial_ending=1")

    pending = DataStorageChangeRequest.objects.using(env).filter(status=DataStorageChangeRequest.Status.PENDING)
    add("info", "storage_requests", "Storage requests to review", "Clients waiting on approval",
        pending.count(), reverse("superadmin:storage_change_requests", args=[env]))

    open_support = SupportRequest.objects.using(env).filter(status=SupportRequest.Status.OPEN)
    add("info", "support_requests", "Support messages to answer", "Clients waiting on a reply",
        open_support.count(), reverse("superadmin:support_requests", args=[env]))

    sync_errors = CloudBackupConnection.objects.using(env).exclude(last_sync_error="")
    add("warning", "drive_errors", "Google Drive sync errors", "Clients whose Sheet can't be reached",
        sync_errors.count(), reverse("superadmin:system_health", args=[env]) + "#drive")

    hooks = PaymentWebhookEvent.objects.using(env).filter(
        status=PaymentWebhookEvent.Status.FAILED,
        received_at__gte=now - datetime.timedelta(days=WEBHOOK_FAILURE_DAYS),
    )
    add("warning", "failed_webhooks", "Failed payment webhooks", f"In the last {WEBHOOK_FAILURE_DAYS} days",
        hooks.count(), reverse("superadmin:webhook_list", args=[env]) + "?status=FAILED")

    if include_activity:
        from .health import MISSING_ENTRY_DAYS, silent_clients

        add("warning", "missing_entries", "Clients missing entries",
            f"Usually post daily, but nothing for {MISSING_ENTRY_DAYS}+ days",
            len(silent_clients(env)), reverse("superadmin:org_list", args=[env]) + "?missing_entries=1")

    order = {"critical": 0, "warning": 1, "info": 2}
    items.sort(key=lambda i: order[i["severity"]])
    return items


def safe_attention_items(env: str) -> tuple[list[dict], bool]:
    """(items, db_ok)."""
    try:
        return attention_items(env), True
    except DatabaseError:
        return [], False
