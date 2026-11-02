import datetime
from decimal import Decimal
from unittest import mock

from django.test import Client, TestCase

from apps.accounts.models import User
from apps.finance import scan_billing
from apps.finance.models import Category, Product, StockUnit
from apps.mobile_api.auth import make_token
from apps.organizations.models import Organization
from apps.organizations.tenant_context import set_current_organization


class MobileStockTests(TestCase):
    IMEI = "356938035643809"

    def setUp(self):
        self.org = Organization.objects.create(
            name="Stock Shop", slug="stock-shop", organization_code="STKSHP",
            storage_mode=Organization.StorageMode.OUR_DATABASE, barcode_scanning_enabled=True,
            inventory_type=Organization.InventoryType.MOBILES,
        )
        set_current_organization(self.org)
        self.addCleanup(set_current_organization, None)
        self.user = User.objects.create_user(
            email="owner@stock.example", password="testpass-123", organization=self.org, role=User.Role.OWNER,
        )
        mock.patch("apps.billing.services.has_active_access", return_value=True).start()
        mock.patch("apps.billing.services.is_account_suspended", return_value=False).start()
        mock.patch("apps.billing.services.historical_window_start", return_value=None).start()
        self.addCleanup(mock.patch.stopall)
        self.phones = Category.objects.create(organization=self.org, kind=Category.Kind.PRODUCT, name="Phones")
        self.purchase_cat = Category.objects.create(organization=self.org, kind=Category.Kind.PURCHASE, name="Stock")
        self.v10 = Product.objects.create(
            organization=self.org, barcode="8800111", name="Vivo V10", category=self.phones,
            sale_price=Decimal("15000"), purchase_price=Decimal("12000"),
        )
        scan_billing.save_bill(
            kind="purchase", date=datetime.date(2026, 10, 20), payment_mode="CASH", bank_account=None,
            category=self.purchase_cat, vendor="", min_date=None, created_by_email="",
            lines=[{"product_id": str(self.v10.pk), "serials": [self.IMEI]}],
        )
        self.client_ = Client()
        self.auth = {"HTTP_AUTHORIZATION": f"Bearer {make_token(self.user)}"}

    def test_stock_lists_phones_with_status(self):
        res = self.client_.get("/api/mobile/stock/", **self.auth)
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body["in_stock"], 1)
        self.assertEqual(body["units"][0]["serial"], self.IMEI)
        self.assertEqual(body["units"][0]["product"], "Vivo V10")

    def test_stock_filters_by_status_and_search(self):
        res = self.client_.get("/api/mobile/stock/", {"status": "SOLD"}, **self.auth)
        self.assertEqual(res.json()["units"], [])
        res = self.client_.get("/api/mobile/stock/", {"q": "v10"}, **self.auth)
        self.assertEqual(len(res.json()["units"]), 1)
        res = self.client_.get("/api/mobile/stock/", {"q": "nope"}, **self.auth)
        self.assertEqual(res.json()["units"], [])

    def test_product_search_finds_by_name(self):
        res = self.client_.get("/api/mobile/products/search/", {"q": "vivo"}, **self.auth)
        self.assertEqual(res.json()["products"][0]["name"], "Vivo V10")

    def test_product_edit_changes_name_and_price(self):
        res = self.client_.post(
            f"/api/mobile/products/{self.v10.pk}/",
            data={"name": "Vivo V10 Pro", "sale_price": "16000", "purchase_price": "12500"},
            content_type="application/json", **self.auth,
        )
        self.assertEqual(res.status_code, 200, res.content)
        self.v10.refresh_from_db()
        self.assertEqual(self.v10.name, "Vivo V10 Pro")
        self.assertEqual(self.v10.sale_price, Decimal("16000"))

    def test_stock_needs_login(self):
        self.assertEqual(self.client_.get("/api/mobile/stock/").status_code, 401)

    def test_inventory_lists_bought_sold_and_in_stock(self):
        res = self.client_.get("/api/mobile/inventory/", **self.auth)
        self.assertEqual(res.status_code, 200)
        body = res.json()
        item = next(i for i in body["items"] if i["name"] == "Vivo V10")
        self.assertEqual((item["purchased"], item["sold"], item["in_stock"]), (1, 0, 1))
        self.assertTrue(item["tracked"])
        self.assertEqual(body["totals"]["in_stock"], 1)

    def test_inventory_needs_login(self):
        self.assertEqual(self.client_.get("/api/mobile/inventory/").status_code, 401)


class MobileStructureTests(MobileStockTests):
    """The phone app is told the category structure only when scanning is on, and saves by path."""

    def test_entry_options_carry_the_structure_when_scanning_is_on(self):
        res = self.client_.get("/api/mobile/entry-options/", **self.auth)
        structure = res.json()["structure"]
        self.assertEqual(structure["item"], "Item")
        self.assertEqual(structure["groups"], ["Category", "Sub-category"])
        self.assertEqual(structure["commons"], [])

    def test_entry_options_carry_nothing_when_scanning_is_off(self):
        Organization.objects.filter(pk=self.org.pk).update(barcode_scanning_enabled=False)
        res = self.client_.get("/api/mobile/entry-options/", **self.auth)
        self.assertIsNone(res.json()["structure"])

    def test_a_new_item_is_saved_by_path_and_common_values(self):
        levels = [
            {"name": "Mobile", "common": False}, {"name": "Brand", "common": False},
            {"name": "Model name", "common": False}, {"name": "Colour", "common": True},
        ]
        Organization.objects.filter(pk=self.org.pk).update(levels=levels)
        res = self.client_.post(
            "/api/mobile/products/",
            data={"barcode": "7700222", "name": "iPhone 15", "sale_price": "70000", "purchase_price": "60000",
                  "path": ["Mobile", "Apple"], "common": {"Colour": "Black"}},
            content_type="application/json", **self.auth,
        )
        self.assertEqual(res.status_code, 201, res.content)
        product = res.json()["product"]
        self.assertEqual(product["path"], ["Mobile", "Apple"])
        self.assertEqual(product["common"], {"Colour": "Black"})
