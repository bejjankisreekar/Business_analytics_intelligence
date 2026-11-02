import datetime
import json
from decimal import Decimal
from unittest import mock

from django.test import Client, TestCase
from django.urls import reverse

from apps.accounts.models import User
from apps.finance import scan_billing
from apps.finance.models import Category, CommonOption, Product, PurchaseEntry, SalesEntry, StockUnit, Subcategory
from apps.finance import scan_billing as _sb_mod
from apps.organizations.models import Organization
from apps.organizations.tenant_context import set_current_organization


class ScanBillingFixtures:
    """Scan-and-bill: products looked up by barcode, and a multi-item bill saved
    as one row per item that shares a bill number."""

    def setUp(self):
        self.org = Organization.objects.create(
            name="Test Mobile Shop", slug="test-mobile-shop", organization_code="TSTMOB",
            storage_mode=Organization.StorageMode.OUR_DATABASE,
            inventory_type=Organization.InventoryType.MOBILES,
        )
        # Barcode scanning is off by default; these tests exercise it once a superadmin has turned it on.
        Organization.objects.filter(pk=self.org.pk).update(barcode_scanning_enabled=True)
        set_current_organization(self.org)
        self.addCleanup(set_current_organization, None)
        self.owner = User.objects.create_user(
            email="owner@mobile.example", password="testpass-123", organization=self.org, role=User.Role.OWNER,
        )
        mock.patch("apps.billing.services.has_active_access", return_value=True).start()
        mock.patch("apps.billing.services.is_account_suspended", return_value=False).start()
        self.addCleanup(mock.patch.stopall)

        self.channel = Category.objects.create(organization=self.org, kind=Category.Kind.SALES, name="In-store")
        self.purchase_cat = Category.objects.create(
            organization=self.org, kind=Category.Kind.PURCHASE, name="Inventory / Stock",
        )
        self.phones = Category.objects.create(organization=self.org, kind=Category.Kind.PRODUCT, name="Phones")
        self.phone = Product.objects.create(
            organization=self.org, barcode="8901234", name="Redmi 13", category=self.phones,
            sale_price=Decimal("12000"), purchase_price=Decimal("10000"),
        )
        self.case = Product.objects.create(
            organization=self.org, barcode="5550001", name="Back cover", category=self.phones,
            sale_price=Decimal("250"), purchase_price=Decimal("80"),
        )
        self.client_ = Client()
        self.client_.force_login(self.owner)

    def _post_json(self, url_name, body):
        return self.client_.post(
            reverse(url_name), data=json.dumps(body), content_type="application/json",
        )

    def test_lookup_finds_known_barcode(self):
        res = self.client_.get(reverse("finance:scan_bill_lookup"), {"barcode": "8901234"})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["product"]["name"], "Redmi 13")
        self.assertEqual(res.json()["product"]["sale_price"], "12000.00")

    def test_lookup_unknown_barcode_is_404(self):
        res = self.client_.get(reverse("finance:scan_bill_lookup"), {"barcode": "0000"})
        self.assertEqual(res.status_code, 404)
        self.assertEqual(res.json()["code"], "not_found")

    def test_duplicate_barcode_is_rejected(self):
        res = self._post_json("finance:scan_bill_product", {"barcode": "8901234", "name": "Copy"})
        self.assertEqual(res.status_code, 400)
        self.assertIn("barcode", res.json()["fields"])

    def test_create_product_from_scan_screen(self):
        res = self._post_json("finance:scan_bill_product", {
            "barcode": "7770007", "name": "Earphones", "sale_price": "999", "purchase_price": "600",
            "category": str(self.phones.pk),
        })
        self.assertEqual(res.status_code, 201)
        product = Product.objects.get(barcode="7770007")
        self.assertEqual(product.sale_price, Decimal("999"))
        self.assertEqual(product.category, self.phones)

    def test_sale_bill_writes_one_row_per_item_with_shared_bill_number(self):
        res = self._post_json("finance:scan_bill_save", {
            "type": "sale",
            "date": "2026-10-20",
            "category": str(self.channel.pk),
            "payment_mode": "CASH",
            "lines": [
                {"product_id": str(self.phone.pk), "quantity": 1},
                {"product_id": str(self.case.pk), "quantity": 2, "unit_price": "200"},
            ],
        })
        self.assertEqual(res.status_code, 201, res.content)
        rows = list(SalesEntry.objects.filter(bill_no=res.json()["bill_no"]).order_by("amount"))
        self.assertEqual(len(rows), 2)
        self.assertEqual([r.amount for r in rows], [Decimal("400.00"), Decimal("12000.00")])
        self.assertEqual(rows[1].product, self.phone)
        self.assertEqual(rows[1].product_category, self.phones)
        self.assertEqual(rows[0].channel, self.channel)
        self.assertEqual(res.json()["total"], "12400.00")

    def test_purchase_bill_defaults_to_purchase_price(self):
        res = self._post_json("finance:scan_bill_save", {
            "type": "purchase",
            "date": "2026-10-20",
            "category": str(self.purchase_cat.pk),
            "payment_mode": "CASH",
            "vendor": "Wholesale Co",
            "lines": [{"product_id": str(self.case.pk), "quantity": 5}],
        })
        self.assertEqual(res.status_code, 201, res.content)
        row = PurchaseEntry.objects.get(bill_no=res.json()["bill_no"])
        self.assertEqual(row.amount, Decimal("400.00"))
        self.assertEqual(row.vendor, "Wholesale Co")
        self.assertEqual(row.category, self.purchase_cat)

    def test_bill_with_no_items_saves_nothing(self):
        res = self._post_json("finance:scan_bill_save", {
            "type": "sale", "date": "2026-10-20", "category": str(self.channel.pk), "lines": [],
        })
        self.assertEqual(res.status_code, 400)
        self.assertIn("lines", res.json()["fields"])
        self.assertFalse(SalesEntry.objects.exists())

    def test_bad_line_stops_whole_bill(self):
        with self.assertRaises(_sb_mod.BillError):
            _sb_mod.save_bill(
                kind="sale",
                date=datetime.date(2026, 10, 20),
                payment_mode="CASH",
                bank_account=None,
                category=self.channel,
                vendor="",
                lines=[
                    {"product_id": str(self.phone.pk), "quantity": 1},
                    {"product_id": str(self.case.pk), "quantity": 0},
                ],
                min_date=None,
                created_by_email=self.owner.email,
            )
        self.assertFalse(SalesEntry.objects.exists())

    def test_scan_page_renders_for_sale_and_purchase(self):
        for kind in ("sale", "purchase"):
            res = self.client_.get(reverse("finance:scan_bill"), {"type": kind})
            self.assertEqual(res.status_code, 200)
            self.assertContains(res, 'id="sb-barcode"')
            self.assertContains(res, "Save " + kind + " bill")

    def test_scanning_is_off_by_default_for_an_organization(self):
        Organization.objects.filter(pk=self.org.pk).update(barcode_scanning_enabled=False)
        res = self.client_.get(reverse("finance:scan_bill_lookup"), {"barcode": "8901234"})
        self.assertEqual(res.status_code, 403)
        self.assertEqual(res.json()["code"], "feature_disabled")
        res = self._post_json("finance:scan_bill_save", {
            "type": "sale", "date": "2026-10-20", "category": str(self.channel.pk),
            "lines": [{"product_id": str(self.phone.pk), "quantity": 1}],
        })
        self.assertEqual(res.status_code, 403)
        self.assertFalse(SalesEntry.objects.exists())
        self.assertEqual(self.client_.get(reverse("finance:scan_bill")).status_code, 404)

    def test_new_organization_defaults_to_scanning_off(self):
        org = Organization.objects.create(
            name="Fresh Shop", slug="fresh-shop", organization_code="FRSHOP",
            storage_mode=Organization.StorageMode.OUR_DATABASE,
        )
        self.assertFalse(org.barcode_scanning_enabled)


