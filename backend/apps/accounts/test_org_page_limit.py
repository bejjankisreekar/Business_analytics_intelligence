from unittest import mock

from django.test import Client, TestCase
from django.urls import reverse

from apps.accounts.manager_pages import landing_url, org_pages_for
from apps.accounts.models import User
from apps.organizations.models import Organization
from apps.superadmin.forms import ClientProfileForm

LIMITED = ["daily_report", "daily_summary", "monthly_summary", "customer_requests"]


@mock.patch("apps.billing.services.is_account_suspended", return_value=False)
@mock.patch("apps.billing.services.has_active_access", return_value=True)
class OrganizationPageLimitTests(TestCase):
    """A superadmin can limit a whole organization to a few pages; others are untouched."""

    def setUp(self):
        self.org = Organization.objects.create(
            name="Corner Shop", slug="corner-shop", organization_code="CORNER01",
            storage_mode=Organization.StorageMode.OUR_DATABASE,
            business_type=Organization.BusinessType.RETAIL_ECOMMERCE,
            allowed_pages=LIMITED,
        )
        self.owner = User.objects.create_user(
            email="o@corner.example", password="x-pass-123", organization=self.org, role=User.Role.OWNER)
        self.client_ = Client()
        self.client_.force_login(self.owner)

    def test_the_four_pages_open(self, *_):
        for name in ("finance:daily_report", "finance:daily_summary", "finance:monthly_summary", "quote_requests:inbox"):
            self.assertEqual(self.client_.get(reverse(name)).status_code, 200, name)

    def test_every_other_page_is_sent_back_to_an_allowed_one(self, *_):
        for name in ("finance:dashboard", "finance:analytics", "finance:sales_intelligence",
                     "finance:cash_position", "finance:ledgers", "finance:reports", "finance:settings",
                     "finance:category_statement"):
            resp = self.client_.get(reverse(name))
            self.assertRedirects(resp, reverse("finance:daily_report"), fetch_redirect_response=False, msg_prefix=name)

    def test_menu_shows_only_the_allowed_pages(self, *_):
        page = self.client_.get(reverse("finance:daily_report"))
        for label in ("Daily Book", "Daily Performance", "Monthly Summary", "Customer Requests"):
            self.assertContains(page, label)
        for label in ("Business Analytics", "Revenue Insights", "Cost Insights", "Cash Position", "Ledgers",
                      "Financial Reports", "Category Statement"):
            self.assertNotContains(page, label)
        # No empty section headings are left behind.
        self.assertNotContains(page, 'app-nav-label">Overview<')
        self.assertNotContains(page, 'app-nav-label">Intelligence<')

    def test_login_lands_on_an_allowed_page(self, *_):
        self.assertEqual(landing_url(self.owner), reverse("finance:daily_report"))
        resp = Client().post(reverse("accounts:login"), {"identifier": "o@corner.example", "password": "x-pass-123"})
        if resp.status_code == 302:
            self.assertNotEqual(resp["Location"], reverse("finance:dashboard"))

    def test_customer_requests_can_be_left_out(self, *_):
        self.org.allowed_pages = ["daily_report", "monthly_summary"]
        self.org.save()
        resp = self.client_.get(reverse("quote_requests:inbox"))
        self.assertRedirects(resp, reverse("finance:daily_report"), fetch_redirect_response=False)
        self.assertNotContains(self.client_.get(reverse("finance:daily_report")), "Customer Requests")
        self.assertEqual(self.client_.get(reverse("quote_requests:qr")).status_code, 302)

    def test_the_customers_open_pages_are_never_limited(self, *_):
        from apps.quote_requests.models import QuoteRequestSettings

        self.org.allowed_pages = ["daily_report"]
        self.org.save()
        QuoteRequestSettings.objects.create(organization=self.org, is_enabled=True)
        self.assertEqual(Client().get("/request/corner-shop/").status_code, 200)

    def test_empty_list_means_full_access(self, *_):
        self.org.allowed_pages = []
        self.org.save()
        self.assertIsNone(org_pages_for(self.org))
        self.assertEqual(self.client_.get(reverse("finance:dashboard")).status_code, 200)
        self.assertEqual(self.client_.get(reverse("finance:analytics")).status_code, 200)

    def test_unknown_keys_do_not_lock_the_organization_out(self, *_):
        self.org.allowed_pages = ["not-a-page"]
        self.org.save()
        self.assertIsNone(org_pages_for(self.org))

    def test_a_manager_is_limited_by_both_lists(self, *_):
        manager = User.objects.create_user(
            email="m@corner.example", password="x-pass-123", organization=self.org, role=User.Role.MANAGER)
        manager.manager_pages = ["daily_report", "dashboard", "analytics"]   # owner ticked more than the limit
        manager.save()
        c = Client()
        c.force_login(manager)
        self.assertEqual(c.get(reverse("finance:daily_report")).status_code, 200)
        self.assertEqual(c.get(reverse("finance:dashboard")).status_code, 302)
        self.assertEqual(c.get(reverse("finance:analytics")).status_code, 302)
        self.assertEqual(c.get(reverse("finance:monthly_summary")).status_code, 302)   # not ticked for this manager

    def test_other_organizations_are_unaffected(self, *_):
        other = Organization.objects.create(
            name="Big Shop", slug="big-shop", organization_code="BIGSHOP1",
            storage_mode=Organization.StorageMode.OUR_DATABASE,
            business_type=Organization.BusinessType.RETAIL_ECOMMERCE)
        user = User.objects.create_user(
            email="o@big.example", password="x-pass-123", organization=other, role=User.Role.OWNER)
        c = Client()
        c.force_login(user)
        self.assertEqual(c.get(reverse("finance:dashboard")).status_code, 200)
        self.assertContains(c.get(reverse("finance:daily_report")), "Business Analytics")


