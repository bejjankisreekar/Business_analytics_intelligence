"""Database usage for one environment: how much space the database takes,
which app's tables take it, and how much each own-database client holds.

Sizes come from Postgres itself (pg_total_relation_size and friends), so the
numbers cover indexes and TOAST, not just row data. Row counts are the
planner's live-row estimate, which is cheap and close enough for a console.
"""
import logging
from collections import defaultdict

from django.apps import apps
from django.conf import settings
from django.db import DatabaseError, connections
from django.views.generic import TemplateView

from apps.accounts.mixins import SuperAdminRequiredMixin
from apps.organizations.models import Organization

from .health import db_bytes_for
from .views import _env_label_or_404

logger = logging.getLogger(__name__)

# What each app's tables hold, in plain words for the page.
APP_PURPOSE = {
    "organizations": "Client organizations, their storage choice, service status and Google Drive connections",
    "accounts": "Users, logins and team members",
    "billing": "Plans, subscriptions, invoices, payments and payment webhook events",
    "finance": "Sales, expense and purchase entries — the clients' own books",
    "superadmin": "Audit log, client notes and tags, support requests, storage requests and alert settings",
    "mobile_api": "Mobile app sign-in tokens",
    "quote_requests": "Customer quote requests, product suggestions and mobile push device tokens",
    "core": "Public website contact messages",
    "sessions": "Browser sessions",
    "auth": "Permissions and groups",
    "contenttypes": "Django content type registry",
    "admin": "Django admin log",
    "sites": "Django sites",
}
OTHER_KEY = "_other"


def _table_owners():
    """{table name: (app_label, model name)} for every table Django manages,
    including the link tables behind many-to-many fields."""
    owners = {}
    for model in apps.get_models(include_auto_created=True):
        owners.setdefault(model._meta.db_table, (model._meta.app_label, model._meta.object_name))
    return owners


def _table_stats(conn):
    """One row per user table in the current schema: name, total bytes (table,
    indexes and TOAST), table bytes, index bytes, estimated live rows."""
    sql = """
        SELECT c.relname,
               pg_total_relation_size(c.oid),
               pg_relation_size(c.oid),
               pg_indexes_size(c.oid),
               COALESCE(s.n_live_tup, 0)
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        LEFT JOIN pg_stat_user_tables s ON s.relid = c.oid
        WHERE c.relkind IN ('r', 'p') AND n.nspname = current_schema()
    """
    with conn.cursor() as cur:
        cur.execute(sql)
        return [
            {"name": name, "total": int(total), "table": int(table), "indexes": int(indexes), "rows": int(rows)}
            for name, total, table, indexes, rows in cur.fetchall()
        ]


def _database_bytes(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT pg_database_size(current_database())")
        return int(cur.fetchone()[0])


def _share(part, whole):
    return round(part * 100 / whole, 1) if whole else 0


def database_usage(env):
    """Everything the usage page shows for one environment's database."""
    conn = connections[env]
    owners = _table_owners()
    tables = _table_stats(conn)
    database_total = _database_bytes(conn)
    tables_total = sum(t["total"] for t in tables)

    groups = defaultdict(lambda: {"tables": [], "total": 0, "table": 0, "indexes": 0, "rows": 0})
    for t in tables:
        app_label, model_name = owners.get(t["name"], (OTHER_KEY, ""))
        group = groups[app_label]
        t["model"] = model_name
        group["tables"].append(t)
        for key in ("total", "table", "indexes", "rows"):
            group[key] += t[key]

    rows = []
    for app_label, group in groups.items():
        if app_label == OTHER_KEY:
            name, purpose = "Other tables", "Tables no model in the project owns, such as migration history"
        else:
            config = apps.get_app_config(app_label)
            name, purpose = config.verbose_name, APP_PURPOSE.get(app_label, "")
        group["tables"].sort(key=lambda t: t["total"], reverse=True)
        group["share"] = _share(group["total"], tables_total)
        rows.append({"key": app_label, "name": name, "purpose": purpose, **group})
    rows.sort(key=lambda r: r["total"], reverse=True)

    clients = []
    if conn.vendor == "postgresql":
        clients_bytes = db_bytes_for(env, Organization.objects.using(env).filter(
            storage_mode=Organization.StorageMode.OUR_DATABASE).values_list("id", flat=True))
        names = dict(Organization.objects.using(env).values_list("id", "name"))
        client_total = sum(clients_bytes.values())
        clients = [
            {"name": names.get(org_id, "Unknown client"), "bytes": size, "share": _share(size, client_total)}
            for org_id, size in sorted(clients_bytes.items(), key=lambda kv: kv[1], reverse=True)
        ]
    else:
        client_total = 0

    capacity = int(settings.DATABASE_CAPACITY_GB.get(env, 0) * 1024**3)
    used_pct = _share(database_total, capacity)
    return {
        "capacity_bytes": capacity,
        "used_pct": min(used_pct, 100),
        "remaining_bytes": max(capacity - database_total, 0),
        "over_capacity": database_total > capacity,
        "database_total": database_total,
        "tables_total": tables_total,
        "table_count": len(tables),
        "row_estimate": sum(t["rows"] for t in tables),
        "apps": rows,
        "clients": clients,
        "client_total": client_total,
        "largest_table": max(tables, key=lambda t: t["total"], default=None),
    }


class DatabaseUsageView(SuperAdminRequiredMixin, TemplateView):
    template_name = "superadmin/database_usage.html"

    def get_context_data(self, **kwargs):
        env = kwargs["env"]
        context = super().get_context_data(**kwargs)
        context["env_key"] = env
        context["env_label"] = _env_label_or_404(env)
        context["db_name"] = connections[env].settings_dict.get("NAME", "")
        try:
            context.update(database_usage(env))
        except DatabaseError:
            logger.exception("Database usage: %s read failed", env)
            context["error"] = "The database could not be read. Check System health for details."
        return context