class BarcodeCodesTests(ScanBillingFixtures, TestCase):
    """Categories and sub-categories have their own codes, products get a code when
    the packet has none, and printing labels gives every item a code."""

    def test_new_product_without_barcode_gets_a_generated_code(self):
        res = self._post_json("finance:scan_bill_product", {
            "barcode": "", "name": "Loose cable", "sale_price": "99", "purchase_price": "40",
        })
        self.assertEqual(res.status_code, 201, res.content)
        product = Product.objects.get(name="Loose cable")
        self.assertTrue(product.barcode.startswith("P"))
        self.assertEqual(len(product.barcode), 9)

    def test_category_code_is_resolved_and_sets_the_bill_category(self):
        self.channel.barcode = "CSALESONE"
        self.channel.save()
        res = self.client_.get(reverse("finance:scan_bill_lookup"), {"barcode": "CSALESONE"})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["type"], "category")
        self.assertEqual(res.json()["category"]["id"], str(self.channel.pk))

    def test_subcategory_code_is_resolved(self):
        sub = Subcategory.objects.create(
            organization=self.org, category=self.channel, name="Accessories", barcode="SACCESS1",
        )
        res = self.client_.get(reverse("finance:scan_bill_lookup"), {"barcode": "SACCESS1"})
        self.assertEqual(res.json()["type"], "subcategory")
        self.assertEqual(res.json()["subcategory"]["category_id"], str(self.channel.pk))
        self.assertEqual(res.json()["subcategory"]["id"], str(sub.pk))

    def test_sale_with_subcategory_saves_it_on_each_row(self):
        sub = Subcategory.objects.create(
            organization=self.org, category=self.channel, name="Phones", barcode="SPHONES1",
        )
        res = self._post_json("finance:scan_bill_save", {
            "type": "sale", "date": "2026-10-20", "category": str(self.channel.pk),
            "subcategory": str(sub.pk),
            "lines": [{"product_id": str(self.phone.pk), "quantity": 1}],
        })
        self.assertEqual(res.status_code, 201, res.content)
        row = SalesEntry.objects.get(bill_no=res.json()["bill_no"])
        self.assertEqual(row.subcategory, sub)

    def test_product_code_clashing_with_a_category_code_is_refused(self):
        self.channel.barcode = "CCLASH01"
        self.channel.save()
        res = self._post_json("finance:scan_bill_product", {"barcode": "CCLASH01", "name": "Clash"})
        self.assertEqual(res.status_code, 400)
        self.assertIn("barcode", res.json()["fields"])

    def test_label_pdf_gives_items_codes_and_returns_a_pdf(self):
        res = self.client_.get(reverse("finance:barcode_labels_pdf"), {"kind": "category"})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res["Content-Type"], "application/pdf")
        self.assertTrue(res.content.startswith(b"%PDF"))
        self.channel.refresh_from_db()
        self.assertTrue(self.channel.barcode.startswith("C"))
        res = self.client_.get(reverse("finance:barcode_labels_pdf"), {"kind": "product"})
        self.assertTrue(res.content.startswith(b"%PDF"))
        self.phone.refresh_from_db()
        self.assertTrue(self.phone.barcode)

    def test_label_pages_render(self):
        res = self.client_.get(reverse("finance:barcode_labels"))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Print product labels")

    def test_label_pdf_rejects_unknown_kind(self):
        res = self.client_.get(reverse("finance:barcode_labels_pdf"), {"kind": "bogus"})
        self.assertEqual(res.status_code, 400)


class SingleLabelTests(ScanBillingFixtures, TestCase):
    """Each category and sub-category has its own label to print."""

    def test_single_category_label_pdf(self):
        res = self.client_.get(reverse("finance:barcode_labels_pdf"), {"kind": "category", "id": str(self.channel.pk)})
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.content.startswith(b"%PDF"))
        self.channel.refresh_from_db()
        self.assertTrue(self.channel.barcode.startswith("C"))

    def test_single_subcategory_label_pdf(self):
        sub = Subcategory.objects.create(organization=self.org, category=self.channel, name="Phones")
        res = self.client_.get(reverse("finance:barcode_labels_pdf"), {"kind": "subcategory", "id": str(sub.pk)})
        self.assertTrue(res.content.startswith(b"%PDF"))
        sub.refresh_from_db()
        self.assertTrue(sub.barcode.startswith("S"))

    def test_unknown_item_is_404(self):
        res = self.client_.get(
            reverse("finance:barcode_labels_pdf"),
            {"kind": "category", "id": "00000000-0000-0000-0000-000000000000"},
        )
        self.assertEqual(res.status_code, 404)

    def test_labels_page_lists_each_item_with_its_own_print_link(self):
        sub = Subcategory.objects.create(organization=self.org, category=self.channel, name="Accessories")
        res = self.client_.get(reverse("finance:barcode_labels"))
        self.assertContains(res, f"kind=category&amp;id={self.channel.pk}")
        self.assertContains(res, f"kind=subcategory&amp;id={sub.pk}")


class SheetCopiesTests(ScanBillingFixtures, TestCase):
    def test_single_item_prints_a_sheet_of_the_same_code(self):
        res = self.client_.get(reverse("finance:barcode_labels_pdf"), {"kind": "category", "id": str(self.channel.pk)})
        small = len(res.content)
        res = self.client_.get(
            reverse("finance:barcode_labels_pdf"),
            {"kind": "category", "id": str(self.channel.pk), "copies": "100"},
        )
        self.assertTrue(res.content.startswith(b"%PDF"))
        self.assertGreater(len(res.content), small)

    def test_copies_are_capped(self):
        from apps.finance.scan_views import MAX_COPIES, _copies
        from django.test import RequestFactory
        request = RequestFactory().get("/", {"copies": "99999"})
        self.assertEqual(_copies(request), MAX_COPIES)
        request = RequestFactory().get("/", {"copies": "0"})
        self.assertEqual(_copies(request), 1)


class ProductEditTests(ScanBillingFixtures, TestCase):
    def _put(self, product, body):
        return self._post_json_to(
            reverse("finance:scan_bill_product_detail", args=[product.pk]), body,
        )

    def _post_json_to(self, url, body):
        return self.client_.post(url, data=json.dumps(body), content_type="application/json")

    def test_edit_name_and_prices_keeps_barcode_and_category(self):
        res = self._put(self.phone, {"name": "Redmi 13 Pro", "sale_price": "13000", "purchase_price": "11000"})
        self.assertEqual(res.status_code, 200, res.content)
        self.phone.refresh_from_db()
        self.assertEqual(self.phone.name, "Redmi 13 Pro")
        self.assertEqual(self.phone.sale_price, Decimal("13000"))
        self.assertEqual(self.phone.barcode, "8901234")
        self.assertEqual(self.phone.category, self.phones)

    def test_edit_to_another_items_barcode_is_refused(self):
        res = self._put(self.phone, {"name": "Redmi", "barcode": self.case.barcode})
        self.assertEqual(res.status_code, 400)
        self.assertIn("barcode", res.json()["fields"])

    def test_edit_requires_a_name(self):
        res = self._put(self.phone, {"name": "  "})
        self.assertEqual(res.status_code, 400)
        self.assertIn("name", res.json()["fields"])

    def test_edited_price_is_used_by_the_next_scan(self):
        self._put(self.case, {"name": "Back cover", "sale_price": "300"})
        res = self.client_.get(reverse("finance:scan_bill_lookup"), {"barcode": self.case.barcode})
        self.assertEqual(res.json()["product"]["sale_price"], "300.00")