class SuperadminPageAccessFormTests(TestCase):
    def test_form_has_the_page_choices_and_saves_the_selection(self):
        org = Organization.objects.create(
            name="Corner Shop", slug="corner-shop", organization_code="CORNER01",
            storage_mode=Organization.StorageMode.OUR_DATABASE)
        form = ClientProfileForm(instance=org)
        keys = {k for k, _ in form.fields["allowed_pages"].choices}
        self.assertTrue({"daily_report", "daily_summary", "monthly_summary", "customer_requests"} <= keys)

        data = {k: v for k, v in form.initial.items() if v not in (None, [])}
        data["allowed_pages"] = LIMITED
        bound = ClientProfileForm(data, instance=org)
        self.assertTrue(bound.is_valid(), bound.errors)
        bound.save()
        org.refresh_from_db()
        self.assertEqual(sorted(org.allowed_pages), sorted(LIMITED))

        data["allowed_pages"] = []
        bound = ClientProfileForm(data, instance=org)
        self.assertTrue(bound.is_valid(), bound.errors)
        bound.save()
        org.refresh_from_db()
        self.assertEqual(org.allowed_pages, [])


@mock.patch("apps.billing.services.is_account_suspended", return_value=False)
@mock.patch("apps.billing.services.has_active_access", return_value=True)
class LitePlanPageLimitTests(TestCase):
    def test_lite_plan_limits_org_to_three_pages(self, *_):
        from apps.billing.models import Plan, Subscription

        lite = Plan.objects.get(slug="lite")
        self.assertEqual(set(lite.allowed_pages), {"customer_requests", "daily_report", "dashboard"})
        org = Organization.objects.create(
            name="Lite Shop", slug="lite-shop", organization_code="LITE0001",
            storage_mode=Organization.StorageMode.OUR_DATABASE,
            business_type=Organization.BusinessType.RETAIL_ECOMMERCE,
        )
        self.assertIsNone(org_pages_for(org))
        Subscription.objects.create(organization=org, plan=lite, is_current=True)
        org = Organization.objects.get(pk=org.pk)
        self.assertEqual(org_pages_for(org), frozenset(lite.allowed_pages))
