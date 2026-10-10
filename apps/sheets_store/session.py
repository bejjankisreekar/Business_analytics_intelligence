"""A request-scoped live view of one org's Google Sheet. Loads each
tab's current rows into memory the first time something asks for it in
a request (one Sheets API read per tab per request, not per query) —
but every write (append/update/delete) goes to the Sheets API
immediately, not buffered, so a request that crashes partway through
never silently loses a financial entry. The in-memory cache is kept in
sync with each write so later reads in the same request see it.

A tab's raw rows are also kept in the process cache (`_CACHE_TTL_SECONDS`)
across requests, so a burst of page loads from the same org doesn't
each re-fetch the same tab from Google. Any write to a tab deletes its
cache entry immediately, so the next read (in this process or another)
always sees it fresh rather than waiting out the TTL.

get_cached_balance/set_cached_balance offer the same cross-request
caching for a computed value apps.finance.services keeps having to
recompute from scratch (a running cash/bank/profit balance summed over
the org's entire history, not just one tab's rows) — invalidated by
_invalidate_tab() the moment a relevant tab is written, not on a timer.

Wired into apps.organizations.middleware.TenantSchemaMiddleware: opened
at the start of a request for an org with a connected Google Drive,
cleared in `finally`.
"""
from __future__ import annotations

import contextlib
import logging
import threading
import uuid

from django.core.cache import cache

from . import client

logger = logging.getLogger(__name__)

_local = threading.local()

# How long a tab's raw rows stay in the cross-request cache before a
# request falls back to Google Sheets again. Keeps a burst of page loads
# (or the "load the rest in background" follow-up request right after
# the initial page render) from re-fetching the same tab from Google —
# bounded short so a stale read after a write outside this process
# (another worker, a script) can't linger long.
_CACHE_TTL_SECONDS = 30

# A cached balance (see get_cached_balance/set_cached_balance below) is
# invalidated precisely, on the next write to a tab it depends on — not
# on a timer — so this TTL only guards against a write path we haven't
# foreseen leaving a stale value cached forever; it's not load-bearing
# for correctness the way _CACHE_TTL_SECONDS above is.
_BALANCE_CACHE_TTL_SECONDS = 300

# Tabs a running cash/bank/profit balance (see apps.finance.services.
# total_balance_as_of / cash_and_bank_as_of — both sum every entry since
# the org's opening date, there's no narrower range to key off) is
# computed from. A write to any of these invalidates every cached
# balance for this org, since we don't track which as_of dates a given
# entry's own date actually affects.
_BALANCE_AFFECTING_TABS = frozenset({
    "SalesEntry", "ExpenseEntry", "PurchaseEntry", "PartnerTransaction", "CashTransfer",
})


def _raw_cache_key(spreadsheet_id: str, tab: str) -> str:
    return f"sheets_store:rows:{spreadsheet_id}:{tab}"


def _decoded_cache_key(spreadsheet_id: str, tab: str) -> str:
    return f"sheets_store:decoded:{spreadsheet_id}:{tab}"


def _balance_generation_key(spreadsheet_id: str) -> str:
    return f"sheets_store:balance_gen:{spreadsheet_id}"


def invalidate_all_for_spreadsheet(spreadsheet_id: str, tabs: list[str]) -> None:
    """Drops the cross-request cache (_CACHE_TTL_SECONDS) for every tab of
    `spreadsheet_id`, plus its cached running balance — used by the
    "Refresh from Sheet" action. A normal write through the app clears a
    tab's cache itself (see SheetSession._invalidate_tab), but an edit
    made directly in the Sheet UI has no such hook: without this, the
    app would keep serving whatever it last cached for up to 30 seconds
    (or longer, if nothing the app itself writes happens to touch that
    tab again). Works off the spreadsheet id alone, no live SheetSession
    required, since cache keys never depend on anything else."""
    for tab in tabs:
        cache.delete(_raw_cache_key(spreadsheet_id, tab))
        cache.delete(_decoded_cache_key(spreadsheet_id, tab))
    cache.delete(_balance_generation_key(spreadsheet_id))