class PhoneTrackingTests(ScanBillingFixtures, TestCase):
    """Phones are tracked one by one: a purchase records each IMEI in stock, and a sale
    takes the exact phone and marks it sold."""

    IMEI_A = "356938035643809"
    IMEI_B = "490154203237518"

    def _purchase(self, serials, product=None):
        return _sb_mod.save_bill(
            kind="purchase", date=datetime.date(2026, 10, 20), payment_mode="CASH", bank_account=None,
            category=self.purchase_cat, vendor="Distributor", min_date=None, created_by_email="",
            lines=[{"product_id": str(product or self.phone.pk), "serials": serials}],
        )

    def _sale(self, lines):
        return _sb_mod.save_bill(
            kind="sale", date=datetime.date(2026, 10, 21), payment_mode="CASH", bank_account=None,
            category=self.channel, vendor="", min_date=None, created_by_email="", lines=lines,
        )

    def test_purchase_records_each_phone_in_stock(self):
        result = self._purchase([self.IMEI_A, self.IMEI_B])
        self.assertEqual(len(result["rows"]), 2)
        self.assertEqual(StockUnit.objects.filter(product=self.phone, status="IN_STOCK").count(), 2)
        self.assertEqual(result["rows"][0].note, f"IMEI {self.IMEI_A}")

    def test_same_imei_cannot_be_bought_twice(self):
        self._purchase([self.IMEI_A])
        with self.assertRaises(_sb_mod.BillError):
            self._purchase([self.IMEI_A])

    def test_tracked_phone_cannot_be_sold_by_box_barcode(self):
        self._purchase([self.IMEI_A])
        with self.assertRaises(_sb_mod.BillError):
            self._sale([{"product_id": str(self.phone.pk), "quantity": 1}])

    def test_sale_marks_the_exact_phone_sold(self):
        self._purchase([self.IMEI_A, self.IMEI_B])
        result = self._sale([{"product_id": str(self.phone.pk), "serials": [self.IMEI_A]}])
        unit = StockUnit.objects.get(serial=self.IMEI_A)
        self.assertEqual(unit.status, "SOLD")
        self.assertEqual(unit.sale_bill_no, result["bill_no"])
        self.assertEqual(StockUnit.objects.get(serial=self.IMEI_B).status, "IN_STOCK")
        self.assertEqual(result["rows"][0].product_category, self.phones)

    def test_sold_phone_cannot_be_sold_again(self):
        self._purchase([self.IMEI_A])
        self._sale([{"product_id": str(self.phone.pk), "serials": [self.IMEI_A]}])
        with self.assertRaises(_sb_mod.BillError):
            self._sale([{"product_id": str(self.phone.pk), "serials": [self.IMEI_A]}])

    def test_phone_of_another_model_is_refused(self):
        self._purchase([self.IMEI_A])
        with self.assertRaises(_sb_mod.BillError):
            self._sale([{"product_id": str(self.case.pk), "serials": [self.IMEI_A]}])

    def test_imei_lookup_finds_the_phone(self):
        self._purchase([self.IMEI_A])
        res = self.client_.get(reverse("finance:scan_bill_lookup"), {"barcode": self.IMEI_A})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["type"], "unit")
        self.assertEqual(res.json()["unit"]["status"], "IN_STOCK")
        self.assertTrue(res.json()["unit"]["product"]["tracked"])

    def test_sale_with_serials_through_the_web_endpoint(self):
        self._purchase([self.IMEI_A])
        res = self._post_json("finance:scan_bill_save", {
            "type": "sale", "date": "2026-10-21", "category": str(self.channel.pk),
            "lines": [{"product_id": str(self.phone.pk), "serials": [self.IMEI_A]}],
        })
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(StockUnit.objects.get(serial=self.IMEI_A).status, "SOLD")


class ProductSubcategoryTests(ScanBillingFixtures, TestCase):
    """An item sits under a sub-category: Mobiles > Vivo > V10."""

    def setUp(self):
        super().setUp()
        self.vivo = Subcategory.objects.create(
            organization=self.org, category=self.phones, name="Vivo",
        )

    def test_new_product_is_stored_under_its_subcategory(self):
        res = self._post_json("finance:scan_bill_product", {
            "barcode": "", "name": "V10", "sale_price": "15000", "purchase_price": "12000",
            "category": str(self.phones.pk), "subcategory": str(self.vivo.pk),
        })
        self.assertEqual(res.status_code, 201, res.content)
        product = Product.objects.get(name="V10")
        self.assertEqual(product.subcategory, self.vivo)
        self.assertEqual(res.json()["product"]["subcategory_path"], "Phones › Vivo")

    def test_editing_name_keeps_the_subcategory(self):
        self.phone.subcategory = self.vivo
        self.phone.save()
        self._post_json_to(
            reverse("finance:scan_bill_product_detail", args=[self.phone.pk]), {"name": "V10 Pro"},
        )
        self.phone.refresh_from_db()
        self.assertEqual(self.phone.subcategory, self.vivo)

    def test_editing_can_move_it_to_another_subcategory(self):
        other = Subcategory.objects.create(organization=self.org, category=self.phones, name="Oppo")
        self.phone.subcategory = self.vivo
        self.phone.save()
        res = self._post_json_to(
            reverse("finance:scan_bill_product_detail", args=[self.phone.pk]),
            {"name": "Reno", "subcategory": str(other.pk)},
        )
        self.assertEqual(res.status_code, 200, res.content)
        self.phone.refresh_from_db()
        self.assertEqual(self.phone.subcategory, other)

    def _post_json_to(self, url, body):
        return self.client_.post(url, data=json.dumps(body), content_type="application/json")


