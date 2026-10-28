"""The database usage page: what the database holds, grouped by app."""
from django.test import Client, TestCase, override_settings

from apps.organizations.services import delete_organization_and_tenant
from apps.organizations.testing import SheetsBackedMixin

from .database_views import database_usage
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
