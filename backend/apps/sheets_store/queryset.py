"""A minimal, list-backed stand-in for Django's QuerySet/Manager, used
only when a SheetSession is active — implements just the subset of the
QuerySet API apps.finance actually calls (per the project plan's full
ORM catalog), backed by apps.sheets_store.store.

The point of this file: apps.finance's existing views/services/forms
call `Model.objects.filter(...)`, `get_object_or_404(Model, pk=pk)`,
`instance.save()`, `category.subcategories.all()` etc. completely
unchanged. SheetAwareManager.get_queryset() returns a real Django
QuerySet when no sheets session is active and this class otherwise.
FK access (`entry.category.name`) needs no special
handling at all: Django's own ForeignKey descriptor calls
`RelatedModel.objects.get(pk=...)`, which already routes through this
same manager once every apps.finance model uses it.

What this deliberately does NOT attempt to generically support:
arbitrary multi-hop FK-*name* filters (`subcategories__sales_entries__
date__gte=`, two sites in services.py — ported by hand to store.py
helpers instead) or annotate() expressions beyond TruncMonth. Q()
objects ARE supported generically (by walking their real .children/
.connector/.negated structure, not a reimplementation) since Django's
Q is a plain, stable tree the one real call site (GST summary's
untaxed-OR-zero-rate filter) already builds unmodified.
"""
from __future__ import annotations

import datetime
from decimal import Decimal

from django.db import models

from . import store
from .session import get_active_session


def _sort_key(value):
    """A value that sorts consistently even when some rows have None —
    Python 3 raises on None-vs-real-value comparisons otherwise."""
    return (value is None, value)


def _q_matches(q, row) -> bool:
    """Evaluates a real django.db.models.Q tree against one in-memory
    row, by walking its public-ish .children/.connector/.negated
    structure — not a reimplementation of Q, just an interpreter for
    the plain tree it already is."""
    results = []
    for child in q.children:
        if hasattr(child, "children"):  # a nested Q
            results.append(_q_matches(child, row))
        else:
            key, value = child
            results.append(store._matches(row, {key: value}))
    result = all(results) if q.connector == "AND" else any(results) if results else False
    return not result if q.negated else result


class SheetAwareManager(models.Manager):
    """Drop-in replacement for `objects = models.Manager()` on an
    apps.finance model. Three cases:

    - A SheetSession is active (StorageMode.GOOGLE_SHEETS org): swaps in
      the list-backed SheetAwareQuerySet below, reading/writing that
      org's Sheet.
    - No session, but a "current organization" is set (StorageMode.
      OUR_DATABASE org — see apps.organizations.tenant_context, set by
      TenantSchemaMiddleware): Django's own real QuerySet, transparently
      filtered to that organization's rows only.
    - Neither (no request context at all, e.g. a one-off shell/script):
      Django's own real QuerySet, completely unfiltered — the caller's
      own responsibility to scope it, same as any plain Django model.
    """

    def get_queryset(self):
        if get_active_session() is not None:
            return SheetAwareQuerySet(self.model)
        qs = super().get_queryset()
        from apps.organizations.tenant_context import get_current_organization

        org = get_current_organization()
        if org is not None:
            qs = qs.filter(organization=org)
        return qs

    def bulk_create(self, objs, batch_size=None, **kwargs):
        # Django's real QuerySet.bulk_create() goes straight to a bulk
        # INSERT, bypassing instance.save() entirely — so neither the
        # Sheets-store branch nor the auto org-tagging in
        # SheetAwareModelMixin.save() would ever run for it. Both need
        # doing by hand here, before delegating to whichever get_queryset()
        # would have returned.
        if get_active_session() is not None:
            return store.bulk_create(self.model, list(objs))
        from apps.organizations.tenant_context import get_current_organization

        org = get_current_organization()
        if org is not None:
            for obj in objs:
                if not obj.organization_id:
                    obj.organization = org
        return super().bulk_create(objs, batch_size=batch_size, **kwargs)