class InventoryTests(ScanBillingFixtures, TestCase):
    """Inventory and stock: bought, sold and in stock for each item, and the phones by IMEI."""

    def _bill(self, kind, product, quantity=None, serials=None, price=None):
        line = {"product_id": str(product.pk)}
        if quantity:
            line["quantity"] = quantity
        if serials:
            line["serials"] = serials
        if price:
            line["unit_price"] = price
        return _sb_mod.save_bill(
            kind=kind, date=datetime.date.today(), payment_mode="CASH", bank_account=None,
            category=self.channel if kind == "sale" else self.purchase_cat,
            vendor="", min_date=None, created_by_email="", lines=[line],
        )

    def _row(self, product):
        from apps.finance import inventory
        return next(r for r in inventory.inventory_rows() if r["id"] == str(product.pk))

    def test_untracked_item_is_bought_minus_sold(self):
        self._bill("purchase", self.case, quantity=10)
        self._bill("sale", self.case, quantity=3)
        row = self._row(self.case)
        self.assertEqual((row["purchased"], row["sold"], row["in_stock"]), (10, 3, 7))
        self.assertEqual(row["stock_value"], Decimal("560.00"))  # 7 x purchase price 80

    def test_tracked_phone_counts_phones_in_stock(self):
        self._bill("purchase", self.phone, serials=["356938035643809", "490154203237518"])
        self._bill("sale", self.phone, serials=["356938035643809"])
        row = self._row(self.phone)
        self.assertTrue(row["tracked"])
        self.assertEqual((row["purchased"], row["sold"], row["in_stock"]), (2, 1, 1))

    def test_item_with_no_bills_shows_zero(self):
        row = self._row(self.case)
        self.assertEqual((row["purchased"], row["sold"], row["in_stock"]), (0, 0, 0))

    def test_inventory_page_renders_all_tabs(self):
        self._bill("purchase", self.case, quantity=5)
        for tab in ("items", "phones", "activity"):
            res = self.client_.get(reverse("finance:inventory"), {"tab": tab})
            self.assertEqual(res.status_code, 200)
        res = self.client_.get(reverse("finance:inventory"))
        self.assertContains(res, "Back cover")

    def test_inventory_page_is_off_when_scanning_is_off(self):
        Organization.objects.filter(pk=self.org.pk).update(barcode_scanning_enabled=False)
        self.assertEqual(self.client_.get(reverse("finance:inventory")).status_code, 404)

    def test_recent_activity_lists_purchases_and_sales(self):
        from apps.finance import inventory
        self._bill("purchase", self.case, quantity=4)
        self._bill("sale", self.case, quantity=1)
        kinds = {m["kind"] for m in inventory.recent_movements()}
        self.assertEqual(kinds, {"purchase", "sale"})


class PhoneDetailsTests(ScanBillingFixtures, TestCase):
    """Each phone is recorded with brand, model, colour, RAM, storage, a second IMEI and other details."""

    IMEI_A = "356938035643809"
    IMEI_B = "490154203237518"

    def setUp(self):
        super().setUp()
        self.vivo = Subcategory.objects.create(organization=self.org, category=self.phones, name="Vivo")
        # Its own item, so the shared test phone is left as it is.
        self.v10 = Product.objects.create(
            organization=self.org, barcode="V10BOX", name="V10", category=self.phones, subcategory=self.vivo,
            sale_price=Decimal("15000"), purchase_price=Decimal("12000"),
        )

    def _purchase(self, serial, details=None):
        return _sb_mod.save_bill(
            kind="purchase", date=datetime.date(2026, 10, 20), payment_mode="CASH", bank_account=None,
            category=self.purchase_cat, vendor="", min_date=None, created_by_email="",
            lines=[{"product_id": str(self.v10.pk), "serials": [serial], "details": details}],
        )

    def test_details_are_recorded_with_the_phone(self):
        self._purchase(self.IMEI_A, {
            "colour": "Midnight Blue", "ram": "8GB", "storage": "128GB",
            "imei2": "356938035643817", "other": "Box sealed, charger included",
        })
        unit = StockUnit.objects.get(serial=self.IMEI_A)
        self.assertEqual(unit.colour, "Midnight Blue")
        self.assertEqual(unit.ram, "8GB")
        self.assertEqual(unit.storage, "128GB")
        self.assertEqual(unit.imei2, "356938035643817")
        self.assertEqual(unit.other_details, "Box sealed, charger included")

    def test_brand_and_model_start_from_the_item(self):
        self._purchase(self.IMEI_A)
        unit = StockUnit.objects.get(serial=self.IMEI_A)
        self.assertEqual(unit.brand, "Vivo")
        self.assertEqual(unit.model_name, "V10")

    def test_brand_and_model_can_be_overridden(self):
        self._purchase(self.IMEI_A, {"brand": "iQOO", "model": "Z9"})
        unit = StockUnit.objects.get(serial=self.IMEI_A)
        self.assertEqual((unit.brand, unit.model_name), ("iQOO", "Z9"))

    def test_scanning_the_second_imei_finds_the_phone(self):
        self._purchase(self.IMEI_A, {"imei2": "356938035643817"})
        res = self.client_.get(reverse("finance:scan_bill_lookup"), {"barcode": "356938035643817"})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["type"], "unit")
        self.assertEqual(res.json()["unit"]["serial"], self.IMEI_A)

    def test_lookup_describes_the_phone(self):
        self._purchase(self.IMEI_A, {"colour": "Blue", "ram": "8GB", "storage": "128GB"})
        res = self.client_.get(reverse("finance:scan_bill_lookup"), {"barcode": self.IMEI_A})
        self.assertEqual(res.json()["unit"]["description"], "Vivo · V10 · Blue · RAM 8GB · 128GB")

    def test_second_imei_already_used_is_refused(self):
        self._purchase(self.IMEI_A, {"imei2": "356938035643817"})
        with self.assertRaises(_sb_mod.BillError):
            self._purchase(self.IMEI_B, {"imei2": "356938035643817"})

    def test_second_imei_equal_to_the_first_is_refused(self):
        with self.assertRaises(_sb_mod.BillError):
            self._purchase(self.IMEI_A, {"imei2": self.IMEI_A})

    def test_a_first_imei_that_is_another_phones_second_imei_is_refused(self):
        self._purchase(self.IMEI_A, {"imei2": "356938035643817"})
        with self.assertRaises(_sb_mod.BillError):
            self._purchase("356938035643817")

    def test_details_show_in_the_phone_list_and_search(self):
        from apps.finance import inventory
        self._purchase(self.IMEI_A, {"colour": "Blue", "ram": "8GB"})
        rows = inventory.unit_rows()
        self.assertEqual(rows[0]["colour"], "Blue")
        self.assertEqual(len(inventory.unit_rows(query="blue")), 1)
        self.assertEqual(len(inventory.unit_rows(query="red")), 0)

    def test_over_long_details_are_cut_to_fit(self):
        self._purchase(self.IMEI_A, {"colour": "x" * 200})
        self.assertEqual(len(StockUnit.objects.get(serial=self.IMEI_A).colour), 50)

    def test_details_come_through_the_web_save_endpoint(self):
        res = self._post_json("finance:scan_bill_save", {
            "type": "purchase", "date": "2026-10-20", "category": str(self.purchase_cat.pk),
            "lines": [{
                "product_id": str(self.v10.pk), "serials": [self.IMEI_A],
                "details": {"colour": "Green", "ram": "12GB", "storage": "256GB"},
            }],
        })
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(StockUnit.objects.get(serial=self.IMEI_A).ram, "12GB")


