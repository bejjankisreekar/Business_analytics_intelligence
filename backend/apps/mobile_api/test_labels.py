from unittest import mock

from django.test import Client, TestCase

from apps.accounts.models import User
from apps.finance.models import Category
from apps.mobile_api.auth import make_token
from apps.organizations.models import Organization
from apps.organizations.tenant_context import set_current_organization


class MobileBarcodeLabelsTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(
            name="Label Shop", slug="label-shop", organization_code="LBLSHP",
            storage_mode=Organization.StorageMode.OUR_DATABASE, barcode_scanning_enabled=True,
        )
        set_current_organization(self.org)
        self.addCleanup(set_current_organization, None)
        self.user = User.objects.create_user(
            email="owner@labels.example", password="testpass-123", organization=self.org, role=User.Role.OWNER,
        )
        mock.patch("apps.billing.services.has_active_access", return_value=True).start()
        mock.patch("apps.billing.services.is_account_suspended", return_value=False).start()
        mock.patch("apps.billing.services.historical_window_start", return_value=None).start()
        self.addCleanup(mock.patch.stopall)
        Category.objects.create(organization=self.org, kind=Category.Kind.SALES, name="In-store")
        self.client_ = Client()
        self.auth = {"HTTP_AUTHORIZATION": f"Bearer {make_token(self.user)}"}

    def test_labels_pdf_for_categories(self):
        res = self.client_.get("/api/mobile/labels/pdf/", {"kind": "category"}, **self.auth)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res["Content-Type"], "application/pdf")
        self.assertTrue(res.content.startswith(b"%PDF"))

    def test_labels_pdf_needs_login(self):
        res = self.client_.get("/api/mobile/labels/pdf/", {"kind": "category"})
        self.assertEqual(res.status_code, 401)

    def test_labels_pdf_is_off_when_scanning_is_off(self):
        Organization.objects.filter(pk=self.org.pk).update(barcode_scanning_enabled=False)
        res = self.client_.get("/api/mobile/labels/pdf/", {"kind": "category"}, **self.auth)
        self.assertEqual(res.status_code, 403)
        self.assertEqual(res.json()["code"], "feature_disabled")