class SheetAwareQuerySet:
    def __init__(self, model, rows=None, extra_annotations=None, ordered=None):
        self.model = model
        self._rows = store.all(model) if rows is None else rows
        self._extra = extra_annotations or {}
        self._ordered = ordered

    def _clone(self, rows, extra=None, ordered=None):
        return SheetAwareQuerySet(
            self.model, rows, extra if extra is not None else self._extra,
            ordered=self._ordered if ordered is None else ordered,
        )

    @property
    def ordered(self) -> bool:
        # Checked by BaseModelFormSet.get_queryset() — True once
        # .order_by(...) has actually been called, else whatever a real
        # QuerySet would default to (the model's own Meta.ordering).
        return bool(self._ordered) if self._ordered is not None else bool(self.model._meta.ordering)

    def _add_hints(self, **hints):
        # QuerySet._add_hints feeds the DB router with context (e.g. a
        # related instance) for picking an alias — meaningless once
        # already routed to a Sheet, so this is a no-op that just
        # satisfies callers (e.g. a reverse FK's related descriptor)
        # that unconditionally call it.
        return self

    def _row_value(self, row, field_name):
        if field_name in self._extra:
            return self._extra[field_name](row)
        if "__" in field_name:
            # FK-name traversal (e.g. "channel__name",
            # "subcategory__parent__name") — walk it one hop at a time
            # through FK-descriptor-resolved related objects, which
            # already route back through this same manager. This is
            # only for .values()/.annotate() group-by field names, not
            # filter()/exclude() lookups (those still need an operator
            # suffix parsed by store._matches).
            obj = row
            for part in field_name.split("__"):
                if obj is None:
                    return None
                obj = getattr(obj, part, None)
            return obj
        return getattr(row, field_name, None)

    def filter(self, *args, **kwargs):
        rows = self._rows
        for q in args:
            rows = [r for r in rows if _q_matches(q, r)]
        if kwargs:
            rows = [r for r in rows if store._matches(r, kwargs)]
        return self._clone(rows)

    def exclude(self, *args, **kwargs):
        rows = self._rows
        for q in args:
            rows = [r for r in rows if not _q_matches(q, r)]
        if kwargs:
            rows = [r for r in rows if not store._matches(r, kwargs)]
        return self._clone(rows)

    def get(self, *args, **kwargs):
        # Django's FK/relation descriptors call qs.get(Q(...)) with a
        # positional Q, not kwargs — route through filter() either way.
        matches = self.filter(*args, **kwargs)._rows if (args or kwargs) else self._rows
        if len(matches) == 0:
            raise self.model.DoesNotExist(f"{self.model.__name__} matching query does not exist.")
        if len(matches) > 1:
            raise self.model.MultipleObjectsReturned(
                f"get() returned more than one {self.model.__name__}."
            )
        return matches[0]

    def all(self):
        return self._clone(list(self._rows))

    def first(self):
        return self._rows[0] if self._rows else None

    def last(self):
        return self._rows[-1] if self._rows else None

    def exists(self):
        return bool(self._rows)

    def count(self):
        return len(self._rows)

    def order_by(self, *fields):
        rows = list(self._rows)
        for field in reversed(fields):
            reverse = field.startswith("-")
            key = field.lstrip("-")
            rows.sort(key=lambda r: _sort_key(self._row_value(r, key)), reverse=reverse)
        return self._clone(rows, ordered=bool(fields))

    def select_related(self, *args, **kwargs):
        return self  # no-op: FK access is already resolved live via this same manager

    def prefetch_related(self, *args, **kwargs):
        return self  # no-op: everything's already in memory for this request

    def distinct(self):
        seen = set()
        rows = []
        for r in self._rows:
            if r.pk not in seen:
                seen.add(r.pk)
                rows.append(r)
        return self._clone(rows)

    def annotate(self, **kwargs):
        new_extra = dict(self._extra)
        for name, expr in kwargs.items():
            new_extra[name] = _compile_row_expr(expr)
        return self._clone(self._rows, new_extra)

    def values(self, *fields):
        return SheetAwareValuesQuerySet(self, fields)

    def values_list(self, *fields, flat=False):
        if flat and len(fields) == 1:
            return [self._row_value(r, fields[0]) for r in self._rows]
        return [tuple(self._row_value(r, f) for f in fields) for r in self._rows]

    def aggregate(self, **kwargs):
        return _aggregate(self._rows, kwargs)

    def create(self, **kwargs):
        return store.create(self.model, **kwargs)

    def bulk_create(self, objs, batch_size=None, **kwargs):
        return store.bulk_create(self.model, list(objs))

    def update(self, **kwargs) -> int:
        count = 0
        for row in self._rows:
            for key, value in kwargs.items():
                setattr(row, key, value)
            store.save(row)
            count += 1
        return count

    def get_or_create(self, defaults=None, **kwargs):
        try:
            return self.get(**kwargs), False
        except self.model.DoesNotExist:
            create_kwargs = dict(kwargs)
            create_kwargs.update(defaults or {})
            return self.create(**create_kwargs), True

    def using(self, alias):
        return self  # alias selection is meaningless once routed to a Sheet

    def complex_filter(self, filter_obj):
        # Mirrors QuerySet.complex_filter: a plain dict of kwargs is
        # filter()'d as-is, a Q() is filter()'d via the same _q_matches
        # path filter() already uses for positional Q args. Called by
        # ForeignKey.validate() with get_limit_choices_to() — {} (no
        # limit_choices_to) for every apps.finance FK today, so this
        # just needs to be a correct no-op in that case, not only avoid
        # crashing.
        if isinstance(filter_obj, models.Q):
            return self.filter(filter_obj) if filter_obj.children else self.all()
        return self.filter(**filter_obj) if filter_obj else self.all()

    def iterator(self, chunk_size=None):
        return iter(list(self._rows))

    @property
    def _prefetch_related_lookups(self):
        # Checked (truthily) by ModelChoiceIterator before deciding
        # whether to call .iterator() — always empty here since
        # prefetch_related() is a no-op on this queryset (see above).
        return ()

    def __iter__(self):
        return iter(self._rows)

    def __len__(self):
        return len(self._rows)

    def __bool__(self):
        return bool(self._rows)

    def __getitem__(self, item):
        result = self._rows[item]
        return self._clone(result) if isinstance(item, slice) else result