class OrganizationIsolationTests(ScanBillingFixtures, TestCase):
    """Only organizations a superadmin switched on get scanning, inventory, labels and stock,
    and an organization never sees another's items or phones."""

    def setUp(self):
        super().setUp()
        # A second organization (a hospital, say) that has not been switched on.
        self.hospital = Organization.objects.create(
            name="City Hospital", slug="city-hospital", organization_code="CITYHSP",
            storage_mode=Organization.StorageMode.OUR_DATABASE,
        )
        self.hospital_user = User.objects.create_user(
            email="owner@hospital.example", password="testpass-123",
            organization=self.hospital, role=User.Role.OWNER,
        )
        self.hospital_client = Client()
        self.hospital_client.force_login(self.hospital_user)
        from apps.mobile_api.auth import make_token
        self.hospital_auth = {"HTTP_AUTHORIZATION": f"Bearer {make_token(self.hospital_user)}"}

    def test_organization_without_the_switch_gets_none_of_it(self):
        for name in ("scan_bill", "inventory", "barcode_labels"):
            self.assertEqual(self.hospital_client.get(reverse(f"finance:{name}")).status_code, 404, name)
        res = self.hospital_client.get(reverse("finance:scan_bill_lookup"), {"barcode": "8901234"})
        self.assertEqual(res.status_code, 403)
        res = self.hospital_client.get(reverse("finance:scan_bill_product_search"), {"q": "redmi"})
        self.assertEqual(res.status_code, 403)

    def test_phone_app_endpoints_are_closed_without_the_switch(self):
        for path in ("/api/mobile/stock/", "/api/mobile/inventory/", "/api/mobile/products/search/?q=redmi",
                     "/api/mobile/labels/pdf/?kind=product", "/api/mobile/products/lookup/?barcode=8901234"):
            res = self.hospital_client.get(path, **self.hospital_auth)
            self.assertEqual(res.status_code, 403, path)
            self.assertEqual(res.json()["code"], "feature_disabled", path)

    def test_phone_app_options_carry_no_scan_data_without_the_switch(self):
        res = self.hospital_client.get("/api/mobile/entry-options/", **self.hospital_auth)
        self.assertEqual(res.status_code, 200)
        self.assertFalse(res.json()["barcode_scanning_enabled"])
        self.assertEqual(res.json()["product_subcategories"], [])

    def test_entry_pages_show_no_scan_links_without_the_switch(self):
        html = self.hospital_client.get(reverse("finance:daily_report")).content.decode()
        self.assertNotIn("scan-bill", html)
        html = self.hospital_client.get(reverse("finance:single_entry")).content.decode()
        self.assertNotIn("scan-bill", html)

    def test_a_switched_on_organization_does_not_see_another_organizations_items(self):
        # Switch the hospital on too: it still has none of the shop's items or phones.
        Organization.objects.filter(pk=self.hospital.pk).update(barcode_scanning_enabled=True)
        res = self.hospital_client.get(reverse("finance:scan_bill_lookup"), {"barcode": "8901234"})
        self.assertEqual(res.status_code, 404)
        html = self.hospital_client.get(reverse("finance:inventory")).content.decode()
        self.assertNotIn("Redmi 13", html)
        res = self.hospital_client.get("/api/mobile/inventory/", **self.hospital_auth)
        self.assertEqual(res.json()["items"], [])

    def test_google_sheets_tabs_are_only_for_switched_on_organizations(self):
        from apps.sheets_store import provisioning, store
        shop, hospital = self.org, self.hospital
        shop.refresh_from_db()  # the switch was turned on in the database during setup
        feature_tabs = {"Product", "StockUnit"}
        self.assertTrue(feature_tabs <= set(store.all_sheet_backed_tabs(shop)))
        self.assertFalse(feature_tabs & set(store.all_sheet_backed_tabs(hospital)))
        self.assertTrue(feature_tabs <= set(store.all_sheet_backed_tabs()))  # no organization: every tab
        self.assertFalse(feature_tabs & {m.__name__ for m in provisioning.tenant_models(hospital)})
        self.assertTrue(feature_tabs <= {m.__name__ for m in provisioning.tenant_models(shop)})
        # The core tabs are the same for both.
        self.assertIn("SalesEntry", store.all_sheet_backed_tabs(hospital))


class PhoneDetailsEditTests(ScanBillingFixtures, TestCase):
    """Details of a phone already in stock can be filled in or changed from the sale screen."""

    IMEI_A = "356938035643809"
    IMEI_B = "490154203237518"

    def setUp(self):
        super().setUp()
        # Its own item, so the shared test phone is left as it is.
        self.handset = Product.objects.create(
            organization=self.org, barcode="HANDSET1", name="Handset", category=self.phones,
            sale_price=Decimal("9000"), purchase_price=Decimal("7000"),
        )
        for imei in (self.IMEI_A, self.IMEI_B):
            _sb_mod.save_bill(
                kind="purchase", date=datetime.date(2026, 10, 20), payment_mode="CASH", bank_account=None,
                category=self.purchase_cat, vendor="", min_date=None, created_by_email="",
                lines=[{"product_id": str(self.handset.pk), "serials": [imei]}],
            )
        self.unit = StockUnit.objects.get(serial=self.IMEI_A)

    def _edit(self, unit, body):
        return self.client_.post(
            reverse("finance:scan_bill_unit", args=[unit.pk]),
            data=json.dumps(body), content_type="application/json",
        )

    def test_lookup_gives_the_phone_id_and_details(self):
        res = self.client_.get(reverse("finance:scan_bill_lookup"), {"barcode": self.IMEI_A})
        unit = res.json()["unit"]
        self.assertEqual(unit["id"], str(self.unit.pk))
        for key in ("brand", "model", "colour", "ram", "storage", "imei2", "other", "description"):
            self.assertIn(key, unit)

    def test_details_can_be_filled_in(self):
        res = self._edit(self.unit, {
            "brand": "Vivo", "model": "V10", "colour": "Black", "ram": "6GB", "storage": "64GB",
            "imei2": "356938035643817", "other": "Refurbished",
        })
        self.assertEqual(res.status_code, 200, res.content)
        self.unit.refresh_from_db()
        self.assertEqual((self.unit.brand, self.unit.colour, self.unit.ram), ("Vivo", "Black", "6GB"))
        self.assertEqual(self.unit.imei2, "356938035643817")
        self.assertEqual(res.json()["unit"]["description"], "Vivo · V10 · Black · RAM 6GB · 64GB")

    def test_a_detail_can_be_cleared(self):
        self._edit(self.unit, {"colour": "Black"})
        self._edit(self.unit, {"colour": ""})
        self.unit.refresh_from_db()
        self.assertEqual(self.unit.colour, "")

    def test_fields_not_sent_are_left_alone(self):
        self._edit(self.unit, {"colour": "Black"})
        self._edit(self.unit, {"ram": "8GB"})
        self.unit.refresh_from_db()
        self.assertEqual((self.unit.colour, self.unit.ram), ("Black", "8GB"))

    def test_the_imei_and_status_cannot_be_changed_here(self):
        self._edit(self.unit, {"serial": "999999999999999", "status": "SOLD"})
        self.unit.refresh_from_db()
        self.assertEqual((self.unit.serial, self.unit.status), (self.IMEI_A, "IN_STOCK"))

    def test_second_imei_of_another_phone_is_refused(self):
        res = self._edit(self.unit, {"imei2": self.IMEI_B})
        self.assertEqual(res.status_code, 400)
        self.assertIn("imei2", res.json()["fields"])

    def test_second_imei_equal_to_the_first_is_refused(self):
        res = self._edit(self.unit, {"imei2": self.IMEI_A})
        self.assertEqual(res.status_code, 400)

    def test_unknown_phone_is_404(self):
        res = self.client_.post(
            reverse("finance:scan_bill_unit", args=["00000000-0000-0000-0000-000000000000"]),
            data="{}", content_type="application/json",
        )
        self.assertEqual(res.status_code, 404)

    def test_closed_when_scanning_is_off(self):
        Organization.objects.filter(pk=self.org.pk).update(barcode_scanning_enabled=False)
        res = self._edit(self.unit, {"colour": "Black"})
        self.assertEqual(res.status_code, 403)


