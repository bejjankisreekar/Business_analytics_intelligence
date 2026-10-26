"""Mixed into every apps.finance model alongside `objects =
SheetAwareManager()` (see queryset.py) — routes instance.save()/
.delete() to the Sheets store when a SheetSession is active (StorageMode.
GOOGLE_SHEETS org), otherwise calls Django's real Model.save()/
.delete(), auto-tagging a brand-new instance with the current
organization first when one is set (StorageMode.OUR_DATABASE org — see
apps.organizations.tenant_context).
"""
from __future__ import annotations

from .session import get_active_session


class SheetAwareModelMixin:
    def save(self, *args, **kwargs):
        if get_active_session() is not None:
            from . import store

            store.save(self)
            return
        if not self.organization_id:
            from apps.organizations.tenant_context import get_current_organization

            org = get_current_organization()
            if org is not None:
                self.organization = org
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if get_active_session() is not None:
            from . import store

            store.delete(self)
            return
        super().delete(*args, **kwargs)
