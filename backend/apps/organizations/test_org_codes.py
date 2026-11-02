from unittest import mock

from django.test import Client, TestCase

from apps.accounts.forms import OrganizationProfileForm
from apps.accounts.models import User
from apps.mobile_api.auth import make_token
from apps.organizations import services
from apps.organizations.models import Organization, OrganizationCategory
from apps.organizations.tenant_context import set_current_organization


class ApplyCodeTests(TestCase):
    def setUp(self):
        self.mobs = OrganizationCategory.objects.get(code="MOBS")
        self.hosp = OrganizationCategory.objects.get(code="HOSP")
        self.org = Organization.objects.create(
            name="Shop", slug="shop-codes", organization_code="SHOPCD1",
            storage_mode=Organization.StorageMode.OUR_DATABASE,
        )

    def test_applying_a_code_sets_the_features(self):
        services.apply_category(self.org, self.mobs)
        self.org.refresh_from_db()
        self.assertEqual(
            (self.org.category, self.org.barcode_scanning_enabled, self.org.inventory_type),
            (self.mobs, True, "MOBILES"),
        )

    def test_switching_to_a_hospital_code_turns_them_off(self):
        services.apply_category(self.org, self.mobs)
        services.apply_category(self.org, self.hosp)
        self.org.refresh_from_db()
        self.assertFalse(self.org.barcode_scanning_enabled)

    def test_no_code_means_no_features_by_default(self):
        self.assertIsNone(self.org.category)
        self.assertFalse(self.org.barcode_scanning_enabled)

    def test_syncing_a_code_updates_its_clients_only(self):
        other = Organization.objects.create(
            name="Not mobiles", slug="not-mobiles", organization_code="NOTMOB1",
            storage_mode=Organization.StorageMode.OUR_DATABASE,
        )
        services.apply_category(self.org, self.mobs)
        self.mobs.inventory_type = "OTHER"
        self.mobs.save()
        self.assertEqual(services.sync_category(self.mobs), 1)
        self.org.refresh_from_db()
        other.refresh_from_db()
        self.assertEqual(self.org.inventory_type, "OTHER")
        self.assertIsNone(other.category)

    def test_a_code_is_saved_in_capitals(self):
        category = OrganizationCategory.objects.create(code=" bakery ", name="Bakeries")
        self.assertEqual(category.code, "BAKERY")

    def test_existing_hospitals_were_filed_under_hosp_without_changing_their_switches(self):
        # The data migration files an organization by business type but leaves its switches alone.
        from importlib import import_module

        migration = import_module("apps.organizations.migrations.0029_seed_organization_categories")
        self.assertEqual(migration.BUSINESS_TYPE_TO_CODE["HEALTHCARE"], "HOSP")
        self.assertEqual(migration.BUSINESS_TYPE_TO_CODE["RESTAURANTS_FOOD"], "REST")


class OrganizationsNeverSeeTheirCodeTests(TestCase):
    """The code and what it is called are for a superadmin only: no page or API an organization
    uses carries them, and an organization has no form field to read or change them."""

    SECRET_CODE = "ZQXK1"
    SECRET_NAME = "Secret kind of shop"

    def setUp(self):
        category = OrganizationCategory.objects.create(
            code=self.SECRET_CODE, name=self.SECRET_NAME, barcode_scanning_enabled=True, inventory_type="MOBILES",
        )
        self.org = Organization.objects.create(
            name="Corner Mobiles", slug="corner-mobiles-codes", organization_code="CORNERC1",
            storage_mode=Organization.StorageMode.OUR_DATABASE, category=category,
            barcode_scanning_enabled=True, inventory_type="MOBILES",
        )
        set_current_organization(self.org)
        self.addCleanup(set_current_organization, None)
        self.user = User.objects.create_user(
            email="owner@codes.example", password="testpass-123", organization=self.org, role=User.Role.OWNER,
        )
        mock.patch("apps.billing.services.has_active_access", return_value=True).start()
        mock.patch("apps.billing.services.is_account_suspended", return_value=False).start()
        mock.patch("apps.billing.services.historical_window_start", return_value=None).start()
        self.addCleanup(mock.patch.stopall)
        self.client_ = Client()
        self.client_.force_login(self.user)
        self.auth = {"HTTP_AUTHORIZATION": f"Bearer {make_token(self.user)}"}

    def _assert_hidden(self, response, label):
        body = response.content.decode()
        self.assertNotIn(self.SECRET_CODE, body, label)
        self.assertNotIn(self.SECRET_NAME, body, label)

    def test_no_web_page_shows_the_code(self):
        for path in ("/app/", "/app/setup/", "/app/daily/", "/app/daily/scan-bill/?type=sale",
                     "/app/daily/scan-bill/inventory/", "/app/daily/scan-bill/labels/"):
            response = self.client_.get(path)
            self.assertEqual(response.status_code, 200, path)
            self._assert_hidden(response, path)

    def test_no_phone_app_response_carries_the_code(self):
        for path in ("/api/mobile/me/", "/api/mobile/entry-options/", "/api/mobile/inventory/",
                     "/api/mobile/stock/", "/api/mobile/dashboard/"):
            response = self.client_.get(path, **self.auth)
            self.assertEqual(response.status_code, 200, path)
            self._assert_hidden(response, path)

    def test_the_organizations_own_profile_form_has_no_code_or_feature_fields(self):
        fields = set(OrganizationProfileForm().fields)
        for name in ("category", "barcode_scanning_enabled", "inventory_type"):
            self.assertNotIn(name, fields)

    def test_an_organization_cannot_change_its_code_through_its_own_form(self):
        before = (self.org.category_id, self.org.barcode_scanning_enabled, self.org.inventory_type)
        data = {"name": "Corner Mobiles", "business_type": "OTHER", "size": "SOLO",
                "category": "999", "barcode_scanning_enabled": "", "inventory_type": "CLOTHES"}
        form = OrganizationProfileForm(data, instance=self.org)
        form.is_valid()
        form.save()
        self.org.refresh_from_db()
        self.assertEqual((self.org.category_id, self.org.barcode_scanning_enabled, self.org.inventory_type), before)

    def test_an_organization_cannot_open_the_superadmin_pages(self):
        for path in ("/superadmin/dev/org-codes/", "/superadmin/dev/org-codes/create/"):
            response = self.client_.get(path)
            self.assertIn(response.status_code, (302, 403, 404), path)
            self.assertNotIn(self.SECRET_CODE, response.content.decode(), path)


