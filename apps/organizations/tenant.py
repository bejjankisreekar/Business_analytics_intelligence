"""Provisioning for the tenant (schema-per-organization) data layer.

`apps.finance` models are migrated once into `public` (the template),
then this module clones their tables into every organization's own
schema and seeds sane defaults there. Nothing here runs on a normal
request — it only runs at signup and from the `provision_tenants`
management command.
"""
from __future__ import annotations

import datetime

from django.apps import apps as django_apps
from django.db import connections

from .utils import TenantSchemaError, create_tenant_schema, schema_context

try:
    from psycopg2 import sql  # type: ignore
except Exception:  # pragma: no cover
    sql = None

TENANT_APP_LABEL = "finance"

DEFAULT_EXPENSE_CATEGORIES = [
    "Rent", "Salaries & Wages", "Utilities", "Marketing", "Logistics & Delivery",
    "Maintenance", "Taxes", "Bank & Payment Charges", "Miscellaneous",
]
DEFAULT_PURCHASE_CATEGORIES = ["Raw Materials", "Inventory / Stock", "Packaging", "Equipment", "Other"]
DEFAULT_SALES_CHANNELS = ["In-store", "Online", "Wholesale", "Other"]


def tenant_models() -> list:
    return list(django_apps.get_app_config(TENANT_APP_LABEL).get_models())


def clone_tenant_tables(schema_name: str, using: str = "default") -> list[str]:
    """Create empty copies of every finance table inside `schema_name`."""
    create_tenant_schema(schema_name, using=using)
    cloned = []
    with connections[using].cursor() as cursor:
        for model in tenant_models():
            table = model._meta.db_table
            if sql is not None:
                cursor.execute(
                    sql.SQL("CREATE TABLE IF NOT EXISTS {}.{} (LIKE public.{} INCLUDING ALL)").format(
                        sql.Identifier(schema_name), sql.Identifier(table), sql.Identifier(table)
                    )
                )
            else:
                cursor.execute(
                    f'CREATE TABLE IF NOT EXISTS "{schema_name}"."{table}" '
                    f'(LIKE "public"."{table}" INCLUDING ALL)'
                )
            cloned.append(table)
    return cloned


def _column_default_literal(field):
    """A SQL literal for `field`'s Django default, or None if it has none
    usable at the DB level (new NOT NULL columns without one are added
    NULLable instead, so a sync never breaks an existing tenant)."""
    if not field.has_default():
        return None
    default = field.get_default()
    if isinstance(default, bool):
        return "TRUE" if default else "FALSE"
    if isinstance(default, (int, float)):
        return str(default)
    from decimal import Decimal as _Decimal
    if isinstance(default, _Decimal):
        return str(default)
    if isinstance(default, str):
        return "'%s'" % default.replace("'", "''")
    return None


def sync_tenant_schema(schema_name: str, using: str = "default") -> list[str]:
    """Bring an already-provisioned tenant schema up to date with the
    current `apps.finance` models: create any table that doesn't exist yet
    (a whole new model), and add any column that doesn't exist yet on an
    existing table (a field added to an existing model). Safe to run
    repeatedly — every statement is additive and IF NOT EXISTS-guarded.
    """
    connection = connections[using]
    create_tenant_schema(schema_name, using=using)
    changes = []
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = %s",
            [schema_name],
        )
        existing_tables = {row[0] for row in cursor.fetchall()}

        for model in tenant_models():
            table = model._meta.db_table
            if table not in existing_tables:
                cursor.execute(
                    sql.SQL("CREATE TABLE {}.{} (LIKE public.{} INCLUDING ALL)").format(
                        sql.Identifier(schema_name), sql.Identifier(table), sql.Identifier(table)
                    )
                )
                changes.append(f"created table {table}")
                continue

            cursor.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = %s AND table_name = %s",
                [schema_name, table],
            )
            existing_columns = {row[0] for row in cursor.fetchall()}

            for field in model._meta.fields:
                column = field.column
                if column in existing_columns:
                    continue
                col_type = field.db_type(connection)
                default_literal = _column_default_literal(field)
                stmt = sql.SQL("ALTER TABLE {}.{} ADD COLUMN {} {}").format(
                    sql.Identifier(schema_name), sql.Identifier(table),
                    sql.Identifier(column), sql.SQL(col_type),
                )
                if default_literal is not None:
                    stmt += sql.SQL(" NOT NULL DEFAULT {}").format(sql.SQL(default_literal))
                cursor.execute(stmt)
                changes.append(f"added column {table}.{column}")
    return changes


def seed_tenant_defaults(
    schema_name: str, *, using: str = "default", fy_start_month: int = 4, opening_balance=0
) -> None:
    """Seed default categories + finance settings inside `schema_name`."""
    from apps.finance.models import Category, FinanceSettings

    with schema_context(schema_name, using=using):
        if not FinanceSettings.objects.using(using).exists():
            FinanceSettings.objects.using(using).create(
                fy_start_month=fy_start_month,
                opening_balance=opening_balance,
                opening_date=datetime.date.today(),
            )
        if not Category.objects.using(using).exists():
            Category.objects.using(using).bulk_create(
                [Category(kind=Category.Kind.EXPENSE, name=name) for name in DEFAULT_EXPENSE_CATEGORIES]
                + [Category(kind=Category.Kind.PURCHASE, name=name) for name in DEFAULT_PURCHASE_CATEGORIES]
                + [Category(kind=Category.Kind.SALES, name=name) for name in DEFAULT_SALES_CHANNELS]
            )


def provision_tenant_schema(org, *, using: str = "default", fy_start_month: int = 4, opening_balance=0) -> None:
    """Full provision for one organization: clone tables + seed defaults."""
    if not org.schema_name:
        raise TenantSchemaError(f"Organization {org.organization_code} has no schema_name.")
    clone_tenant_tables(org.schema_name, using=using)
    seed_tenant_defaults(
        org.schema_name, using=using, fy_start_month=fy_start_month, opening_balance=opening_balance
    )