class ItemDetailsAndTypeTests(ScanBillingFixtures, TestCase):
    """An item carries its own details and opening stock, and what is recorded follows the inventory type."""

    IMEI = "356938035643809"

    def _create(self, body):
        return self._post_json("finance:scan_bill_product", body)

    def test_a_phone_item_carries_brand_colour_ram_and_storage(self):
        res = self._create({
            "barcode": "EAN13V10BLU", "name": "V10", "sale_price": "15000", "purchase_price": "12000",
            "brand": "Vivo", "colour": "Blue", "ram": "8GB", "storage": "128GB",
        })
        self.assertEqual(res.status_code, 201, res.content)
        body = res.json()["product"]
        self.assertEqual((body["brand"], body["colour"], body["ram"], body["storage"]), ("Vivo", "Blue", "8GB", "128GB"))
        self.assertEqual(body["attributes_text"], "Vivo · Blue · RAM 8GB · 128GB")
        self.assertEqual(body["attribute_keys"], ["brand", "colour", "ram", "storage", "extra"])

    def test_buying_a_phone_fills_its_details_from_the_item(self):
        product = Product.objects.create(
            organization=self.org, barcode="EAN13V10BLU", name="V10", category=self.phones,
            brand="Vivo", colour="Blue", ram="8GB", storage="128GB",
            sale_price=Decimal("15000"), purchase_price=Decimal("12000"),
        )
        _sb_mod.save_bill(
            kind="purchase", date=datetime.date(2026, 10, 20), payment_mode="CASH", bank_account=None,
            category=self.purchase_cat, vendor="", min_date=None, created_by_email="",
            lines=[{"product_id": str(product.pk), "serials": [self.IMEI]}],
        )
        unit = StockUnit.objects.get(serial=self.IMEI)
        self.assertEqual((unit.brand, unit.model_name, unit.colour, unit.ram, unit.storage),
                         ("Vivo", "V10", "Blue", "8GB", "128GB"))

    def test_details_typed_on_the_line_win_over_the_item(self):
        product = Product.objects.create(
            organization=self.org, barcode="EAN13V10BLU", name="V10", category=self.phones,
            colour="Blue", sale_price=Decimal("15000"), purchase_price=Decimal("12000"),
        )
        _sb_mod.save_bill(
            kind="purchase", date=datetime.date(2026, 10, 20), payment_mode="CASH", bank_account=None,
            category=self.purchase_cat, vendor="", min_date=None, created_by_email="",
            lines=[{"product_id": str(product.pk), "serials": [self.IMEI], "details": {"colour": "Green"}}],
        )
        self.assertEqual(StockUnit.objects.get(serial=self.IMEI).colour, "Green")

    def test_opening_stock_counts_in_the_inventory(self):
        from apps.finance import inventory
        res = self._create({
            "barcode": "TEE1", "name": "Cotton tee", "sale_price": "499", "purchase_price": "250",
            "opening_stock": "20",
        })
        self.assertEqual(res.status_code, 201, res.content)
        set_current_organization(self.org)  # the request above cleared it
        tee = Product.objects.get(barcode="TEE1")
        _sb_mod.save_bill(
            kind="purchase", date=datetime.date(2026, 10, 20), payment_mode="CASH", bank_account=None,
            category=self.purchase_cat, vendor="", min_date=None, created_by_email="",
            lines=[{"product_id": str(tee.pk), "quantity": 5}],
        )
        _sb_mod.save_bill(
            kind="sale", date=datetime.date(2026, 10, 21), payment_mode="CASH", bank_account=None,
            category=self.channel, vendor="", min_date=None, created_by_email="",
            lines=[{"product_id": str(tee.pk), "quantity": 8}],
        )
        row = next(r for r in inventory.inventory_rows() if r["id"] == str(tee.pk))
        self.assertEqual((row["opening"], row["purchased"], row["sold"], row["in_stock"]), (20, 5, 8, 17))

    def test_opening_quantity_must_be_a_whole_number(self):
        res = self._create({"barcode": "TEE2", "name": "Tee", "opening_stock": "two"})
        self.assertEqual(res.status_code, 400)
        self.assertIn("opening_stock", res.json()["fields"])
        res = self._create({"barcode": "TEE3", "name": "Tee", "opening_stock": "-4"})
        self.assertEqual(res.status_code, 400)

    def test_item_details_can_be_edited_and_cleared(self):
        res = self._post_json_to(
            reverse("finance:scan_bill_product_detail", args=[self.case.pk]),
            {"name": "Back cover", "colour": "Red", "ram": "", "opening_stock": "7"},
        )
        self.assertEqual(res.status_code, 200, res.content)
        self.case.refresh_from_db()
        self.assertEqual((self.case.colour, self.case.opening_stock), ("Red", 7))

    def _post_json_to(self, url, body):
        return self.client_.post(url, data=json.dumps(body), content_type="application/json")

    def _other_type_org(self, kind):
        org = Organization.objects.create(
            name=f"{kind} shop", slug=f"{kind.lower()}-shop", organization_code=f"T{kind[:5]}",
            storage_mode=Organization.StorageMode.OUR_DATABASE, barcode_scanning_enabled=True,
            inventory_type=kind,
        )
        set_current_organization(org)
        return org

    def test_clothes_organization_does_not_track_imeis(self):
        org = self._other_type_org(Organization.InventoryType.CLOTHES)
        channel = Category.objects.create(organization=org, kind=Category.Kind.SALES, name="Store")
        buy = Category.objects.create(organization=org, kind=Category.Kind.PURCHASE, name="Stock")
        shirt = Product.objects.create(
            organization=org, barcode="SHIRT1", name="Shirt", brand="Raymond", size="M", colour="Navy",
            sale_price=Decimal("1200"), purchase_price=Decimal("700"),
        )
        _sb_mod.save_bill(
            kind="purchase", date=datetime.date(2026, 10, 20), payment_mode="CASH", bank_account=None,
            category=buy, vendor="", min_date=None, created_by_email="",
            lines=[{"product_id": str(shirt.pk), "quantity": 6, "serials": ["356938035643809"]}],
        )
        self.assertFalse(StockUnit.objects.exists())  # IMEIs are ignored; it is plain quantity
        self.assertFalse(_sb_mod.is_tracked(shirt))
        self.assertEqual(_sb_mod.attributes_text(shirt), "Raymond · Size M · Navy")
        self.assertEqual(_sb_mod.resolve_code("356938035643809"), (None, None))
        _sb_mod.save_bill(
            kind="sale", date=datetime.date(2026, 10, 21), payment_mode="CASH", bank_account=None,
            category=channel, vendor="", min_date=None, created_by_email="",
            lines=[{"product_id": str(shirt.pk), "quantity": 2}],
        )
        from apps.finance import inventory
        row = next(r for r in inventory.inventory_rows() if r["id"] == str(shirt.pk))
        self.assertEqual((row["purchased"], row["sold"], row["in_stock"]), (6, 2, 4))

    def test_each_type_asks_for_its_own_fields(self):
        keys = {}
        for kind in (Organization.InventoryType.MOBILES, Organization.InventoryType.CLOTHES,
                     Organization.InventoryType.OTHER):
            org = self._other_type_org(kind) if kind != Organization.InventoryType.MOBILES else self.org
            if kind == Organization.InventoryType.MOBILES:
                set_current_organization(org)
            item = Product(organization=org, barcode=f"K{kind}", name="X")
            keys[kind] = _sb_mod.product_json(item)["attribute_keys"]
        self.assertEqual(keys["MOBILES"], ["brand", "colour", "ram", "storage", "extra"])
        self.assertEqual(keys["CLOTHES"], ["brand", "size", "colour", "extra"])
        self.assertEqual(keys["OTHER"], ["brand", "extra"])