class LevelStructureTests(TestCase):
    """Category structure: 2 to 8 levels with names, some of them common. Three levels by default."""

    def _levels(self, *names, common=()):
        return [{"name": n, "common": n in common} for n in names]

    def test_the_default_is_three_levels(self):
        from apps.organizations.levels import default_levels, org_levels

        self.assertEqual([l["name"] for l in default_levels()], ["Category", "Sub-category", "Item"])
        org = Organization(name="x", slug="x-lv", organization_code="XLV1")
        self.assertEqual(len(org_levels(org)), 3)

    def test_a_valid_structure_is_cleaned(self):
        from apps.organizations.levels import clean_levels

        cleaned = clean_levels([{"name": "  Mobile ", "common": False}, {"name": "Brand"}, {"name": "Model name"},
                                {"name": "Colour", "common": True}])
        self.assertEqual([l["name"] for l in cleaned], ["Mobile", "Brand", "Model name", "Colour"])
        self.assertEqual([l["common"] for l in cleaned], [False, False, False, True])

    def test_eight_levels_are_allowed_and_nine_are_not(self):
        from django.core.exceptions import ValidationError

        from apps.organizations.levels import clean_levels

        eight = self._levels(*[f"Level {i}" for i in range(1, 9)])
        self.assertEqual(len(clean_levels(eight)), 8)
        with self.assertRaises(ValidationError):
            clean_levels(self._levels(*[f"Level {i}" for i in range(1, 10)]))

    def test_fewer_than_two_levels_is_refused(self):
        from django.core.exceptions import ValidationError

        from apps.organizations.levels import clean_levels

        with self.assertRaises(ValidationError):
            clean_levels(self._levels("Only one"))

    def test_two_levels_are_allowed(self):
        from apps.organizations.levels import clean_levels

        self.assertEqual(len(clean_levels(self._levels("Group", "Item"))), 2)

    def test_two_levels_cannot_be_the_same_name(self):
        from django.core.exceptions import ValidationError

        from apps.organizations.levels import clean_levels

        with self.assertRaises(ValidationError):
            clean_levels(self._levels("Brand", "brand", "Item"))

    def test_a_level_needs_a_name(self):
        from django.core.exceptions import ValidationError

        from apps.organizations.levels import clean_levels

        with self.assertRaises(ValidationError):
            clean_levels([{"name": "Category"}, {"name": "  "}, {"name": "Item"}])

    def test_at_least_two_levels_must_be_in_the_tree(self):
        from django.core.exceptions import ValidationError

        from apps.organizations.levels import clean_levels

        with self.assertRaises(ValidationError):
            clean_levels(self._levels("Brand", "Colour", "RAM", common=("Colour", "RAM")))

    def test_levels_split_into_groups_item_and_commons(self):
        from apps.organizations.levels import PRESETS, split_levels

        groups, item, commons = split_levels(PRESETS["mobiles"][1])
        self.assertEqual([g["name"] for g in groups], ["Mobile", "Brand"])
        self.assertEqual(item["name"], "Model name")
        self.assertEqual([c["name"] for c in commons], ["Colour", "RAM", "Storage"])

    def test_the_starter_mobile_and_clothes_codes_have_their_structures(self):
        mobs = OrganizationCategory.objects.get(code="MOBS")
        clth = OrganizationCategory.objects.get(code="CLTH")
        self.assertEqual([l["name"] for l in mobs.levels], ["Mobile", "Brand", "Model name", "Colour", "RAM", "Storage"])
        self.assertEqual([l["name"] for l in clth.levels], ["Category", "Brand", "Item", "Size", "Colour"])
        self.assertEqual(len(OrganizationCategory.objects.get(code="HOSP").levels), 3)

    def test_applying_a_code_gives_the_client_its_structure(self):
        org = Organization.objects.create(
            name="Phones", slug="phones-lv", organization_code="PHONLV1",
            storage_mode=Organization.StorageMode.OUR_DATABASE,
        )
        services.apply_category(org, OrganizationCategory.objects.get(code="MOBS"))
        org.refresh_from_db()
        self.assertEqual(len(org.levels), 6)
