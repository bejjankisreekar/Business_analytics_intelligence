"""Builds the full-database Excel export: one workbook, one sheet per
apps.finance table — the "Export as Excel" download
(apps.accounts.views.ExportFinanceDataExcelView).

Must be called from inside a request already routed to the right
organization's Google Sheet (TenantSchemaMiddleware) — this just queries
apps.finance models as-is, it does no routing of its own.
"""
from __future__ import annotations

import datetime
import decimal
import io
import re
import uuid

import openpyxl
from django.apps import apps as django_apps
from openpyxl.styles import Font

# Characters Excel forbids in a sheet name, e.g. CashTransfer's verbose
# name "Cash/bank transfer" — openpyxl raises on any of these.
_INVALID_SHEET_CHARS = re.compile(r"[\\/?*\[\]:]")


def _sheet_name(model) -> str:
    raw = model._meta.verbose_name_plural.title() or model.__name__
    return _INVALID_SHEET_CHARS.sub("-", raw)[:31]


def _cell_value(value):
    """Coerces a Django field value into something openpyxl can write —
    it only accepts str/int/float/bool/date-ish/None, so UUID and
    Decimal (both common here: every pk and every money field) need
    converting explicitly."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, decimal.Decimal):
        return float(value)
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime.datetime):
        # Excel has no concept of timezone-aware datetimes — write the
        # local wall-clock time, same as everywhere else in this app
        # (USE_TZ=True stores UTC, but every report already localizes).
        from django.utils import timezone as dj_timezone

        if dj_timezone.is_aware(value):
            value = dj_timezone.localtime(value)
        return value.replace(tzinfo=None)
    if isinstance(value, datetime.date):
        return value
    return str(value)


def build_finance_excel_bytes() -> bytes:
    workbook = openpyxl.Workbook()
    workbook.remove(workbook.active)
    from apps.sheets_store.store import sheet_fields

    for model in django_apps.get_app_config("finance").get_models():
        # Excludes `organization` — meaningless noise in a one-org
        # export (blank for a GOOGLE_SHEETS org, since it's never set
        # on an instance read from a Sheet; just the same repeated
        # value for an OUR_DATABASE org).
        fields = sheet_fields(model)
        sheet = workbook.create_sheet(title=_sheet_name(model))
        sheet.append([field.verbose_name.title() for field in fields])
        for cell in sheet[1]:
            cell.font = Font(bold=True)
        for obj in model.objects.all().order_by("pk"):
            sheet.append([_cell_value(getattr(obj, field.attname)) for field in fields])

    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()
