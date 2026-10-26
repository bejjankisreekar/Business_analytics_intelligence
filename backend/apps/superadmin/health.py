"""Per-client usage and a simple health score, so the console can show who
is actively using Finday and who is drifting away.

Usage comes from what we can see without opening a client's data store:
- everyone: team size and the latest login of anyone on the team;
- own-database clients: how many sales/expense/purchase entries they've
  made (in total and in the last 30 days) and when the latest one was;
- Google Sheets clients: their data lives in their own Sheet, so we only
  see whether the connection works (last sync, last error).

Finance models normally scope themselves to the request's organization
(SheetAwareManager); `_base_manager` is Django's plain manager, so these
counts always cover every client, whatever request they run in.
"""
import datetime
from collections import defaultdict

from django.apps import apps
from django.db import DatabaseError, connections, transaction
from django.db.models import Count, Max, Q
from django.utils import timezone

from apps.accounts.models import User
from apps.billing.models import Invoice, Subscription
from apps.organizations.models import CloudBackupConnection, Organization

from .analytics import OPEN_INVOICE

ACTIVE_WINDOW_DAYS = 30
INACTIVE_DAYS = 30

HEALTHY, AT_RISK, CRITICAL = "healthy", "at_risk", "critical"
LABELS = {HEALTHY: "Healthy", AT_RISK: "At risk", CRITICAL: "Critical"}


def _entry_models():
    from apps.finance.models import ExpenseEntry, PurchaseEntry, SalesEntry

    return (SalesEntry, ExpenseEntry, PurchaseEntry)


MISSING_ENTRY_DAYS = 3        # no entry for this many days = "gone quiet"
PATTERN_WINDOW_DAYS = 30      # how far back we look for a normal posting rhythm
PATTERN_MIN_DAYS = 5          # distinct entry days in that window to count as "regular"
LOOKBACK_DAYS = 60


def silent_clients(env: str, min_days: int = MISSING_ENTRY_DAYS) -> dict:
    """{org_id: days since last entry} for own-database clients that normally
    post entries but have gone quiet. "Normally" = entries on at least
    PATTERN_MIN_DAYS different days in the PATTERN_WINDOW_DAYS up to their last
    entry, so a client who only ever logs a few entries isn't flagged. Clients
    with no entry in the last LOOKBACK_DAYS are left to the "no login" check."""
    today = timezone.localdate()
    since = today - datetime.timedelta(days=LOOKBACK_DAYS)
    days_by_org = defaultdict(set)
    try:
        for model in _entry_models():
            for org_id, day in (
                model._base_manager.using(env).filter(date__gte=since).values_list("organization_id", "date").distinct()
            ):
                days_by_org[org_id].add(day)
        own_db = set(
            Organization.objects.using(env)
            .filter(id__in=list(days_by_org), storage_mode=Organization.StorageMode.OUR_DATABASE,
                    service_status=Organization.ServiceStatus.ACTIVE)
            .values_list("id", flat=True)
        )
    except DatabaseError:
        return {}
    out = {}
    for org_id in own_db:
        days = days_by_org[org_id]
        last = max(days)
        gap = (today - last).days
        window_start = last - datetime.timedelta(days=PATTERN_WINDOW_DAYS - 1)
        if gap >= min_days and sum(1 for d in days if d >= window_start) >= PATTERN_MIN_DAYS:
            out[org_id] = gap
    return out


def db_bytes_for(env: str, org_ids) -> dict:
    """{org_id: approx bytes of finance rows} held in our database. Sums each
    row's stored size (pg_column_size) across every finance table; indexes and
    table overhead aren't counted, so read it as "data stored", not disk used."""
    org_ids = list(org_ids)
    totals = defaultdict(int)
    if not org_ids:
        return totals
    conn = connections[env]
    if conn.vendor != "postgresql":
        return totals
    quote = conn.ops.quote_name
    for model in apps.get_app_config("finance").get_models():
        if not any(f.name == "organization" for f in model._meta.get_fields()):
            continue
        sql = (
            f"SELECT organization_id, COALESCE(SUM(pg_column_size(t.*)), 0) "
            f"FROM {quote(model._meta.db_table)} t WHERE organization_id = ANY(%s) GROUP BY organization_id"
        )
        try:
            with transaction.atomic(using=env), conn.cursor() as cur:
                cur.execute(sql, [org_ids])
                for org_id, size in cur.fetchall():
                    totals[org_id] += int(size)
        except DatabaseError:
            continue  # table missing in this environment: counts as zero
    return totals


