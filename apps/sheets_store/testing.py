"""An in-memory fake standing in for the real Google Sheets API calls in
apps.sheets_store.client, so tests can exercise the full org-creation
and finance-data-write code paths without any live network access or
Google OAuth. See apps.organizations.testing.SheetsBackedTestCase,
which patches apps.sheets_store.client's module-level functions onto an
instance of this class for the duration of a test.
"""
from __future__ import annotations

import itertools


class FakeSheetsBackend:
    """One in-memory stand-in for a Google account's Sheets: maps
    spreadsheet_id -> {tab_name: rows}, mirroring the shape every real
    apps.sheets_store.client call operates on.
    """

    def __init__(self):
        self._spreadsheets: dict[str, dict[str, list[list]]] = {}
        self._sheet_ids: dict[str, dict[str, int]] = {}
        self._next_id = itertools.count(1)

    def create_spreadsheet(self, access_token, *, title, tab_names, folder_id):
        spreadsheet_id = f"fake-sheet-{next(self._next_id)}"
        self._spreadsheets[spreadsheet_id] = {name: [] for name in tab_names}
        self._sheet_ids[spreadsheet_id] = {name: i for i, name in enumerate(tab_names, start=1)}
        return spreadsheet_id

    def get_values(self, access_token, *, spreadsheet_id, a1_range):
        tab = a1_range.split("!", 1)[0]
        return [list(row) for row in self._spreadsheets[spreadsheet_id].get(tab, [])]

    def append_rows(self, access_token, *, spreadsheet_id, tab, rows):
        if not rows:
            return
        self._spreadsheets[spreadsheet_id].setdefault(tab, []).extend(list(r) for r in rows)

    def batch_update_values(self, access_token, *, spreadsheet_id, data):
        for item in data:
            tab, cell = item["range"].split("!", 1)
            row_num = int("".join(ch for ch in cell if ch.isdigit()))
            rows = self._spreadsheets[spreadsheet_id].setdefault(tab, [])
            for offset, row in enumerate(item["values"]):
                idx = row_num - 1 + offset
                while len(rows) <= idx:
                    rows.append([])
                rows[idx] = list(row)

    def delete_rows(self, access_token, *, spreadsheet_id, sheet_id, row_numbers):
        tabs = self._sheet_ids[spreadsheet_id]
        tab = next(name for name, sid in tabs.items() if sid == sheet_id)
        rows = self._spreadsheets[spreadsheet_id][tab]
        for row_num in sorted(row_numbers, reverse=True):
            idx = row_num - 1
            if 0 <= idx < len(rows):
                del rows[idx]

    def get_sheet_ids(self, access_token, *, spreadsheet_id):
        return dict(self._sheet_ids[spreadsheet_id])
