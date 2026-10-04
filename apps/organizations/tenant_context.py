"""The current request's organization, for StorageMode.OUR_DATABASE
orgs — mirrors apps.sheets_store.session's thread-local pattern, just
for the "finance data lives in our own Postgres" path instead of a
Google Sheet. Set by TenantSchemaMiddleware for the duration of a
request; read by SheetAwareManager/SheetAwareModelMixin to scope every
query/save to this organization when no SheetSession is active.
"""
from __future__ import annotations

import threading

_local = threading.local()


def set_current_organization(org) -> None:
    _local.organization = org


def get_current_organization():
    return getattr(_local, "organization", None)