def _compile_row_expr(expr):
    """Compiles the one per-row annotate() expression apps.finance
    actually uses — TruncMonth(date_field) — into row -> value."""
    from django.db.models.functions import TruncMonth

    if isinstance(expr, TruncMonth):
        source_field = expr.source_expressions[0].name

        def fn(row, _f=source_field):
            value = getattr(row, _f, None)
            return value.replace(day=1) if value else None

        return fn
    raise NotImplementedError(f"annotate() expression not supported by the Sheets store: {expr!r}")


def _aggregate_value(rows, field, agg):
    from django.db.models import Avg, Count, Sum

    values = [getattr(r, field, None) for r in rows]
    if isinstance(agg, Count):
        return len(rows)
    non_null = [v for v in values if v is not None]
    if isinstance(agg, Sum):
        if not non_null:
            return Decimal(0) if any(isinstance(v, Decimal) for v in values) or not values else 0
        total = non_null[0]
        for v in non_null[1:]:
            total = total + v
        return total
    if isinstance(agg, Avg):
        if not non_null:
            return None
        total = non_null[0]
        for v in non_null[1:]:
            total = total + v
        return total / len(non_null)
    raise NotImplementedError(f"aggregate not supported by the Sheets store: {agg!r}")


def _aggregate(rows, aggregates: dict) -> dict:
    out = {}
    for alias, agg in aggregates.items():
        field = agg.source_expressions[0].name if agg.source_expressions else "id"
        out[alias] = _aggregate_value(rows, field, agg)
    return out


class SheetAwareResultList(list):
    """What .values(...).annotate(...) returns — a plain list of dicts
    that also supports .order_by(...), matching the one real Django
    QuerySet chain shape apps.finance actually uses after annotate()."""

    def order_by(self, *fields):
        rows = list(self)
        for field in reversed(fields):
            reverse = field.startswith("-")
            key = field.lstrip("-")
            rows.sort(key=lambda r: _sort_key(r.get(key)), reverse=reverse)
        return SheetAwareResultList(rows)


class SheetAwareValuesQuerySet:
    """`qs.values(*group_fields)` — either iterated directly for a
    plain projection, or `.annotate(alias=Sum("field"))`'d into a
    group-by (the ~20 cataloged `.values().annotate(Sum/Count)` sites,
    including the two-stage TruncMonth variant where group_fields is
    the name of a prior .annotate() pseudo-field)."""

    def __init__(self, base_qs: SheetAwareQuerySet, group_fields):
        self.base_qs = base_qs
        self.group_fields = group_fields

    def annotate(self, **aggregates) -> SheetAwareResultList:
        groups: dict[tuple, list] = {}
        order = []
        for row in self.base_qs._rows:
            key = tuple(self.base_qs._row_value(row, f) for f in self.group_fields)
            if key not in groups:
                groups[key] = []
                order.append(key)
            groups[key].append(row)

        results = []
        for key in order:
            rows = groups[key]
            out = dict(zip(self.group_fields, key))
            out.update(_aggregate(rows, aggregates))
            results.append(out)
        return SheetAwareResultList(results)

    def __iter__(self):
        return iter(
            [{f: self.base_qs._row_value(r, f) for f in self.group_fields} for r in self.base_qs._rows]
        )

    def __len__(self):
        return len(self.base_qs._rows)
