"""Data-access surface over the active SheetSession for apps.finance
models — services.py and views.py call this instead of Model.objects.*
once ported (see the Phase 2/3/4 plan). Row identity is the model's
existing UUID pk (always column A); column order on every tab is fixed
as model._meta.fields order — the store never parses the header text
for meaning, that row exists purely for the client's own convenience
reading the Sheet by eye.

Must be called with a SheetSession active (see session.set_active_session,
wired into TenantSchemaMiddleware for every org).
"""
from __future__ import annotations

import functools
import uuid

from django.db.models import F
from django.http import Http404
from django.utils import timezone

from . import fields as field_codec
from .session import get_active_session


def _tab_name(model) -> str:
    return model.__name__


def all_sheet_backed_tabs() -> list[str]:
    """Every model's tab name, across every installed app, that reads and
    writes through a SheetSession (i.e. uses SheetAwareManager) — the full
    set TenantSchemaMiddleware prefetches in one Sheets API call when a
    GOOGLE_SHEETS org's session opens, instead of each page discovering
    (and separately, sequentially fetching) its own subset one tab at a
    time."""
    from django.apps import apps as django_apps

    from .queryset import SheetAwareManager

    return [
        model.__name__
        for model in django_apps.get_models()
        if isinstance(getattr(model, "objects", None), SheetAwareManager)
    ]


def sheet_fields(model) -> list:
    """Every column a tab actually has. Deliberately excludes
    `organization` — that column only exists to isolate rows sharing
    one Postgres table (StorageMode.OUR_DATABASE); a GOOGLE_SHEETS org's
    isolation is just "which spreadsheet", so writing it there would be
    meaningless noise at best, and at worst would silently misalign
    every column after it for a Sheet provisioned before this field
    existed on the model (exactly what happened the first time this
    went out without the exclusion)."""
    return [f for f in model._meta.fields if f.name != "organization"]


def _row_to_instance(model, row: list):
    kwargs = {}
    for i, field in enumerate(sheet_fields(model)):
        cell = row[i] if i < len(row) else ""
        kwargs[field.attname] = field_codec.from_cell(field, cell)
    instance = model(**kwargs)
    # The plain Model(**kwargs) constructor always marks a fresh
    # instance as "adding" (never saved yet) — wrong here, since this
    # is always reconstructing a row that already exists in the Sheet.
    # Left at the default, every edit's uniqueness check (which skips
    # excluding the instance's own pk while adding) would see the row
    # as colliding with itself, and RequireCategoryMixin-style "blank
    # on an existing row is OK" checks would never apply either.
    instance._state.adding = False
    return instance


def _instance_to_row(instance) -> list:
    return [field_codec.to_cell(field, getattr(instance, field.attname)) for field in sheet_fields(type(instance))]


def _all_rows(model):
    """Yields (row_number, instance) for every data row of `model`'s
    tab, 1-indexed as seen in the sheet (header=1, first data row=2).

    Decoded once per tab per request and cached on the session (cleared
    on any write to that tab): every `Model.objects` access builds a
    fresh SheetAwareQuerySet, so without this a page calling several
    service functions against the same model would redecode the whole
    tab from scratch each time."""
    session = get_active_session()
    tab = _tab_name(model)
    cached = session.get_decoded(tab)
    if cached is None:
        cached = [
            (offset + 2, _row_to_instance(model, row))
            for offset, row in enumerate(session.data_rows(tab))
            if any(cell != "" for cell in row)  # skip a wholly-blank trailing row, if any
        ]
        session.set_decoded(tab, cached)
    yield from cached


def all(model) -> list:
    return [instance for _, instance in _all_rows(model)]


def _resolve(instance, value):
    """Resolves a django.db.models.F("field") against `instance` —
    the one shape apps.finance actually uses (comparing two columns of
    the same row, e.g. `exclude(amount_received__gte=F("amount"))`).
    Any other value passes through unchanged."""
    if isinstance(value, F):
        return getattr(instance, value.name, None)
    return value


@functools.lru_cache(maxsize=None)
def _field_for(model, field_name):
    """Memoized: a model's fields are fixed for the process lifetime, but
    this runs once per lookup key per row checked — on a Sheets-mode org
    with thousands of rows and `.filter()` calls that have no index to
    fall back on, the linear scan over model._meta.fields it used to do
    every single time was itself a measurable chunk of every page load."""
    if field_name == "pk":
        return model._meta.pk
    for f in model._meta.fields:
        if f.name == field_name or f.attname == field_name:
            return f
    return None


