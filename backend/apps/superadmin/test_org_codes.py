from django.test import Client, TestCase

from apps.organizations.models import Organization, OrganizationCategory

from .tests import DATABASES, ENV, _make_superadmin


class OrgCodeTests(TestCase):
    """Codes for kinds of organization (HOSP, MOBS ...) that a superadmin gives to clients."""

    databases = DATABASES

    def setUp(self):
        self.superadmin = _make_superadmin("codes@test.local")
        self.c = Client()
        self.c.force_login(self.superadmin)
        self.org = Organization.objects.using(ENV).create(
            name="Corner Mobiles", slug="corner-mobiles", organization_code="CORNERM1",
            storage_mode=Organization.StorageMode.OUR_DATABASE,
            business_type=Organization.BusinessType.RETAIL_ECOMMERCE,
        )

    def _category(self, code):
        return OrganizationCategory.objects.using(ENV).get(code=code)

    def test_starter_codes_exist(self):
        hosp, mobs, clth = self._category("HOSP"), self._category("MOBS"), self._category("CLTH")
        self.assertFalse(hosp.barcode_scanning_enabled)
        self.assertTrue(mobs.barcode_scanning_enabled)
        self.assertEqual(mobs.inventory_type, "MOBILES")
        self.assertEqual(clth.inventory_type, "CLOTHES")
        for code in ("DIAG", "REST", "GENR"):
            self._category(code)

    def test_the_list_page_shows_the_codes(self):
        page = self.c.get(f"/superadmin/{ENV}/org-codes/")
        self.assertEqual(page.status_code, 200)
        for text in ("HOSP", "MOBS", "REST", "Hospitals", "Mobile shops"):
            self.assertContains(page, text)

    def test_a_new_code_can_be_created(self):
        resp = self.c.post(f"/superadmin/{ENV}/org-codes/create/", {
            "code": "pharm", "name": "Pharmacies", "barcode_scanning_enabled": "on",
            "inventory_type": "OTHER", "notes": "", "is_active": "on",
        })
        self.assertEqual(resp.status_code, 302)
        created = self._category("PHARM")  # saved in capitals
        self.assertEqual((created.name, created.barcode_scanning_enabled), ("Pharmacies", True))

    def test_a_bad_or_duplicate_code_is_refused(self):
        base = {"name": "Thing", "inventory_type": "OTHER", "is_active": "on"}
        page = self.c.post(f"/superadmin/{ENV}/org-codes/create/", {**base, "code": "H O S"})
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Use 2 to 12 letters or digits")
        page = self.c.post(f"/superadmin/{ENV}/org-codes/create/", {**base, "code": "hosp"})
        self.assertContains(page, "already used")
        self.assertFalse(OrganizationCategory.objects.using(ENV).filter(name="Thing").exists())

    def test_assigning_a_code_gives_the_client_its_features(self):
        mobs = self._category("MOBS")
        resp = self.c.post(f"/superadmin/{ENV}/{self.org.pk}/org-code/", {"category": mobs.pk})
        self.assertEqual(resp.status_code, 302)
        self.org.refresh_from_db(using=ENV)
        self.assertEqual(self.org.category_id, mobs.pk)
        self.assertTrue(self.org.barcode_scanning_enabled)
        self.assertEqual(self.org.inventory_type, "MOBILES")

    def test_a_hospital_code_turns_scanning_off(self):
        Organization.objects.using(ENV).filter(pk=self.org.pk).update(barcode_scanning_enabled=True)
        self.c.post(f"/superadmin/{ENV}/{self.org.pk}/org-code/", {"category": self._category("HOSP").pk})
        self.org.refresh_from_db(using=ENV)
        self.assertFalse(self.org.barcode_scanning_enabled)

    def test_removing_the_code_leaves_the_features_as_they_are(self):
        self.c.post(f"/superadmin/{ENV}/{self.org.pk}/org-code/", {"category": self._category("CLTH").pk})
        self.c.post(f"/superadmin/{ENV}/{self.org.pk}/org-code/", {"category": ""})
        self.org.refresh_from_db(using=ENV)
        self.assertIsNone(self.org.category_id)
        self.assertTrue(self.org.barcode_scanning_enabled)
        self.assertEqual(self.org.inventory_type, "CLOTHES")

    def test_editing_a_code_updates_every_client_that_has_it(self):
        category = OrganizationCategory.objects.using(ENV).create(
            code="SHOES", name="Shoe shops", barcode_scanning_enabled=False,
        )
        other = Organization.objects.using(ENV).create(
            name="Other Shoes", slug="other-shoes", organization_code="OTHERSH1",
            storage_mode=Organization.StorageMode.OUR_DATABASE, category=category,
        )
        self.org.category = category
        self.org.save(using=ENV, update_fields=["category"])
        resp = self.c.post(f"/superadmin/{ENV}/org-codes/{category.pk}/edit/", {
            "code": "SHOES", "name": "Shoe shops", "barcode_scanning_enabled": "on",
            "inventory_type": "CLOTHES", "notes": "", "is_active": "on",
        })
        self.assertEqual(resp.status_code, 302)
        for org in (self.org, other):
            org.refresh_from_db(using=ENV)
            self.assertTrue(org.barcode_scanning_enabled)
            self.assertEqual(org.inventory_type, "CLOTHES")

    def test_the_client_page_offers_the_codes(self):
        page = self.c.get(f"/superadmin/{ENV}/{self.org.pk}/")
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Organization code")
        self.assertContains(page, "MOBS · Mobile shops")

    def test_an_inactive_code_cannot_be_given(self):
        OrganizationCategory.objects.using(ENV).filter(code="GENR").update(is_active=False)
        resp = self.c.post(f"/superadmin/{ENV}/{self.org.pk}/org-code/", {"category": self._category("GENR").pk})
        self.assertEqual(resp.status_code, 404)

    def test_the_client_list_shows_each_clients_code(self):
        self.c.post(f"/superadmin/{ENV}/{self.org.pk}/org-code/", {"category": self._category("MOBS").pk})
        page = self.c.get(f"/superadmin/{ENV}/")
        self.assertContains(page, "MOBS")


