"""Test support for anything that creates an organization and/or writes
apps.finance data. Every org's finance data lives only in its own
Google Sheet, reachable only after a real Google OAuth round trip — not
something a test can do. SheetsBackedMixin patches that whole path with
an in-memory fake (apps.sheets_store.testing.FakeSheetsBackend) and
exposes create_connected_organization(), which does what a real signup
followed by a successful Drive connect would do: create the
Organization + admin user, then provision its (fake) Google Sheet — so
the rest of the test can write finance data exactly as it would against
a real one.

Use SheetsBackedTestCase for a plain TestCase, or mix SheetsBackedMixin
into a TransactionTestCase directly:

    class Foo(SheetsBackedMixin, TransactionTestCase):
        ...
"""
from __future__ import annotations

import datetime
from contextlib import contextmanager
from unittest import mock

from django.test import TestCase
from django.utils import timezone

from .encryption import encrypt_secret
from .models import CloudBackupConnection
from .services import create_organization_with_tenant_schema_and_admin


class SheetsBackedMixin:
    def setUp(self):
        super().setUp()
        from apps.sheets_store.testing import FakeSheetsBackend

        self._sheets_backend = FakeSheetsBackend()
        patchers = [
            mock.patch("apps.organizations.google_drive_client.is_configured", return_value=True),
            mock.patch(
                "apps.sheets_store.client.create_spreadsheet",
                side_effect=self._sheets_backend.create_spreadsheet,
            ),
            mock.patch("apps.sheets_store.client.get_values", side_effect=self._sheets_backend.get_values),
            mock.patch("apps.sheets_store.client.append_rows", side_effect=self._sheets_backend.append_rows),
            mock.patch(
                "apps.sheets_store.client.batch_update_values",
                side_effect=self._sheets_backend.batch_update_values,
            ),
            mock.patch("apps.sheets_store.client.delete_rows", side_effect=self._sheets_backend.delete_rows),
            mock.patch("apps.sheets_store.client.get_sheet_ids", side_effect=self._sheets_backend.get_sheet_ids),
        ]
        for patcher in patchers:
            patcher.start()
            self.addCleanup(patcher.stop)

    def connect_drive_for(self, org, *, using: str = "default"):
        """Connects+provisions `org`'s Google Sheet, exactly as a
        successful Drive OAuth connect would for an org that already
        exists (e.g. one just created via the superadmin "create
        client" view, which — like self-serve signup — defers Sheet
        provisioning until that connect happens).
        """
        from apps.sheets_store.provisioning import provision_sheet_tenant

        access_token = "fake-access-token"
        connection = CloudBackupConnection.objects.using(using).create(
            organization=org,
            access_token_encrypted=encrypt_secret(access_token),
            refresh_token_encrypted=encrypt_secret("fake-refresh-token"),
            token_expires_at=timezone.now() + datetime.timedelta(hours=1),
            external_folder_id="fake-folder",
        )
        spreadsheet_id = provision_sheet_tenant(org, access_token=access_token, folder_id="fake-folder")
        connection.external_file_id = spreadsheet_id
        connection.last_synced_at = timezone.now()
        connection.save(using=using, update_fields=["external_file_id", "last_synced_at"])
        org.refresh_from_db(using=using)
        return connection

    def create_connected_organization(self, *, org_data, admin_data, using: str = "default"):
        """Signs up an org (raises OrganizationSignupError on the usual
        conditions) and connects+provisions its Google Sheet, exactly as
        a real signup immediately followed by a successful Drive OAuth
        connect would — just with a fake Drive/Sheets backend standing
        in for Google. Returns (org, admin), same as the real service
        function.
        """
        org, admin = create_organization_with_tenant_schema_and_admin(
            org_data=org_data, admin_data=admin_data, using=using
        )
        self.connect_drive_for(org, using=using)
        return org, admin


class SheetsBackedTestCase(SheetsBackedMixin, TestCase):
    pass


@contextmanager
def sheet_session(org):
    """Activates `org`'s (fake, in tests) Google Sheet session for a
    block of plain ORM code outside a request — the test equivalent of
    what TenantSchemaMiddleware does per-request. `org` must already be
    connected (created via create_connected_organization)."""
    from apps.organizations.drive_sync import get_valid_access_token
    from apps.sheets_store.session import SheetSession, set_active_session

    connection = org.cloud_backup
    access_token = get_valid_access_token(connection)
    set_active_session(SheetSession(access_token, connection.external_file_id))
    try:
        yield
    finally:
        set_active_session(None)
