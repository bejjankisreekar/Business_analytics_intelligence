"""Switches an already-live org between StorageMode.OUR_DATABASE and
StorageMode.GOOGLE_SHEETS, copying every row across so nothing is lost.
Both directions preserve each row's own UUID pk and FK ids exactly —
every apps.finance model already uses a UUID assigned in Python, so
copying is a straight row-for-row transplant, not a remap.

The engine being migrated away from is never deleted:
- OUR_DATABASE -> GOOGLE_SHEETS leaves the Postgres rows in place,
  orphaned (no longer read, since nothing routes to them for this org
  once storage_mode flips) but not dropped.
- GOOGLE_SHEETS -> OUR_DATABASE leaves the Google Sheet itself exactly
  as it was — only our own CloudBackupConnection pointer to it is
  removed (see apps.accounts.views.SwitchToOurDatabaseView, which does
  that disconnect after calling the function below).
"""
from __future__ import annotations

from django.apps import apps as django_apps

from .tenant_context import set_current_organization


class StorageMigrationError(RuntimeError):
    pass


def _finance_models() -> list:
    return list(django_apps.get_app_config("finance").get_models())


BULK_BATCH_SIZE = 500


def _bulk_write(model, instances, *, organization=None) -> None:
    """Writes `instances` in batches via bulk_create — one Sheets API call per
    batch (not per row, which trips Google's 60-writes-per-minute quota) or
    one INSERT per batch on the Postgres side. Rows are copied as-is, so
    model save() overrides don't re-derive anything."""
    for start in range(0, len(instances), BULK_BATCH_SIZE):
        batch = instances[start:start + BULK_BATCH_SIZE]
        if organization is not None:
            for instance in batch:
                instance.organization = organization
        model.objects.bulk_create(batch)


def _write_ordered(model, rows, *, organization=None) -> None:
    """Writes `rows` (already-loaded instances) to whatever write target
    is currently active (a SheetSession, or the "current organization"
    Postgres context). Subcategory's self-referencing `parent` needs
    parent-before-child order so a real Postgres FK constraint never
    rejects an insert; every other model has no such ordering requirement."""
    if model.__name__ != "Subcategory":
        _bulk_write(model, list(rows), organization=organization)
        return

    remaining = list(rows)
    inserted_ids: set = set()
    while remaining:
        ready = [r for r in remaining if r.parent_id is None or r.parent_id in inserted_ids]
        if not ready:
            raise StorageMigrationError("Subcategory data has a broken parent reference — cannot migrate safely.")
        _bulk_write(model, ready, organization=organization)
        inserted_ids.update(r.pk for r in ready)
        remaining = [r for r in remaining if r.pk not in inserted_ids]


def migrate_our_database_to_google_sheets(org, *, access_token: str, folder_id: str) -> str:
    """OUR_DATABASE -> GOOGLE_SHEETS. Creates the org's new Sheet,
    pre-filled with every row currently in our Postgres tables for this
    org (instead of the blank-default seed a brand-new signup gets).
    Returns the new spreadsheet id."""
    from apps.sheets_store import client, store
    from apps.sheets_store.session import SheetSession, set_active_session

    models = _finance_models()

    set_current_organization(org)
    try:
        data = {model: list(model.objects.all()) for model in models}
    finally:
        set_current_organization(None)

    spreadsheet_id = client.create_spreadsheet(
        access_token, title=f"{org.organization_code} Data",
        tab_names=[model.__name__ for model in models], folder_id=folder_id,
    )
    header_data = [
        {"range": f"{model.__name__}!A1", "values": [[f.verbose_name.title() for f in store.sheet_fields(model)]]}
        for model in models
    ]
    client.batch_update_values(access_token, spreadsheet_id=spreadsheet_id, data=header_data)

    set_active_session(SheetSession(access_token, spreadsheet_id))
    try:
        for model in models:
            _write_ordered(model, data[model])
    finally:
        set_active_session(None)

    return spreadsheet_id


def migrate_google_sheets_to_our_database(org) -> None:
    """GOOGLE_SHEETS -> OUR_DATABASE. Copies every row currently in the
    org's connected Google Sheet into our Postgres tables, tagged with
    this organization. The Sheet itself is left exactly as it was —
    the caller is responsible for disconnecting afterward."""
    from apps.organizations.drive_sync import get_valid_access_token
    from apps.sheets_store.session import SheetSession, set_active_session

    connection = org.cloud_backup
    access_token = get_valid_access_token(connection)
    models = _finance_models()

    set_active_session(SheetSession(access_token, connection.external_file_id))
    try:
        data = {model: list(model.objects.all()) for model in models}
    finally:
        set_active_session(None)

    set_current_organization(org)
    try:
        for model in models:
            _write_ordered(model, data[model], organization=org)
    finally:
        set_current_organization(None)