class SheetSession:
    def __init__(self, access_token: str, spreadsheet_id: str):
        self.access_token = access_token
        self.spreadsheet_id = spreadsheet_id
        self._raw_rows: dict[str, list[list]] = {}
        self._sheet_ids: dict[str, int] | None = None
        # Decoded (row_number, instance) pairs per tab, set by
        # apps.sheets_store.store. A fresh SheetAwareQuerySet re-decodes
        # the whole tab on every Model.objects access — a page that
        # touches one model from several service functions would
        # otherwise redecode every row once per function call.
        self._decoded: dict[str, list] = {}
        # None outside a batch_writes() block. While a batch is open,
        # append_rows/update_rows/delete_rows queue their rows here by tab
        # instead of hitting the Sheets API immediately — see batch_writes().
        self._batch: dict[str, dict[str, list]] | None = None

    def _cache_key(self, tab: str) -> str:
        return _raw_cache_key(self.spreadsheet_id, tab)

    def _ensure_loaded(self, tab: str) -> list[list]:
        if tab not in self._raw_rows:
            cached = cache.get(self._cache_key(tab))
            if cached is None:
                cached = client.get_values(
                    self.access_token, spreadsheet_id=self.spreadsheet_id, a1_range=f"{tab}!A1:ZZ100000"
                )
                cache.set(self._cache_key(tab), cached, _CACHE_TTL_SECONDS)
            self._raw_rows[tab] = cached
        return self._raw_rows[tab]

    def prefetch(self, tabs: list[str]) -> None:
        """Loads several tabs with ONE Sheets API call (batchGet) instead
        of one lazy `_ensure_loaded` call per tab as each is first touched.
        A single page commonly needs 6-9 different tabs across its various
        service calls — each individual Sheets API round trip costs
        roughly a second regardless of how small that tab is, so fetching
        them one at a time cost multiple seconds of pure network latency
        before any of the page's own work could even start. Tabs already
        loaded this request, or still within the cross-request cache, are
        skipped — only genuinely missing ones go into the batch call."""
        missing = [tab for tab in dict.fromkeys(tabs) if tab not in self._raw_rows]
        if not missing:
            return
        still_missing = []
        for tab in missing:
            cached = cache.get(self._cache_key(tab))
            if cached is not None:
                self._raw_rows[tab] = cached
            else:
                still_missing.append(tab)
        if not still_missing:
            return
        try:
            results = client.batch_get_values(
                self.access_token,
                spreadsheet_id=self.spreadsheet_id,
                a1_ranges=[f"{tab}!A1:ZZ100000" for tab in still_missing],
            )
        except client.SheetsAPIError:
            # One tab missing from the spreadsheet (e.g. a model added after
            # the sheet was provisioned — see sync_sheet_tabs) fails the whole
            # batch. Don't take every request down with it: skip the prefetch
            # and let each tab load on its own when a page actually uses it.
            logger.exception("Sheets prefetch failed; falling back to per-tab loads")
            return
        for tab, rows in zip(still_missing, results):
            self._raw_rows[tab] = rows
            cache.set(self._cache_key(tab), rows, _CACHE_TTL_SECONDS)

    def _decoded_cache_key(self, tab: str) -> str:
        return _decoded_cache_key(self.spreadsheet_id, tab)

    def get_decoded(self, tab: str) -> list | None:
        if tab in self._decoded:
            return self._decoded[tab]
        # Also cached cross-request (same TTL/invalidation as the raw
        # rows) — decoding a large tab's cells into typed Model instances
        # (dates, Decimals, UUIDs) is real per-row CPU work, otherwise
        # redone from scratch on every request even when the raw rows
        # were already sitting in cache. Django's cache backends pickle
        # on get/set, so this always hands back a private copy — safe
        # even if some caller mutates an instance in place.
        cached = cache.get(self._decoded_cache_key(tab))
        if cached is not None:
            self._decoded[tab] = cached
        return cached

    def set_decoded(self, tab: str, rows: list) -> None:
        self._decoded[tab] = rows
        cache.set(self._decoded_cache_key(tab), rows, _CACHE_TTL_SECONDS)

    def _balance_generation_key(self) -> str:
        return _balance_generation_key(self.spreadsheet_id)

    def _balance_cache_key(self, name: str, args: tuple) -> str:
        # The generation token is created on first use and deleted by
        # _invalidate_tab() below whenever a balance-affecting tab is
        # written — so every key built against a stale generation simply
        # stops being found, rather than needing to hunt down and delete
        # one key per (name, as_of) pair that was ever cached.
        gen = cache.get_or_set(self._balance_generation_key(), lambda: uuid.uuid4().hex, None)
        arg_key = ":".join(str(a) for a in args)
        return f"sheets_store:balance:{self.spreadsheet_id}:{gen}:{name}:{arg_key}"

    def get_cached_balance(self, name: str, *args):
        return cache.get(self._balance_cache_key(name, args))

    def set_cached_balance(self, name: str, *args, value) -> None:
        cache.set(self._balance_cache_key(name, args), value, _BALANCE_CACHE_TTL_SECONDS)

    def _invalidate_tab(self, tab: str) -> None:
        self._decoded.pop(tab, None)
        cache.delete(self._cache_key(tab))
        cache.delete(self._decoded_cache_key(tab))
        if tab in _BALANCE_AFFECTING_TABS:
            cache.delete(self._balance_generation_key())

    def header(self, tab: str) -> list:
        values = self._ensure_loaded(tab)
        return values[0] if values else []

    def data_rows(self, tab: str) -> list[list]:
        """Rows after the header (row 1) — the actual data."""
        values = self._ensure_loaded(tab)
        return values[1:] if len(values) > 1 else []

    def _sheet_id(self, tab: str) -> int:
        if self._sheet_ids is None:
            self._sheet_ids = client.get_sheet_ids(self.access_token, spreadsheet_id=self.spreadsheet_id)
        return self._sheet_ids[tab]

    def _pending(self, tab: str) -> dict[str, list]:
        return self._batch.setdefault(tab, {"updates": [], "deletes": [], "appends": []})

    @contextlib.contextmanager
    def batch_writes(self):
        """Defers every append_row/update_row/delete_row call made inside
        this block, then flushes each tab's queued rows with at most one
        append call, one update call and one delete call — instead of one
        Sheets API round trip per row. Callers keep writing through plain
        instance.save()/.delete() exactly as outside a batch (so a model's
        own save() override, e.g. SalesEntry's gross/discount -> amount
        derivation, still runs normally); only the actual network calls
        underneath get coalesced. This is what turned a multi-row Bulk
        Entry save — one API round trip per row, each costing roughly a
        second — into a couple of requests total.

        Flushed in updates -> deletes -> appends order: updates and
        deletes were both resolved to row numbers against the same
        pre-batch snapshot, so updates must land before any delete can
        shift later rows up; appends have no row number to invalidate and
        always go last. Nested calls join the already-open batch rather
        than flushing early.
        """
        if self._batch is not None:
            yield
            return
        self._batch = {}
        try:
            yield
        finally:
            batch, self._batch = self._batch, None
            for tab, ops in batch.items():
                if ops["updates"]:
                    self.update_rows(tab, ops["updates"])
                if ops["deletes"]:
                    self.delete_rows(tab, ops["deletes"])
                if ops["appends"]:
                    self.append_rows(tab, ops["appends"])

    def append_row(self, tab: str, row: list) -> None:
        self.append_rows(tab, [row])

    def append_rows(self, tab: str, rows: list[list]) -> None:
        """Same as append_row, but writes every row in `rows` with one
        Sheets API call instead of one call per row — the difference
        between a handful of requests and thousands for a genuinely
        bulk write (see store.bulk_create, used by an import or a demo-
        data seed, never by the live single-entry save path)."""
        if not rows:
            return
        if self._batch is not None:
            self._pending(tab)["appends"].extend(rows)
            return
        client.append_rows(self.access_token, spreadsheet_id=self.spreadsheet_id, tab=tab, rows=rows)
        self._invalidate_tab(tab)
        if tab in self._raw_rows:
            self._raw_rows[tab].extend(rows)
        else:
            # First touch of this tab in the request: seed the cache
            # directly from what we just wrote instead of spending a
            # second API call re-reading it. The placeholder in row 0
            # is wrong if something later calls .header(tab) on a tab
            # that was only ever appended to (never read) this request
            # — nothing does today, but a future caller needing the
            # real header should call .header(tab) before its first
            # append, which forces a genuine load.
            self._raw_rows[tab] = [[], *rows]

    def update_row(self, tab: str, row_number: int, row: list) -> None:
        """`row_number` is 1-indexed as seen in the sheet (header=1, so
        the first data row is 2)."""
        self.update_rows(tab, [(row_number, row)])

    def update_rows(self, tab: str, pairs: list[tuple[int, list]]) -> None:
        """Same as update_row, but writes every (row_number, row) pair in
        `pairs` with one Sheets API call instead of one call per row —
        same reasoning as append_rows above, for edits/upserts to rows
        that already exist."""
        if not pairs:
            return
        if self._batch is not None:
            self._pending(tab)["updates"].extend(pairs)
            return
        client.batch_update_values(
            self.access_token,
            spreadsheet_id=self.spreadsheet_id,
            data=[{"range": f"{tab}!A{row_number}", "values": [row]} for row_number, row in pairs],
        )
        self._invalidate_tab(tab)
        values = self._ensure_loaded(tab)
        for row_number, row in pairs:
            if row_number - 1 < len(values):
                values[row_number - 1] = row

    def delete_row(self, tab: str, row_number: int) -> None:
        self.delete_rows(tab, [row_number])

    def delete_rows(self, tab: str, row_numbers: list[int]) -> None:
        """Same as delete_row, but removes every row in `row_numbers` with
        one Sheets API call instead of one call per row (e.g. a formset's
        rows all checked for removal in the same Bulk Entry save)."""
        if not row_numbers:
            return
        if self._batch is not None:
            self._pending(tab)["deletes"].extend(row_numbers)
            return
        client.delete_rows(
            self.access_token,
            spreadsheet_id=self.spreadsheet_id,
            sheet_id=self._sheet_id(tab),
            row_numbers=row_numbers,
        )
        self._invalidate_tab(tab)
        values = self._ensure_loaded(tab)
        for row_number in sorted(set(row_numbers), reverse=True):
            if row_number - 1 < len(values):
                del values[row_number - 1]


def set_active_session(session: SheetSession | None) -> None:
    _local.session = session


def get_active_session() -> SheetSession | None:
    return getattr(_local, "session", None)
