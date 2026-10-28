"""Cell <-> Python coercion for the Sheets store, keyed off each Django
field's actual type — so a row read back from a tab reconstructs
exactly the value that was written, with no parsing ambiguity.

This relies on every write going through client.py with
valueInputOption=RAW: Sheets stores exactly the string/number/boolean
sent, with no autodetection or locale reformatting, so a read is a
pure inverse of the matching write — money and dates round-trip through
explicit ISO/decimal strings rather than Sheets' own (locale-dependent,
float-precision-losing) number/date formatting.
"""
from __future__ import annotations

import datetime
import decimal
import uuid

from django.db import models


def to_cell(field, value):
    """A model field's Python value -> one JSON-serializable cell value
    (str / int / float / bool / "") ready for the Sheets API."""
    if value is None or value == "":
        return ""
    if isinstance(field, models.DecimalField):
        return str(value)
    if isinstance(field, models.DateTimeField):
        if isinstance(value, datetime.datetime):
            from django.utils import timezone as dj_timezone

            if dj_timezone.is_aware(value):
                value = dj_timezone.localtime(value)
            return value.replace(tzinfo=None).isoformat()
        return str(value)
    if isinstance(field, models.DateField):
        return value.isoformat() if isinstance(value, datetime.date) else str(value)
    if isinstance(field, models.UUIDField):
        return str(value)
    if isinstance(field, models.ForeignKey):
        return str(value) if value else ""
    if isinstance(field, models.BooleanField):
        return bool(value)
    if isinstance(field, (models.IntegerField, models.PositiveIntegerField, models.PositiveSmallIntegerField)):
        return int(value)
    return str(value)


_SERIAL_EPOCH = datetime.datetime(1899, 12, 30)


def _is_serial(cell) -> bool:
    if isinstance(cell, bool):
        return False
    if isinstance(cell, (int, float)):
        return True
    try:
        float(str(cell))
    except ValueError:
        return False
    return True


def _normalize_datetime_text(text: str) -> str:
    """Tolerates a time typed with dots ('2026-10-28 16.27.57'), which some
    locales produce when a cell is edited by hand in Sheets."""
    text = text.strip()
    date_part, sep, time_part = text.partition(" ")
    if sep and "." in time_part and ":" not in time_part:
        head, _, frac = time_part.rpartition(".") if time_part.count(".") == 3 else (time_part, "", "")
        time_part = (head.replace(".", ":") + "." + frac) if frac else time_part.replace(".", ":")
    return f"{date_part}{sep}{time_part}"


def from_cell(field, cell):
    """A raw cell value read back from the Sheets API -> the correctly
    typed Python value for `field`. An empty cell becomes whatever
    Django's own Field.get_default() says "no value" means for that
    field's type — None for a nullable field (e.g. every optional FK
    here), but "" for a non-nullable CharField/TextField (e.g. `note`),
    which rejects a real NULL at the database level."""
    if cell is None or cell == "":
        return field.get_default()
    if isinstance(field, models.DecimalField):
        return decimal.Decimal(str(cell))
    if isinstance(field, models.DateTimeField):
        if _is_serial(cell):
            return _SERIAL_EPOCH + datetime.timedelta(days=float(cell))
        return datetime.datetime.fromisoformat(_normalize_datetime_text(str(cell)))
    if isinstance(field, models.DateField):
        # A cell edited by hand in Google Sheets becomes a real date cell,
        # which UNFORMATTED_VALUE returns as a serial number, not ISO text.
        if _is_serial(cell):
            return (_SERIAL_EPOCH + datetime.timedelta(days=float(cell))).date()
        return datetime.date.fromisoformat(str(cell))
    if isinstance(field, (models.UUIDField, models.ForeignKey)):
        return uuid.UUID(str(cell))
    if isinstance(field, models.BooleanField):
        return bool(cell)
    if isinstance(field, (models.IntegerField, models.PositiveIntegerField, models.PositiveSmallIntegerField)):
        return int(cell)
    return str(cell)