def _coerce(field, value):
    """A real QuerySet lookup coerces its right-hand side through the
    target field (e.g. the SQL driver turns "1500" into a numeric
    bind for a DecimalField) before comparing — nothing here does
    that automatically, so a raw string straight from a submitted
    form (every ModelChoiceField.to_python ultimately calls
    `queryset.get(pk=<that string>)`) would otherwise never match a
    row's real UUID/Decimal/date value. field.to_python() is a safe
    no-op for a field whose value is already the right type (e.g.
    CharField) or already a string.
    """
    if field is None:
        return value
    if isinstance(value, str):
        try:
            return field.to_python(value)
        except Exception:
            return value
    if isinstance(value, (list, tuple, set)):
        return type(value)(_coerce(field, v) for v in value)
    return value


def _matches(instance, lookups: dict) -> bool:
    for key, raw_expected in lookups.items():
        field_name, _, op = key.partition("__")
        op = op or "exact"
        actual = getattr(instance, field_name, None)
        expected = _coerce(_field_for(type(instance), field_name), _resolve(instance, raw_expected))
        if op == "exact" and actual != expected:
            return False
        if op == "gte" and not (actual is not None and actual >= expected):
            return False
        if op == "lte" and not (actual is not None and actual <= expected):
            return False
        if op == "gt" and not (actual is not None and actual > expected):
            return False
        if op == "lt" and not (actual is not None and actual < expected):
            return False
        if op == "in" and actual not in expected:
            return False
        if op == "isnull" and (actual is None) != expected:
            return False
    return True


def filter(model, **lookups) -> list:
    """Supports exact/`__gte`/`__lte`/`__gt`/`__lt`/`__in`/`__isnull`
    against this model's own fields (including an FK's id field, e.g.
    `category_id=`). One-hop FK *name* traversal (`category__kind=`)
    and the two-hop reverse-FK report filters are intentionally not
    generic here — see store_special.py, built per the exact two call
    sites that need them (apps/finance/services.py's GST summary and
    active-subcategories-under-kind filters)."""
    return [instance for instance in all(model) if _matches(instance, lookups)]


def get(model, pk):
    if pk is None:
        return None
    pk = pk if isinstance(pk, uuid.UUID) else uuid.UUID(str(pk))
    for instance in all(model):
        if instance.pk == pk:
            return instance
    return None


def get_or_404(model, pk):
    instance = get(model, pk)
    if instance is None:
        raise Http404(f"{model.__name__} not found.")
    return instance


def exists(model, **lookups) -> bool:
    return bool(filter(model, **lookups)) if lookups else bool(all(model))


def _apply_auto_fields(instance, *, is_create: bool) -> None:
    """A real Model.save() populates auto_now/auto_now_add fields via
    each field's pre_save() as part of the INSERT/UPDATE — bypassed
    entirely here since nothing goes through the real QuerySet/SQL
    compiler, so it must be done by hand."""
    now = timezone.now()
    for field in instance._meta.fields:
        if getattr(field, "auto_now", False) or (is_create and getattr(field, "auto_now_add", False)):
            setattr(instance, field.attname, now)


def create(model, **field_values):
    instance = model(**field_values)
    # Routes through instance.save() rather than appending the row
    # directly, so a model's own save() override (e.g. SalesEntry's
    # gross_amount/discount -> amount derivation) runs here exactly as
    # it would for a real QuerySet.create() against Postgres — save()
    # itself ends up back in SheetAwareModelMixin.save(), which appends
    # via save() below since this pk doesn't exist in the tab yet.
    instance.save()
    return instance


def bulk_create(model, instances: list) -> list:
    """Appends every instance in `instances` to model's tab with ONE
    Sheets API call, instead of one call per row (see save() above) —
    the difference between a handful of requests and thousands for a
    genuinely bulk write (an import, a demo-data seed). Every instance
    is treated as brand new: no existing-pk lookup/update, unlike
    save()."""
    if not instances:
        return instances
    for instance in instances:
        _apply_auto_fields(instance, is_create=True)
    session = get_active_session()
    session.append_rows(_tab_name(model), [_instance_to_row(instance) for instance in instances])
    return instances


def save(instance) -> None:
    """Mirrors Model.save()'s upsert-by-pk behavior: updates the
    existing row if one has this pk, otherwise appends a new one."""
    model = type(instance)
    session = get_active_session()
    tab = _tab_name(model)
    for row_number, existing in _all_rows(model):
        if existing.pk == instance.pk:
            _apply_auto_fields(instance, is_create=False)
            session.update_row(tab, row_number, _instance_to_row(instance))
            return
    _apply_auto_fields(instance, is_create=True)
    session.append_row(tab, _instance_to_row(instance))


def delete(instance) -> None:
    model = type(instance)
    session = get_active_session()
    tab = _tab_name(model)
    for row_number, existing in _all_rows(model):
        if existing.pk == instance.pk:
            session.delete_row(tab, row_number)
            return
