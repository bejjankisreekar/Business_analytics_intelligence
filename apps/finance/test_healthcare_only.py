from unittest import mock

from django.test import Client, TestCase

from apps.accounts.models import User
from apps.finance.access import has_kickbacks_access as real_has_kickbacks_access
from apps.organizations.models import Organization


# The menu's check is looked up by name, so other tests may replace it. These
# tests run the real rule, so they put it back for their own run.
@mock.patch("config.context_processors.has_kickbacks_access", side_effect=real_has_kickbacks_access)
@mock.patch("apps.billing.services.is_account_suspended", return_value=False)
@mock.patch("apps.billing.services.has_active_access", return_value=True)
class HealthcareOnlyPagesTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(
            name="Retail Shop", slug="retail-shop", organization_code="RTLSHP",
            storage_mode=Organization.StorageMode.OUR_DATABASE,
            business_type=Organization.BusinessType.RETAIL_ECOMMERCE,
        )
        self.owner = User.objects.create_user(
            email="owner@retail.example", password="testpass-123", organization=self.org, role=User.Role.OWNER,
        )
        self.client_ = Client()
        self.client_.force_login(self.owner)

    def test_retail_org_has_no_admissions_claims_or_ip_revenue_on_screen(self, *_):
        resp = self.client_.get("/app/setup/")
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, "IP Billing Revenue")
        self.assertNotContains(resp, "Hospital Consulting Doctors")
        dash = self.client_.get("/app/")
        self.assertNotContains(dash, "IP Admissions")
        self.assertNotContains(dash, "Insurance Claims")

    def test_retail_org_cannot_open_claims_or_admissions(self, *_):
        self.assertEqual(self.client_.get("/app/insurance-claims/").status_code, 302)
        self.assertEqual(self.client_.get("/app/patient-bills/").status_code, 302)