class SetupInventoryTests(ScanBillingFixtures, TestCase):
    """The inventory list on the Setup page, and phones added there as opening stock."""

    IMEI = "356938035643809"

    def _unit_add(self, body):
        return self.client_.post(
            reverse("finance:scan_bill_unit_add"), data=json.dumps(body), content_type="application/json",
        )

    def test_setup_page_lists_the_inventory_for_a_switched_on_organization(self):
        res = self.client_.get(reverse("finance:settings"))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, 'id="inventory-setup"')
        self.assertContains(res, "Redmi 13")  # in the item data the page builds the table from

    def test_setup_page_has_no_inventory_without_the_switch(self):
        Organization.objects.filter(pk=self.org.pk).update(barcode_scanning_enabled=False)
        res = self.client_.get(reverse("finance:settings"))
        self.assertEqual(res.status_code, 200)
        self.assertNotContains(res, "inventory-setup")

    def test_setup_page_has_no_inventory_for_another_organization(self):
        other = Organization.objects.create(
            name="City Hospital", slug="city-hospital-2", organization_code="CTYHSP2",
            storage_mode=Organization.StorageMode.OUR_DATABASE,
        )
        user = User.objects.create_user(
            email="owner@hospital2.example", password="testpass-123", organization=other, role=User.Role.OWNER,
        )
        client = Client()
        client.force_login(user)
        self.assertNotContains(client.get(reverse("finance:settings")), "inventory-setup")

    def test_an_opening_phone_is_added_without_a_purchase_bill(self):
        res = self._unit_add({
            "product": str(self.phone.pk), "serial": self.IMEI, "colour": "Black", "ram": "8GB",
        })
        self.assertEqual(res.status_code, 201, res.content)
        set_current_organization(self.org)
        unit = StockUnit.objects.get(serial=self.IMEI)
        self.assertEqual((unit.status, unit.purchase_bill_no, unit.colour, unit.ram), ("IN_STOCK", "", "Black", "8GB"))
        self.assertFalse(PurchaseEntry.objects.exists())  # nothing added to the books

    def test_an_opening_phone_takes_its_details_from_the_item(self):
        self.phone.brand, self.phone.storage = "Redmi", "128GB"
        self.phone.save()
        self._unit_add({"product": str(self.phone.pk), "serial": self.IMEI})
        set_current_organization(self.org)
        unit = StockUnit.objects.get(serial=self.IMEI)
        self.assertEqual((unit.brand, unit.model_name, unit.storage), ("Redmi", "Redmi 13", "128GB"))

    def test_an_opening_phone_needs_an_item_and_an_imei(self):
        self.assertEqual(self._unit_add({"serial": self.IMEI}).status_code, 400)
        res = self._unit_add({"product": str(self.phone.pk), "serial": ""})
        self.assertEqual(res.status_code, 400)
        self.assertIn("serial", res.json()["fields"])

    def test_an_imei_already_recorded_is_refused(self):
        self._unit_add({"product": str(self.phone.pk), "serial": self.IMEI})
        res = self._unit_add({"product": str(self.phone.pk), "serial": self.IMEI})
        self.assertEqual(res.status_code, 400)
        self.assertIn("serial", res.json()["fields"])

    def test_opening_phones_are_only_for_a_mobiles_organization(self):
        Organization.objects.filter(pk=self.org.pk).update(inventory_type=Organization.InventoryType.CLOTHES)
        res = self._unit_add({"product": str(self.phone.pk), "serial": self.IMEI})
        self.assertEqual(res.status_code, 400)

    def test_an_opening_phone_is_closed_when_scanning_is_off(self):
        Organization.objects.filter(pk=self.org.pk).update(barcode_scanning_enabled=False)
        self.assertEqual(self._unit_add({"product": str(self.phone.pk), "serial": self.IMEI}).status_code, 403)

    def test_an_opening_phone_counts_in_stock_and_sells_like_any_other(self):
        from apps.finance import inventory
        self._unit_add({"product": str(self.phone.pk), "serial": self.IMEI})
        set_current_organization(self.org)
        row = next(r for r in inventory.inventory_rows() if r["id"] == str(self.phone.pk))
        self.assertEqual(row["in_stock"], 1)
        _sb_mod.save_bill(
            kind="sale", date=datetime.date(2026, 10, 21), payment_mode="CASH", bank_account=None,
            category=self.channel, vendor="", min_date=None, created_by_email="",
            lines=[{"product_id": str(self.phone.pk), "serials": [self.IMEI]}],
        )
        self.assertEqual(StockUnit.objects.get(serial=self.IMEI).status, "SOLD")