def usage_for(env: str, org_ids) -> dict:
    """{org_id: {...usage...}} for the given organizations."""
    org_ids = list(org_ids)
    out = defaultdict(lambda: {
        "users": 0, "active_users": 0, "last_login": None,
        "entries_total": 0, "entries_30d": 0, "last_entry_at": None,
        "sheet_connected": False, "last_synced_at": None, "sync_error": "",
        "overdue_invoices": 0, "db_bytes": 0,
    })
    if not org_ids:
        return out
    for org_id, size in db_bytes_for(env, org_ids).items():
        out[org_id]["db_bytes"] = size
    since = timezone.now() - datetime.timedelta(days=ACTIVE_WINDOW_DAYS)

    for row in (
        User.objects.using(env).filter(organization_id__in=org_ids)
        .values("organization_id")
        .annotate(n=Count("pk"), active=Count("pk", filter=Q(last_login__gte=since)), last=Max("last_login"))
    ):
        u = out[row["organization_id"]]
        u["users"], u["active_users"], u["last_login"] = row["n"], row["active"], row["last"]

    try:
        for model in _entry_models():
            for row in (
                model._base_manager.using(env).filter(organization_id__in=org_ids)
                .values("organization_id")
                .annotate(n=Count("pk"), recent=Count("pk", filter=Q(created_at__gte=since)), last=Max("created_at"))
            ):
                u = out[row["organization_id"]]
                u["entries_total"] += row["n"]
                u["entries_30d"] += row["recent"]
                if row["last"] and (u["last_entry_at"] is None or row["last"] > u["last_entry_at"]):
                    u["last_entry_at"] = row["last"]
    except DatabaseError:
        pass  # finance tables missing in this environment: usage just shows as zero

    for conn in CloudBackupConnection.objects.using(env).filter(organization_id__in=org_ids):
        u = out[conn.organization_id]
        u["sheet_connected"] = bool(conn.external_file_id)
        u["last_synced_at"] = conn.last_synced_at
        u["sync_error"] = conn.last_sync_error

    today = timezone.localdate()
    for row in (
        Invoice.objects.using(env).filter(organization_id__in=org_ids, status__in=OPEN_INVOICE, due_date__lt=today)
        .values("organization_id").annotate(n=Count("pk"))
    ):
        out[row["organization_id"]]["overdue_invoices"] = row["n"]
    return out


def score(org: Organization, subscription, usage: dict) -> dict:
    """{score: 0-100, level, label, reasons: [str]} — reasons are why points
    were taken off, most serious first."""
    now = timezone.now()
    today = timezone.localdate()
    points, reasons = 100, []

    def take(n, why):
        nonlocal points
        points -= n
        reasons.append((n, why))

    if org.service_status != Organization.ServiceStatus.ACTIVE:
        take(40, "Service suspended")

    status = subscription.status if subscription else None
    if subscription is None:
        take(20, "No subscription")
    elif status in (Subscription.Status.PAST_DUE, Subscription.Status.PAYMENT_DUE):
        take(25, "Payment overdue on subscription")
    elif status in (Subscription.Status.EXPIRED, Subscription.Status.CANCELLED, Subscription.Status.SUSPENDED):
        take(35, f"Subscription {subscription.get_status_display().lower()}")
    elif status == Subscription.Status.TRIAL and subscription.trial_end_date:
        days = (subscription.trial_end_date - today).days
        if 0 <= days <= 7:
            take(5, f"Trial ends in {days} day{'s' if days != 1 else ''}")

    if usage["overdue_invoices"]:
        take(20, f"{usage['overdue_invoices']} overdue invoice{'s' if usage['overdue_invoices'] != 1 else ''}")

    last_login = usage["last_login"]
    if last_login is None:
        take(30, "Nobody has ever logged in")
    else:
        idle = (now - last_login).days
        if idle > INACTIVE_DAYS:
            take(25, f"No login for {idle} days")
        elif idle > 14:
            take(10, f"No login for {idle} days")

    if org.storage_mode == Organization.StorageMode.OUR_DATABASE:
        if usage["entries_total"] == 0:
            take(15, "No entries recorded yet")
        elif usage["entries_30d"] == 0:
            take(15, f"No entries in the last {ACTIVE_WINDOW_DAYS} days")
    else:
        if usage["sync_error"]:
            take(20, "Google Sheet sync failing")
        elif not usage["sheet_connected"]:
            take(15, "Google Sheet not connected")

    points = max(points, 0)
    level = HEALTHY if points >= 75 else AT_RISK if points >= 45 else CRITICAL
    return {
        "score": points, "level": level, "label": LABELS[level],
        "reasons": [why for _n, why in sorted(reasons, key=lambda r: -r[0])],
    }


def is_inactive(usage: dict) -> bool:
    last = usage["last_login"]
    return last is None or (timezone.now() - last).days > INACTIVE_DAYS
