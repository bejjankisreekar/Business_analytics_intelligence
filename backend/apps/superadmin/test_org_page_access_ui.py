from django.test import Client, TestCase

from apps.organizations.models import Organization
from apps.superadmin.forms import ClientProfileForm

from .tests import DATABASES, ENV, _make_superadmin


class OrgPageAccessEditTests(TestCase):
    """A superadmin sets, from the client edit page, which pages an organization may open."""

    databases = DATABASES

    def setUp(self):
        self.superadmin = _make_superadmin("pages@test.local")
        self.org = Organization.objects.using(ENV).create(
            name="Corner Shop", slug="corner-shop", organization_code="CORNER01",
            storage_mode=Organization.StorageMode.OUR_DATABASE,
            business_type=Organization.BusinessType.RETAIL_ECOMMERCE,
        )
        self.c = Client()
        self.c.force_login(self.superadmin)
        self.url = f"/superadmin/{ENV}/{self.org.pk}/edit/"

    def test_edit_page_lists_the_pages_to_tick(self):
        page = self.c.get(self.url)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Page access")
        for label in ("Daily Book", "Daily Performance", "Monthly Summary", "Customer Requests"):
            self.assertContains(page, label)
        self.assertContains(page, "Leave every box unticked for full access")

    def test_saving_the_ticked_pages(self):
        data = {k: v for k, v in ClientProfileForm(instance=self.org).initial.items() if v not in (None, [])}
        data["allowed_pages"] = ["daily_report", "monthly_summary", "customer_requests"]
        resp = self.c.post(self.url, data)
        self.assertIn(resp.status_code, (200, 302))
        self.org.refresh_from_db(using=ENV)
        self.assertEqual(sorted(self.org.allowed_pages), ["customer_requests", "daily_report", "monthly_summary"])

        data["allowed_pages"] = []
        self.c.post(self.url, data)
        self.org.refresh_from_db(using=ENV)
        self.assertEqual(self.org.allowed_pages, [])