class StructureItemTests(ScanBillingFixtures, TestCase):
    """Items follow the organization's category structure: a path through the group levels, and
    optional values from the common levels, whose lists are shared by every item."""

    MOBILE_LEVELS = [
        {"name": "Mobile", "common": False}, {"name": "Brand", "common": False},
        {"name": "Model name", "common": False},
        {"name": "Colour", "common": True}, {"name": "RAM", "common": True}, {"name": "Storage", "common": True},
    ]

    def _use(self, levels):
        self.org.levels = levels
        set_current_organization(self.org)

    def _create(self, **body):
        body.setdefault("sale_price", "1000")
        body.setdefault("purchase_price", "800")
        return self._post_json("finance:scan_bill_product", body)

    def test_three_levels_by_default_make_a_category_and_a_sub_category(self):
        res = self._create(barcode="D1", name="Widget", path=["Gadgets", "Small"])
        self.assertEqual(res.status_code, 201, res.content)
        product = Product.objects.get(barcode="D1")
        self.assertEqual(product.category.name, "Gadgets")
        self.assertEqual(product.category.kind, Category.Kind.PRODUCT)
        self.assertEqual(product.subcategory.name, "Small")
        self.assertEqual(res.json()["product"]["path"], ["Gadgets", "Small"])

    def test_a_mobile_structure_takes_brand_and_common_values(self):
        self.org.levels = self.MOBILE_LEVELS
        Organization.objects.filter(pk=self.org.pk).update(levels=self.MOBILE_LEVELS)
        res = self._create(
            barcode="M1", name="iPhone 15", path=["Mobile", "Apple"],
            common={"Colour": "Black", "RAM": "6GB", "Storage": "128GB"},
        )
        self.assertEqual(res.status_code, 201, res.content)
        body = res.json()["product"]
        self.assertEqual(body["path"], ["Mobile", "Apple"])
        self.assertEqual(body["common"], {"Colour": "Black", "RAM": "6GB", "Storage": "128GB"})
        self.assertEqual(body["attributes_text"], "Black · RAM 6GB · 128GB")
        set_current_organization(self.org)
        product = Product.objects.get(barcode="M1")
        self.assertEqual((product.colour, product.ram, product.storage), ("Black", "6GB", "128GB"))

    def test_a_deeper_structure_makes_a_chain_of_groups(self):
        levels = [{"name": f"Level {i}", "common": False} for i in range(1, 7)]
        self.org.levels = levels
        Organization.objects.filter(pk=self.org.pk).update(levels=levels)
        res = self._create(barcode="DEEP1", name="Thing", path=["A", "B", "C", "D", "E"])
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(res.json()["product"]["path"], ["A", "B", "C", "D", "E"])
        set_current_organization(self.org)
        leaf = Product.objects.get(barcode="DEEP1").subcategory
        depth, node = 0, leaf
        while node.parent_id:
            depth += 1
            node = node.parent
        self.assertEqual((leaf.name, depth), ("E", 3))

    def test_more_names_than_levels_is_refused(self):
        res = self._create(barcode="X1", name="Thing", path=["A", "B", "C"])  # default: two group levels
        self.assertEqual(res.status_code, 400)
        self.assertIn("path", res.json()["fields"])

    def test_a_gap_in_the_path_is_refused(self):
        res = self._create(barcode="X2", name="Thing", path=["A", "", "C"])
        self.assertEqual(res.status_code, 400)

    def test_the_same_group_is_reused_not_repeated(self):
        self._create(barcode="R1", name="One", path=["Gadgets", "Small"])
        self._create(barcode="R2", name="Two", path=["gadgets", "SMALL"])
        set_current_organization(self.org)
        self.assertEqual(Category.objects.filter(kind=Category.Kind.PRODUCT, name__iexact="gadgets").count(), 1)
        self.assertEqual(Subcategory.objects.filter(name__iexact="small").count(), 1)
        self.assertEqual(Product.objects.get(barcode="R1").subcategory_id, Product.objects.get(barcode="R2").subcategory_id)

    def test_a_common_level_with_no_column_is_kept_with_the_item(self):
        levels = [{"name": "Category", "common": False}, {"name": "Item", "common": False},
                  {"name": "Fabric", "common": True}]
        self.org.levels = levels
        Organization.objects.filter(pk=self.org.pk).update(levels=levels)
        res = self._create(barcode="F1", name="Shirt", path=["Tops"], common={"Fabric": "Cotton"})
        self.assertEqual(res.json()["product"]["common"], {"Fabric": "Cotton"})
        self.assertEqual(res.json()["product"]["attributes_text"], "Cotton")

    def test_a_value_for_a_level_that_is_not_common_is_ignored(self):
        res = self._create(barcode="I1", name="Thing", path=["Gadgets"], common={"Colour": "Black"})
        self.assertEqual(res.json()["product"]["common"], {})  # the default structure has no common levels

    def test_the_shared_lists_collect_every_value_used(self):
        Organization.objects.filter(pk=self.org.pk).update(levels=self.MOBILE_LEVELS)
        self._create(barcode="S1", name="A", path=["Mobile", "Apple"], common={"Colour": "Black", "RAM": "6GB"})
        self._create(barcode="S2", name="B", path=["Mobile", "Vivo"], common={"Colour": "Blue", "RAM": "6GB"})
        self.org.levels = self.MOBILE_LEVELS
        set_current_organization(self.org)
        options = _sb_mod.common_options()
        self.assertEqual(options["Colour"], ["Black", "Blue"])
        self.assertEqual(options["RAM"], ["6GB"])  # the same value is only kept once
        self.assertEqual(options["Storage"], [])

    def test_common_values_are_the_same_list_for_every_group(self):
        Organization.objects.filter(pk=self.org.pk).update(levels=self.MOBILE_LEVELS)
        self._create(barcode="G1", name="A", path=["Mobile", "Apple"], common={"Colour": "Black"})
        self._create(barcode="G2", name="B", path=["Mobile", "Vivo"], common={"Colour": "Black"})
        self.org.levels = self.MOBILE_LEVELS
        set_current_organization(self.org)
        self.assertEqual(CommonOption.objects.filter(level="Colour").count(), 1)

    def test_editing_an_item_can_move_it_and_change_its_common_values(self):
        Organization.objects.filter(pk=self.org.pk).update(levels=self.MOBILE_LEVELS)
        self._create(barcode="E1", name="Phone", path=["Mobile", "Apple"], common={"Colour": "Black"})
        set_current_organization(self.org)
        product = Product.objects.get(barcode="E1")
        res = self._post_json_to(
            reverse("finance:scan_bill_product_detail", args=[product.pk]),
            {"name": "Phone", "path": ["Mobile", "Vivo"], "common": {"Colour": "Blue", "Storage": "64GB"}},
        )
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.json()["product"]["path"], ["Mobile", "Vivo"])
        self.assertEqual(res.json()["product"]["common"], {"Colour": "Blue", "Storage": "64GB"})

    def test_an_empty_path_clears_the_groups(self):
        self._create(barcode="C1", name="Thing", path=["Gadgets", "Small"])
        set_current_organization(self.org)
        product = Product.objects.get(barcode="C1")
        res = self._post_json_to(
            reverse("finance:scan_bill_product_detail", args=[product.pk]), {"name": "Thing", "path": []},
        )
        self.assertEqual(res.json()["product"]["path"], [])

    def _post_json_to(self, url, body):
        return self.client_.post(url, data=json.dumps(body), content_type="application/json")

    def test_the_scan_page_and_setup_carry_the_structure(self):
        Organization.objects.filter(pk=self.org.pk).update(levels=self.MOBILE_LEVELS)
        page = self.client_.get(reverse("finance:scan_bill"), {"type": "sale"})
        self.assertContains(page, "Model name")
        self.assertContains(page, "pathOptions")
        setup = self.client_.get(reverse("finance:settings"))
        self.assertContains(setup, "commonRows")

    def test_a_common_value_can_be_added_and_removed_from_the_lists(self):
        Organization.objects.filter(pk=self.org.pk).update(levels=self.MOBILE_LEVELS)
        res = self._post_json("finance:common_option_add", {"level": "Colour", "value": "Midnight Blue"})
        self.assertEqual(res.status_code, 201, res.content)
        option_id = res.json()["option"]["id"]
        res = self.client_.post(reverse("finance:common_option_remove", args=[option_id]))
        self.assertEqual(res.status_code, 200)
        self.org.levels = self.MOBILE_LEVELS
        set_current_organization(self.org)
        self.assertFalse(CommonOption.objects.filter(level="Colour").exists())

    def test_a_value_cannot_be_added_to_a_level_that_is_not_common(self):
        Organization.objects.filter(pk=self.org.pk).update(levels=self.MOBILE_LEVELS)
        res = self._post_json("finance:common_option_add", {"level": "Brand", "value": "Apple"})
        self.assertEqual(res.status_code, 400)
        res = self._post_json("finance:common_option_add", {"level": "Colour", "value": "  "})
        self.assertEqual(res.status_code, 400)

    def test_the_lists_are_closed_when_scanning_is_off(self):
        Organization.objects.filter(pk=self.org.pk).update(barcode_scanning_enabled=False)
        res = self._post_json("finance:common_option_add", {"level": "Colour", "value": "Red"})
        self.assertEqual(res.status_code, 403)