class LevelEditorTests(TestCase):
    """A superadmin sets the category levels on a code, and for one client."""

    databases = DATABASES

    def setUp(self):
        import json

        self.json = json
        self.superadmin = _make_superadmin("levels@test.local")
        self.c = Client()
        self.c.force_login(self.superadmin)
        self.org = Organization.objects.using(ENV).create(
            name="Level Shop", slug="level-shop", organization_code="LEVELS01",
            storage_mode=Organization.StorageMode.OUR_DATABASE,
        )

    def _code_post(self, code, levels):
        return self.c.post(f"/superadmin/{ENV}/org-codes/create/", {
            "code": code, "name": f"Kind {code}", "inventory_type": "OTHER", "is_active": "on",
            "levels_json": self.json.dumps(levels),
        })

    def test_the_code_page_shows_the_editor_with_presets(self):
        page = self.c.get(f"/superadmin/{ENV}/org-codes/create/")
        self.assertContains(page, "Category levels")
        self.assertContains(page, "Mobile shop: Mobile &gt; Brand &gt; Model name")
        self.assertContains(page, "lv-editor")

    def test_a_new_code_gets_three_levels_by_default(self):
        self.c.post(f"/superadmin/{ENV}/org-codes/create/", {
            "code": "PLAIN", "name": "Plain", "inventory_type": "OTHER", "is_active": "on",
        })
        category = OrganizationCategory.objects.using(ENV).get(code="PLAIN")
        self.assertEqual([l["name"] for l in category.levels], ["Category", "Sub-category", "Item"])

    def test_a_code_can_have_up_to_eight_levels(self):
        levels = [{"name": f"Level {i}", "common": i > 6} for i in range(1, 9)]
        resp = self._code_post("DEEP", levels)
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(len(OrganizationCategory.objects.using(ENV).get(code="DEEP").levels), 8)

    def test_nine_levels_one_level_or_repeated_names_are_refused(self):
        too_many = [{"name": f"L{i}", "common": False} for i in range(1, 10)]
        self.assertContains(self._code_post("BAD1", too_many), "between 2 and 8 levels")
        self.assertContains(self._code_post("BAD2", [{"name": "Only", "common": False}]), "between 2 and 8 levels")
        self.assertContains(
            self._code_post("BAD3", [{"name": "A"}, {"name": "a"}, {"name": "Item"}]), "Give each level its own name",
        )
        self.assertFalse(OrganizationCategory.objects.using(ENV).filter(code__in=["BAD1", "BAD2", "BAD3"]).exists())

    def test_editing_a_code_updates_its_clients_structure(self):
        mobs = OrganizationCategory.objects.using(ENV).get(code="MOBS")
        self.org.category = mobs
        self.org.save(using=ENV, update_fields=["category"])
        new = [{"name": "Phone", "common": False}, {"name": "Item", "common": False}, {"name": "Colour", "common": True}]
        resp = self.c.post(f"/superadmin/{ENV}/org-codes/{mobs.pk}/edit/", {
            "code": "MOBS", "name": "Mobile shops", "barcode_scanning_enabled": "on", "inventory_type": "MOBILES",
            "is_active": "on", "levels_json": self.json.dumps(new),
        })
        self.assertEqual(resp.status_code, 302)
        self.org.refresh_from_db(using=ENV)
        self.assertEqual([l["name"] for l in self.org.levels], ["Phone", "Item", "Colour"])

    def test_giving_a_client_a_code_gives_it_the_structure(self):
        mobs = OrganizationCategory.objects.using(ENV).get(code="MOBS")
        self.c.post(f"/superadmin/{ENV}/{self.org.pk}/org-code/", {"category": mobs.pk})
        self.org.refresh_from_db(using=ENV)
        self.assertEqual(len(self.org.levels), 6)

    def test_one_clients_structure_can_be_changed_by_itself(self):
        levels = [{"name": "Shelf", "common": False}, {"name": "Product", "common": False}]
        resp = self.c.post(f"/superadmin/{ENV}/{self.org.pk}/levels/", {"levels_json": self.json.dumps(levels)})
        self.assertEqual(resp.status_code, 302)
        self.org.refresh_from_db(using=ENV)
        self.assertEqual([l["name"] for l in self.org.levels], ["Shelf", "Product"])

    def test_a_bad_structure_for_one_client_is_refused(self):
        before = list(self.org.levels)
        self.c.post(f"/superadmin/{ENV}/{self.org.pk}/levels/", {"levels_json": self.json.dumps([{"name": "Only"}])})
        self.org.refresh_from_db(using=ENV)
        self.assertEqual(self.org.levels, before)

    def test_the_client_page_shows_its_structure_editor(self):
        page = self.c.get(f"/superadmin/{ENV}/{self.org.pk}/")
        self.assertContains(page, "Category structure (3 levels)")
