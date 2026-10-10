"""Thin wrapper around the Google Sheets API v4 — the live datastore
for every Sheets-mode org's finance data. Mirrors apps.organizations.
google_drive_client's shape: plain `requests` calls against documented
REST endpoints, no Google SDK. The `drive.file` scope that app already
asks for covers Sheets API access to any spreadsheet created through
it, so no extra OAuth scope is needed here.

Every write uses valueInputOption=RAW — Sheets stores exactly the
string/number/boolean sent with no autodetection or reformatting, which
is what makes apps.sheets_store.fields's coercion a clean, deterministic
round trip (see that module's docstring).
"""
from __future__ import annotations

import time

import requests

SHEETS_BASE = "https://sheets.googleapis.com/v4/spreadsheets"
DRIVE_FILES_URL = "https://www.googleapis.com/drive/v3/files"


class SheetsAPIError(RuntimeError):
    pass


def _post_with_retry(url: str, **kwargs):
    """POST, waiting out Google's per-minute write quota (HTTP 429) a few
    times before giving up — a bulk migration can briefly hit it."""
    for attempt in range(4):
        resp = requests.post(url, **kwargs)
        if resp.status_code != 429 or attempt == 3:
            return resp
        time.sleep(20)


def _headers(access_token: str) -> dict:
    return {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}


def create_spreadsheet(access_token: str, *, title: str, tab_names: list[str], folder_id: str) -> str:
    """Creates a new spreadsheet with one tab per name in `tab_names`
    (in order), then moves it into `folder_id` (the Sheets API has no
    "create inside this folder" option — the Drive API move is a
    required second call). Returns the new spreadsheet id, which is
    also its Drive file id.
    """
    body = {
        "properties": {"title": title},
        "sheets": [{"properties": {"title": name}} for name in tab_names],
    }
    resp = requests.post(SHEETS_BASE, headers=_headers(access_token), json=body, timeout=30)
    if not resp.ok:
        raise SheetsAPIError(f"Couldn't create your spreadsheet: {resp.text[:300]}")
    spreadsheet_id = resp.json()["spreadsheetId"]

    move_resp = requests.patch(
        f"{DRIVE_FILES_URL}/{spreadsheet_id}",
        headers={"Authorization": f"Bearer {access_token}"},
        params={"addParents": folder_id, "removeParents": "root", "fields": "id,parents"},
        timeout=15,
    )
    if not move_resp.ok:
        raise SheetsAPIError(
            f"Created your spreadsheet but couldn't move it into your Drive folder: {move_resp.text[:300]}"
        )
    return spreadsheet_id


def add_sheet_tabs(access_token: str, *, spreadsheet_id: str, titles: list[str]) -> None:
    """Adds one or more new, empty tabs (at the end) to an already
    existing spreadsheet in one call — used to catch up a tenant's
    spreadsheet with a model added after it was first provisioned
    (see apps.organizations.management.commands.sync_sheet_tabs)."""
    if not titles:
        return
    body = {"requests": [{"addSheet": {"properties": {"title": title}}} for title in titles]}
    resp = requests.post(
        f"{SHEETS_BASE}/{spreadsheet_id}:batchUpdate", headers=_headers(access_token), json=body, timeout=30
    )
    if not resp.ok:
        raise SheetsAPIError(f"Couldn't add tab(s) {titles}: {resp.text[:300]}")


def get_values(access_token: str, *, spreadsheet_id: str, a1_range: str) -> list[list]:
    resp = requests.get(
        f"{SHEETS_BASE}/{spreadsheet_id}/values/{requests.utils.quote(a1_range)}",
        headers=_headers(access_token),
        params={"valueRenderOption": "UNFORMATTED_VALUE"},
        timeout=30,
    )
    if not resp.ok:
        raise SheetsAPIError(f"Couldn't read '{a1_range}': {resp.text[:300]}")
    return resp.json().get("values", [])


def batch_get_values(access_token: str, *, spreadsheet_id: str, a1_ranges: list[str]) -> list[list[list]]:
    """Several ranges in ONE Sheets API call — one list of rows per requested
    range, in order. For cheap lookups that only need a column or two."""
    resp = requests.get(
        f"{SHEETS_BASE}/{spreadsheet_id}/values:batchGet",
        headers=_headers(access_token),
        params={"ranges": a1_ranges, "valueRenderOption": "UNFORMATTED_VALUE"},
        timeout=30,
    )
    if not resp.ok:
        raise SheetsAPIError(f"Couldn't read {a1_ranges}: {resp.text[:300]}")
    return [vr.get("values", []) for vr in resp.json().get("valueRanges", [])]


def append_rows(access_token: str, *, spreadsheet_id: str, tab: str, rows: list[list]) -> None:
    if not rows:
        return
    resp = _post_with_retry(
        f"{SHEETS_BASE}/{spreadsheet_id}/values/{requests.utils.quote(tab)}:append",
        headers=_headers(access_token),
        params={"valueInputOption": "RAW", "insertDataOption": "INSERT_ROWS"},
        json={"values": rows},
        timeout=30,
    )
    if not resp.ok:
        raise SheetsAPIError(f"Couldn't add a row to '{tab}': {resp.text[:300]}")


def batch_update_values(access_token: str, *, spreadsheet_id: str, data: list[dict]) -> None:
    """`data` is a list of {"range": "<Tab>!A<row>", "values": [[...]]}
    — writes every range's values in one request. Used both for header
    rows (provisioning) and single-row updates (store.save)."""
    if not data:
        return
    resp = _post_with_retry(
        f"{SHEETS_BASE}/{spreadsheet_id}/values:batchUpdate",
        headers=_headers(access_token),
        json={"valueInputOption": "RAW", "data": data},
        timeout=30,
    )
    if not resp.ok:
        raise SheetsAPIError(f"Couldn't update values: {resp.text[:300]}")


def delete_rows(access_token: str, *, spreadsheet_id: str, sheet_id: int, row_numbers: list[int]) -> None:
    """Deletes 1-indexed `row_numbers` (as seen in the sheet UI; the
    header is row 1) from the tab identified by its numeric `sheet_id`
    — Sheets' structural batchUpdate addresses tabs by id, not name.
    Deletes highest row first so earlier deletions in the same call
    don't shift the row numbers of ones still pending.
    """
    if not row_numbers:
        return
    body = {
        "requests": [
            {
                "deleteDimension": {
                    "range": {
                        "sheetId": sheet_id,
                        "dimension": "ROWS",
                        "startIndex": row_num - 1,
                        "endIndex": row_num,
                    }
                }
            }
            for row_num in sorted(row_numbers, reverse=True)
        ]
    }
    resp = requests.post(
        f"{SHEETS_BASE}/{spreadsheet_id}:batchUpdate", headers=_headers(access_token), json=body, timeout=30
    )
    if not resp.ok:
        raise SheetsAPIError(f"Couldn't delete rows: {resp.text[:300]}")


def get_sheet_ids(access_token: str, *, spreadsheet_id: str) -> dict[str, int]:
    """{tab_name: numeric_sheet_id} for every tab — needed by
    delete_rows, which addresses a tab by that id, not its name."""
    resp = requests.get(
        f"{SHEETS_BASE}/{spreadsheet_id}",
        headers=_headers(access_token),
        params={"fields": "sheets.properties"},
        timeout=15,
    )
    if not resp.ok:
        raise SheetsAPIError(f"Couldn't read spreadsheet structure: {resp.text[:300]}")
    return {s["properties"]["title"]: s["properties"]["sheetId"] for s in resp.json().get("sheets", [])}
