"""The IP Admissions page must open for a Google Sheets-backed organization.

Regression: the admission form's doctor dropdown called
Subcategory.objects.none(), which the Sheets manager does not implement, so
every visit to /app/patient-bills/ returned a 500 in production.
"""
import datetime
from decimal import Decimal
from unittest import mock

from django.test import Client

from apps.finance.models import KickbackEntry
from apps.organizations.models import Organization
from apps.organizations.services import delete_organization_and_tenant
from apps.organizations.testing import SheetsBackedTestCase, sheet_session


@mock.patch("apps.billing.services.is_account_suspended", return_value=False)
@mock.patch("apps.billing.services.has_active_access", return_value=True)
@mock.patch("apps.billing.services.has_business_tier_plan", return_value=True)
class KickbacksPageSheetsTests(SheetsBackedTestCase):
    def setUp(self):
        super().setUp()
        self.org, self.owner = self.create_connected_organization(
            org_data={"name": "IP Admissions Test Org", "business_type": Organization.BusinessType.HEALTHCARE},
            admin_data={"email": "owner@ipadmissionstest.example", "password": "ownerpass123"},
        )
        self.client_ = Client()
        self.client_.force_login(self.owner)

    def tearDown(self):
        delete_organization_and_tenant(self.org)

    def test_page_loads_with_no_admissions(self, *_):
        resp = self.client_.get("/app/patient-bills/")
        self.assertEqual(resp.status_code, 200)

    def test_page_loads_with_an_admission_on_the_sheet(self, *_):
        with sheet_session(self.org):
            KickbackEntry.objects.create(
                bill_number="IP-1", patient_name="Ramesh", admission_date=datetime.date(2026, 10, 1),
                final_amount=Decimal("5000"), kickback_amount=Decimal("500"),
            )
        resp = self.client_.get("/app/patient-bills/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Ramesh")
