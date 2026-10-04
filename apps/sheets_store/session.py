"""A request-scoped live view of one org's Google Sheet. Loads each
tab's current rows into memory the first time something asks for it in
a request (one Sheets API read per tab per request, not per query) —
but every write (append/update/delete) goes to the Sheets API
immediately, not buffered, so a request that crashes partway through
never silently loses a financial entry. The in-memory cache is kept in
sync with each write so later reads in the same request see it.

Wired into apps.organizations.middleware.TenantSchemaMiddleware: opened
at the start of a request for an org with a connected Google Drive,
cleared in `finally`.
"""
from __future__ import annotations

import threading

from . import client

_local = threading.local()


class SheetSession:
    def __init__(self, access_token: str, spreadsheet_id: str):
        self.access_token = access_token
        self.spreadsheet_id = spreadsheet_id
        self._raw_rows: dict[str, list[list]] = {}
        self._sheet_ids: dict[str, int] | None = None

    def _ensure_loaded(self, tab: str) -> list[list]:
        if tab not in self._raw_rows:
            self._raw_rows[tab] = client.get_values(
                self.access_token, spreadsheet_id=self.spreadsheet_id, a1_range=f"{tab}!A1:ZZ100000"
            )
        return self._raw_rows[tab]

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
        client.append_rows(self.access_token, spreadsheet_id=self.spreadsheet_id, tab=tab, rows=rows)
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
        client.batch_update_values(
            self.access_token,
            spreadsheet_id=self.spreadsheet_id,
            data=[{"range": f"{tab}!A{row_number}", "values": [row]}],
        )
        values = self._ensure_loaded(tab)
        if row_number - 1 < len(values):
            values[row_number - 1] = row

    def delete_row(self, tab: str, row_number: int) -> None:
        client.delete_rows(
            self.access_token,
            spreadsheet_id=self.spreadsheet_id,
            sheet_id=self._sheet_id(tab),
            row_numbers=[row_number],
        )
        values = self._ensure_loaded(tab)
        if row_number - 1 < len(values):
            del values[row_number - 1]


def set_active_session(session: SheetSession | None) -> None:
    _local.session = session


def get_active_session() -> SheetSession | None:
    return getattr(_local, "session", None)
