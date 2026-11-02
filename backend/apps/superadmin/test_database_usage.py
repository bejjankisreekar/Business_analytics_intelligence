"""The database usage page: what the database holds, grouped by app."""
from django.test import Client, TestCase, override_settings

from apps.organizations.services import delete_organization_and_tenant
from apps.organizations.testing import SheetsBackedMixin

from .database_views import database_usage
from .models import MobileAppVersion
from .tests import DATABASES, ENV, _make_client, _make_superadmin


class DatabaseUsageTests(SheetsBackedMixin, TestCase):
    databases = DATABASES

    def setUp(self):
        super().setUp()
        self.superadmin = _make_superadmin("db-usage@test.local")
        self.org, _ = _make_client(self)
        self.client_ = Client()
        self.client_.force_login(self.superadmin)

    def tearDown(self):
        delete_organization_and_tenant(self.org, using=ENV)

    def test_page_lists_apps_and_tables(self):
        resp = self.client_.get(f"/superadmin/{ENV}/database/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "What it's used for")
        self.assertContains(resp, "accounts_user")

    def test_usage_totals_are_consistent(self):
        usage = database_usage(ENV)
        self.assertGreater(usage["database_total"], 0)
        self.assertEqual(usage["tables_total"], sum(app["total"] for app in usage["apps"]))
        self.assertTrue(any(app["key"] == "organizations" for app in usage["apps"]))

    def test_clients_only_list_own_database_storage(self):
        usage = database_usage(ENV)
        self.assertIsInstance(usage["clients"], list)
        for client in usage["clients"]:
            self.assertGreaterEqual(client["bytes"], 0)

    @override_settings(DATABASE_CAPACITY_GB={ENV: 1})
    def test_capacity_card_shows_used_and_remaining(self):
        usage = database_usage(ENV)
        self.assertEqual(usage["capacity_bytes"], 1024**3)
        self.assertEqual(usage["remaining_bytes"], 1024**3 - usage["database_total"])
        self.assertEqual(usage["used_pct"], round(usage["database_total"] * 100 / 1024**3, 1))
        self.assertFalse(usage["over_capacity"])

    def test_requires_superadmin(self):
        resp = Client().get(f"/superadmin/{ENV}/database/")
        self.assertNotEqual(resp.status_code, 200)


class DatabaseAppFileTests(TestCase):
    databases = DATABASES

    def setUp(self):
        self.superadmin = _make_superadmin("db-apps@test.local")
        self.client_ = Client()
        self.client_.force_login(self.superadmin)
        self.current = MobileAppVersion.objects.using(ENV).create(
            version="2.0.0", file=b"x", file_name="a.apk", file_size=1, sha256="0" * 64,
            is_current=True, uploaded_by_email="sa@test.local")
        self.old = MobileAppVersion.objects.using(ENV).create(
            version="1.0.0", file=b"y", file_name="b.apk", file_size=1, sha256="1" * 64,
            is_current=False, uploaded_by_email="sa@test.local")

    def test_delete_old_version(self):
        resp = self.client_.post(f"/superadmin/{ENV}/database/apps/{self.old.pk}/delete/")
        self.assertRedirects(resp, f"/superadmin/{ENV}/database/", fetch_redirect_response=False)
        self.assertFalse(MobileAppVersion.objects.using(ENV).filter(pk=self.old.pk).exists())

    def test_current_version_is_kept(self):
        self.client_.post(f"/superadmin/{ENV}/database/apps/{self.current.pk}/delete/")
        self.assertTrue(MobileAppVersion.objects.using(ENV).filter(pk=self.current.pk).exists())

    def test_compact_runs_vacuum_full(self):
        from unittest import mock

        with mock.patch("apps.superadmin.database_views.connections") as conns:
            resp = self.client_.post(f"/superadmin/{ENV}/database/compact/")
        self.assertEqual(resp.status_code, 302)
        executed = conns.__getitem__.return_value.cursor.return_value.__enter__.return_value.execute
        executed.assert_called_once()
        self.assertIn("VACUUM FULL", executed.call_args[0][0])
