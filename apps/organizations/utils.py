"""Schema-per-tenant helpers.

Every organization gets its own PostgreSQL schema, named from a random
per-organization code (e.g. organization_code "A1F93B2C" -> schema
"org_a1f93b2c"). The `public` schema stays shared: it only ever holds
Organization + User (and anything else account/billing related). Once
operational models (daily sales, expenses, etc.) are added, they get
cloned into each tenant schema the same way HRMS clones its tenant
tables — this module is where that cloning would plug in.
"""
import re
import secrets
from contextlib import contextmanager

from django.db import connections

try:
    from psycopg2 import sql  # type: ignore
except Exception:  # pragma: no cover
    sql = None


SCHEMA_RE = re.compile(r"^[a-z0-9_]{1,63}$")
SCHEMA_PREFIX = "org_"


class TenantSchemaError(RuntimeError):
    pass


def generate_organization_code() -> str:
    """8-char uppercase hex code, e.g. 'A1F93B2C'."""
    return secrets.token_hex(4).upper()


def normalize_schema_name(organization_code: str) -> str:
    schema = f"{SCHEMA_PREFIX}{(organization_code or '').strip().lower()}"
    if not SCHEMA_RE.match(schema):
        raise TenantSchemaError("Invalid schema name generated.")
    return schema


def schema_exists(schema_name: str, using: str = "default") -> bool:
    with connections[using].cursor() as cursor:
        cursor.execute(
            "SELECT 1 FROM information_schema.schemata WHERE schema_name = %s",
            [schema_name],
        )
        return cursor.fetchone() is not None


def create_tenant_schema(schema_name: str, using: str = "default") -> None:
    if not SCHEMA_RE.match(schema_name):
        raise TenantSchemaError("Invalid schema name.")
    with connections[using].cursor() as cursor:
        if sql is not None:
            cursor.execute(
                sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(schema_name))
            )
        else:
            cursor.execute(f"CREATE SCHEMA IF NOT EXISTS {schema_name}")


def drop_tenant_schema(schema_name: str, using: str = "default") -> None:
    if not SCHEMA_RE.match(schema_name):
        raise TenantSchemaError("Invalid schema name.")
    if schema_name == "public":
        raise TenantSchemaError("Cannot drop public schema.")
    with connections[using].cursor() as cursor:
        if sql is not None:
            cursor.execute(
                sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema_name))
            )
        else:
            cursor.execute(f"DROP SCHEMA IF EXISTS {schema_name} CASCADE")


def set_schema_search_path(schema_name: str | None, using: str = "default") -> None:
    """Point this connection's search_path at `schema_name` (falling back to
    `public` for unqualified table lookups), or reset to `public` alone when
    `schema_name` is falsy. This is what makes the same finance.models
    queries transparently read/write a different organization's tables.
    """
    with connections[using].cursor() as cursor:
        if schema_name:
            if not SCHEMA_RE.match(schema_name):
                raise TenantSchemaError("Invalid schema name.")
            if sql is not None:
                cursor.execute(
                    sql.SQL("SET search_path TO {}, public").format(sql.Identifier(schema_name))
                )
            else:
                cursor.execute(f"SET search_path TO {schema_name}, public")
        else:
            cursor.execute("SET search_path TO public")


@contextmanager
def schema_context(schema_name: str, using: str = "default"):
    """Run a block of ORM code against `schema_name`'s tenant schema, then
    restore the connection to `public`."""
    set_schema_search_path(schema_name, using=using)
    try:
        yield
    finally:
        set_schema_search_path(None, using=using)
