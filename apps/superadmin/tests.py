"""
Alias note: authentication always reads/writes the `default` database alias
(Django's auth middleware and `authenticate()` never take an explicit
`using`), while superadmin views deliberately query the `dev`/`prod`
aliases explicitly. In real deployments `default` IS whichever of those two
is active, so this is invisible. Under Django's TestCase, each alias gets
its own isolated, uncommitted transaction even when they share the same
physical database — so a user written via `.using("dev")` is invisible to
a plain (default-alias) read in the same test. Consequences for this file:

- The superadmin account itself is always created via the plain manager
  (default alias), matching how it's actually authenticated.
- Tests that only exercise `.using(ENV)` reads/writes consistently (list,
  search, create, edit, subscription) are fine under TestCase.
- Tests that need to log in as a user created via `.using(ENV)` (proving an
  owner can actually use their new/suspended workspace) need
  TransactionTestCase, which commits for real instead of rolling back.
"""

import datetime
from decimal import Decimal

from django.db.models import ProtectedError
from django.test import Client, TestCase, TransactionTestCase

from apps.accounts.models import User
from apps.billing import invoicing
from apps.billing import payments as payment_services
from apps.billing import services as billing_services
from apps.billing.models import Invoice, Payment, Plan, Subscription
from apps.organizations import service_control
from apps.organizations.models import Organization, ServiceStatusChange
from apps.organizations.services import create_organization_with_tenant_schema_and_admin, delete_organization_and_tenant

ENV = "dev"
DATABASES = {"default", "dev", "prod"}


def _make_superadmin(email="sa@test.local"):
    return User.objects.create_user(
        email=email, password="pass12345", role=User.Role.SUPER_ADMIN, is_staff=True
    )


def _make_client(env=ENV):
    org, owner = create_organization_with_tenant_schema_and_admin(
        org_data={
            "name": "Test Org",
            "business_type": Organization.BusinessType.RETAIL_ECOMMERCE,
            "contact_person": "Alice Owner",
            "contact_email": "alice@testorg.example",
        },
        admin_data={"email": "owner@testorg.example", "password": "ownerpass123", "first_name": "Alice"},
        using=env,
    )
    free_plan = Plan.objects.using(env).get(slug="free")
    billing_services.start_trial(org, free_plan, using=env)
    return org, owner


class SuperAdminAccessControlTests(TestCase):
    databases = DATABASES

    def setUp(self):
        self.superadmin = _make_superadmin()
        self.org, self.owner = _make_client()

    def tearDown(self):
        delete_organization_and_tenant(self.org, using=ENV)

    def test_anonymous_redirected_to_login(self):
        resp = Client().get("/superadmin/")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/accounts/login/", resp.headers["Location"])

    def test_non_superadmin_forbidden(self):
        regular_user = User.objects.create_user(
            email="regular@test.local", password="pass12345", role=User.Role.OWNER
        )
        c = Client()
        c.force_login(regular_user)
        resp = c.get("/superadmin/")
        self.assertEqual(resp.status_code, 403)

    def test_superadmin_can_view_overview_list_and_detail(self):
        c = Client()
        c.force_login(self.superadmin)
        self.assertEqual(c.get("/superadmin/").status_code, 200)
        self.assertEqual(c.get(f"/superadmin/{ENV}/").status_code, 200)
        self.assertEqual(c.get(f"/superadmin/{ENV}/{self.org.pk}/").status_code, 200)

    def test_unknown_environment_404s(self):
        c = Client()
        c.force_login(self.superadmin)
        resp = c.get("/superadmin/staging/")
        self.assertEqual(resp.status_code, 404)


class ClientListSearchFilterTests(TestCase):
    databases = DATABASES

    def setUp(self):
        self.superadmin = _make_superadmin("sa2@test.local")
        self.org, self.owner = _make_client()
        self.client_ = Client()
        self.client_.force_login(self.superadmin)

    def tearDown(self):
        delete_organization_and_tenant(self.org, using=ENV)

    def test_search_by_name_matches(self):
        resp = self.client_.get(f"/superadmin/{ENV}/", {"q": "Test Org"})
        self.assertContains(resp, "Test Org")

    def test_search_no_match_returns_empty(self):
        resp = self.client_.get(f"/superadmin/{ENV}/", {"q": "Nonexistent Org XYZ"})
        self.assertNotContains(resp, "Test Org")

    def test_filter_by_status_active(self):
        resp = self.client_.get(f"/superadmin/{ENV}/", {"status": "active"})
        self.assertContains(resp, "Test Org")

    def test_filter_by_status_suspended_excludes_active_org(self):
        resp = self.client_.get(f"/superadmin/{ENV}/", {"status": "suspended"})
        self.assertNotContains(resp, "Test Org")

    def test_filter_by_business_type(self):
        resp = self.client_.get(f"/superadmin/{ENV}/", {"business_type": "RETAIL_ECOMMERCE"})
        self.assertContains(resp, "Test Org")
        resp2 = self.client_.get(f"/superadmin/{ENV}/", {"business_type": "MANUFACTURING"})
        self.assertNotContains(resp2, "Test Org")

    def test_list_shows_contact_and_user_count(self):
        resp = self.client_.get(f"/superadmin/{ENV}/")
        self.assertContains(resp, "Alice Owner")
        self.assertContains(resp, "alice@testorg.example")


class ClientCreateEditTests(TestCase):
    databases = DATABASES

    def setUp(self):
        self.superadmin = _make_superadmin("sa3@test.local")
        self.client_ = Client()
        self.client_.force_login(self.superadmin)
        self._created_orgs = []

    def tearDown(self):
        for org in self._created_orgs:
            org.refresh_from_db(using=ENV)
            delete_organization_and_tenant(org, using=ENV)

    def _create_payload(self, **overrides):
        payload = {
            "name": "Created Client Co",
            "business_type": "RETAIL_ECOMMERCE",
            "industry": "Retail Goods",
            "size": "SMALL",
            "contact_person": "Bob Contact",
            "contact_email": "bob@createdclient.example",
            "contact_phone": "1234567890",
            "address": "1 Main St",
            "city": "Metropolis",
            "state": "MP",
            "country": "India",
            "tax_id": "GSTABC123",
            "website": "https://createdclient.example",
            "owner_first_name": "Bob",
            "owner_last_name": "Owner",
            "owner_email": "bob.owner@createdclient.example",
            "owner_password": "",
        }
        payload.update(overrides)
        return payload

    def test_create_client_creates_org_schema_owner_and_subscription(self):
        resp = self.client_.post(f"/superadmin/{ENV}/create/", self._create_payload())
        self.assertEqual(resp.status_code, 302)

        org = Organization.objects.using(ENV).get(name="Created Client Co")
        self._created_orgs.append(org)

        self.assertTrue(org.schema_name)
        from apps.organizations.utils import schema_exists

        self.assertTrue(schema_exists(org.schema_name, using=ENV))

        owner = User.objects.using(ENV).get(email="bob.owner@createdclient.example")
        self.assertEqual(owner.organization_id, org.id)
        self.assertEqual(owner.role, User.Role.OWNER)

        subscription = billing_services.get_current_subscription(org.id, using=ENV)
        self.assertEqual(subscription.plan.slug, "free")
        self.assertEqual(subscription.status, Subscription.Status.TRIAL)

    def test_create_client_duplicate_owner_email_rejected(self):
        self.client_.post(f"/superadmin/{ENV}/create/", self._create_payload())
        org = Organization.objects.using(ENV).get(name="Created Client Co")
        self._created_orgs.append(org)

        resp = self.client_.post(
            f"/superadmin/{ENV}/create/",
            self._create_payload(name="Another Client Co", owner_email="bob.owner@createdclient.example"),
        )
        self.assertEqual(resp.status_code, 200)  # re-renders form with error, does not redirect
        self.assertFalse(Organization.objects.using(ENV).filter(name="Another Client Co").exists())

    def test_edit_client_updates_profile_fields(self):
        self.client_.post(f"/superadmin/{ENV}/create/", self._create_payload())
        org = Organization.objects.using(ENV).get(name="Created Client Co")
        self._created_orgs.append(org)

        edit_payload = self._create_payload(name="Created Client Co", city="New City")
        for key in list(edit_payload):
            if key.startswith("owner_"):
                del edit_payload[key]  # edit form has no owner_* fields

        resp = self.client_.post(f"/superadmin/{ENV}/{org.pk}/edit/", edit_payload)
        self.assertEqual(resp.status_code, 302)
        org.refresh_from_db(using=ENV)
        self.assertEqual(org.city, "New City")


class SubscriptionAndServiceControlTests(TestCase):
    databases = DATABASES

    def setUp(self):
        self.superadmin = _make_superadmin("sa4@test.local")
        self.org, self.owner = _make_client()
        self.client_ = Client()
        self.client_.force_login(self.superadmin)
        self.paid_plan = Plan.objects.using(ENV).get(slug="enterprise")

    def tearDown(self):
        delete_organization_and_tenant(self.org, using=ENV)

    def test_subscription_create_supersedes_current_and_keeps_history(self):
        original = billing_services.get_current_subscription(self.org.id, using=ENV)

        resp = self.client_.post(
            f"/superadmin/{ENV}/{self.org.pk}/subscription/",
            {
                "plan": self.paid_plan.pk, "billing_cycle": "MONTHLY", "start_date": "2026-09-14",
                "price": "", "discount": "50", "tax": "20", "status": "ACTIVE",
                "payment_status": "PAID", "auto_renewal": "on", "notes": "upgrade",
            },
        )
        self.assertEqual(resp.status_code, 302)

        current = billing_services.get_current_subscription(self.org.id, using=ENV)
        self.assertNotEqual(current.pk, original.pk)
        self.assertEqual(current.plan_id, self.paid_plan.id)
        self.assertEqual(current.status, "ACTIVE")
        self.assertEqual(current.payment_status, "PAID")
        self.assertEqual(current.price, self.paid_plan.monthly_price)  # blank price -> plan default
        self.assertEqual(current.final_amount, self.paid_plan.monthly_price - Decimal("50") + Decimal("20"))

        # Old row must still exist, unmodified, just no longer current — history, never deleted.
        original.refresh_from_db(using=ENV)
        self.assertFalse(original.is_current)
        self.assertEqual(original.plan_id, Plan.objects.using(ENV).get(slug="free").id)

        all_rows = Subscription.objects.using(ENV).filter(organization_id=self.org.id)
        self.assertEqual(all_rows.count(), 2)

    def test_extend_trial_updates_trial_end_date(self):
        subscription = billing_services.get_current_subscription(self.org.id, using=ENV)
        new_date = (subscription.trial_end_date or datetime.date.today()) + datetime.timedelta(days=30)

        resp = self.client_.post(
            f"/superadmin/{ENV}/{self.org.pk}/subscription/extend-trial/",
            {"new_trial_end_date": new_date.isoformat()},
        )
        self.assertEqual(resp.status_code, 302)
        subscription.refresh_from_db(using=ENV)
        self.assertEqual(subscription.trial_end_date, new_date)
        # Extending is an in-place update on the SAME row, not a new history entry.
        self.assertEqual(Subscription.objects.using(ENV).filter(organization_id=self.org.id).count(), 1)

    def test_grant_complimentary_one_month(self):
        resp = self.client_.post(
            f"/superadmin/{ENV}/{self.org.pk}/subscription/complimentary/",
            {"duration": "1_month", "start_date": "2026-09-14", "plan": "", "notes": "goodwill"},
        )
        self.assertEqual(resp.status_code, 302)
        current = billing_services.get_current_subscription(self.org.id, using=ENV)
        self.assertTrue(current.is_complimentary)
        self.assertEqual(current.price, Decimal("0.00"))
        self.assertEqual(current.final_amount, Decimal("0.00"))
        self.assertEqual(current.start_date, datetime.date(2026, 9, 14))
        self.assertEqual(current.end_date, datetime.date(2026, 9, 14) + datetime.timedelta(days=30))

    def test_grant_complimentary_custom_range_requires_end_date(self):
        resp = self.client_.post(
            f"/superadmin/{ENV}/{self.org.pk}/subscription/complimentary/",
            {"duration": "custom", "start_date": "2026-09-14", "plan": "", "notes": ""},
        )
        self.assertEqual(resp.status_code, 302)  # redirects back with an error message, doesn't 500
        # No new subscription should have been created since the form was invalid.
        self.assertEqual(Subscription.objects.using(ENV).filter(organization_id=self.org.id).count(), 1)

        resp2 = self.client_.post(
            f"/superadmin/{ENV}/{self.org.pk}/subscription/complimentary/",
            {"duration": "custom", "start_date": "2026-01-01", "end_date": "2026-06-30", "plan": "", "notes": ""},
        )
        self.assertEqual(resp2.status_code, 302)
        current = billing_services.get_current_subscription(self.org.id, using=ENV)
        self.assertEqual(current.start_date, datetime.date(2026, 1, 1))
        self.assertEqual(current.end_date, datetime.date(2026, 6, 30))

    def test_plan_cannot_be_deleted_while_referenced_by_a_subscription(self):
        free_plan = Plan.objects.using(ENV).get(slug="free")
        with self.assertRaises(ProtectedError):
            free_plan.delete(using=ENV)

    def test_suspend_and_resume_flip_service_status_only(self):
        self.assertEqual(self.org.service_status, Organization.ServiceStatus.ACTIVE)
        self.assertTrue(self.org.is_active)  # account status, must stay untouched throughout

        resp = self.client_.post(
            f"/superadmin/{ENV}/{self.org.pk}/service/suspend/",
            {"reason": "PAYMENT_OVERDUE", "notes": "test", "confirm": "on"},
        )
        self.assertEqual(resp.status_code, 302)
        self.org.refresh_from_db(using=ENV)
        self.assertEqual(self.org.service_status, Organization.ServiceStatus.SUSPENDED)
        self.assertTrue(self.org.is_active)

        self.client_.post(f"/superadmin/{ENV}/{self.org.pk}/service/resume/", {"confirm": "on"})
        self.org.refresh_from_db(using=ENV)
        self.assertEqual(self.org.service_status, Organization.ServiceStatus.ACTIVE)
        self.assertTrue(self.org.is_active)


class PlanManagementTests(TestCase):
    databases = DATABASES

    def setUp(self):
        self.superadmin = _make_superadmin("sa6@test.local")
        self.client_ = Client()
        self.client_.force_login(self.superadmin)

    def tearDown(self):
        Plan.objects.using(ENV).filter(slug__startswith="qa-").delete()

    def test_create_plan_with_features(self):
        resp = self.client_.post(
            f"/superadmin/{ENV}/plans/create/",
            {
                "name": "QA Plan", "is_active": "on", "monthly_price": "199", "yearly_price": "1990",
                "trial_days": "10", "user_limit": "3", "business_limit": "",
                "features": ["business_dashboard", "sales_analytics", "ai_insights"],
            },
        )
        self.assertEqual(resp.status_code, 302)
        plan = Plan.objects.using(ENV).get(name="QA Plan")
        self.assertEqual(plan.slug, "qa-plan")  # slugified name, matches the tearDown cleanup filter
        self.assertEqual(plan.trial_days, 10)
        self.assertEqual(plan.user_limit, 3)
        self.assertIsNone(plan.business_limit)
        self.assertEqual(set(plan.features), {"business_dashboard", "sales_analytics", "ai_insights"})

    def test_edit_plan_updates_pricing_and_active_flag(self):
        create_resp = self.client_.post(
            f"/superadmin/{ENV}/plans/create/",
            {
                "name": "QA Edit Plan", "is_active": "on", "monthly_price": "100", "yearly_price": "1000",
                "trial_days": "0", "user_limit": "", "business_limit": "", "features": [],
            },
        )
        self.assertEqual(create_resp.status_code, 302)
        plan = Plan.objects.using(ENV).get(name="QA Edit Plan")

        edit_resp = self.client_.post(
            f"/superadmin/{ENV}/plans/{plan.pk}/edit/",
            {
                "name": "QA Edit Plan", "monthly_price": "150", "yearly_price": "1500",
                "trial_days": "5", "user_limit": "", "business_limit": "", "features": ["reports"],
            },
            # is_active omitted -> unchecked checkbox
        )
        self.assertEqual(edit_resp.status_code, 302)
        plan.refresh_from_db(using=ENV)
        self.assertEqual(plan.monthly_price, Decimal("150.00"))
        self.assertFalse(plan.is_active)
        self.assertEqual(plan.features, ["reports"])

    def test_toggle_plan_active(self):
        self.client_.post(
            f"/superadmin/{ENV}/plans/create/",
            {
                "name": "QA Toggle Plan", "is_active": "on", "monthly_price": "0", "yearly_price": "0",
                "trial_days": "0", "user_limit": "", "business_limit": "", "features": [],
            },
        )
        plan = Plan.objects.using(ENV).get(name="QA Toggle Plan")
        self.assertTrue(plan.is_active)

        self.client_.post(f"/superadmin/{ENV}/plans/{plan.pk}/toggle/")
        plan.refresh_from_db(using=ENV)
        self.assertFalse(plan.is_active)

        self.client_.post(f"/superadmin/{ENV}/plans/{plan.pk}/toggle/")
        plan.refresh_from_db(using=ENV)
        self.assertTrue(plan.is_active)


class CrossConnectionLoginTests(TransactionTestCase):
    """Full auth-cycle tests: a user created via `.using(ENV)` must commit
    for real to be visible to the plain (default-alias) login/session
    machinery — hence TransactionTestCase, not TestCase, here.
    """

    databases = DATABASES
    # TransactionTestCase resets state by truncating tables after each test
    # (no rollback) — without this, that truncation would also wipe the
    # Plan rows seeded by the billing migrations before the next test runs.
    serialized_rollback = True

    def setUp(self):
        self.superadmin = _make_superadmin("sa5@test.local")

    def tearDown(self):
        User.objects.filter(email="sa5@test.local").delete()

    def test_created_client_owner_can_log_in_and_use_workspace(self):
        admin_client = Client()
        admin_client.force_login(self.superadmin)

        payload = {
            "name": "Live Login Co",
            "business_type": "RETAIL_ECOMMERCE",
            "industry": "Retail",
            "size": "SMALL",
            "contact_person": "Cara Contact",
            "contact_email": "cara@livelogin.example",
            "contact_phone": "1112223333",
            "address": "9 Elm St",
            "city": "Springfield",
            "state": "SF",
            "country": "India",
            "tax_id": "GSTLIVE1",
            "website": "https://livelogin.example",
            "owner_first_name": "Cara",
            "owner_last_name": "Owner",
            "owner_email": "cara.owner@livelogin.example",
            "owner_password": "ownerlivepass1",
        }
        resp = admin_client.post(f"/superadmin/{ENV}/create/", payload)
        self.assertEqual(resp.status_code, 302)

        org = Organization.objects.using(ENV).get(name="Live Login Co")
        try:
            owner_client = Client()
            login_resp = owner_client.post(
                "/accounts/login/",
                {"email": "cara.owner@livelogin.example", "password": "ownerlivepass1"},
                follow=True,
            )
            self.assertTrue(login_resp.context["user"].is_authenticated)
            dashboard_resp = owner_client.get("/app/")
            self.assertEqual(dashboard_resp.status_code, 200)
        finally:
            delete_organization_and_tenant(org, using=ENV)

    def test_suspending_org_blocks_login_and_reactivating_restores_it(self):
        org, owner = _make_client()
        try:
            admin_client = Client()
            admin_client.force_login(self.superadmin)
            owner_client = Client()

            login_ok = owner_client.post(
                "/accounts/login/", {"email": owner.email, "password": "ownerpass123"}, follow=True
            )
            self.assertTrue(login_ok.context["user"].is_authenticated)
            owner_client.post("/accounts/logout/")

            admin_client.post(
                f"/superadmin/{ENV}/{org.pk}/service/suspend/",
                {"reason": "ADMINISTRATIVE", "confirm": "on"},
            )
            org.refresh_from_db(using=ENV)
            self.assertEqual(org.service_status, Organization.ServiceStatus.SUSPENDED)
            self.assertTrue(org.is_active)  # account status untouched by suspension

            blocked = owner_client.post(
                "/accounts/login/", {"email": owner.email, "password": "ownerpass123"}, follow=True
            )
            self.assertFalse(blocked.context["user"].is_authenticated)

            admin_client.post(f"/superadmin/{ENV}/{org.pk}/service/resume/", {"confirm": "on"})
            org.refresh_from_db(using=ENV)
            self.assertEqual(org.service_status, Organization.ServiceStatus.ACTIVE)

            restored = owner_client.post(
                "/accounts/login/", {"email": owner.email, "password": "ownerpass123"}, follow=True
            )
            self.assertTrue(restored.context["user"].is_authenticated)
        finally:
            delete_organization_and_tenant(org, using=ENV)


class PaymentAndInvoiceUITests(TestCase):
    databases = DATABASES

    def setUp(self):
        self.superadmin = _make_superadmin("sa7@test.local")
        self.org, self.owner = _make_client()
        self.client_ = Client()
        self.client_.force_login(self.superadmin)
        self.invoice = invoicing.create_invoice(self.org, using=ENV, subtotal=1000, discount=0, tax=180)
        self.payment, _ = payment_services.record_payment(
            self.org, using=ENV, invoice=self.invoice, amount=self.invoice.total,
            gateway=Payment.Gateway.MANUAL, payment_method=Payment.Method.BANK_TRANSFER,
            status=Payment.Status.SUCCESS,
        )

    def tearDown(self):
        delete_organization_and_tenant(self.org, using=ENV)

    def test_payment_list_shows_payment_and_filters_by_status(self):
        resp = self.client_.get(f"/superadmin/{ENV}/payments/")
        self.assertContains(resp, self.payment.payment_id)

        resp_success = self.client_.get(f"/superadmin/{ENV}/payments/", {"status": "SUCCESS"})
        self.assertContains(resp_success, self.payment.payment_id)

        resp_failed = self.client_.get(f"/superadmin/{ENV}/payments/", {"status": "FAILED"})
        self.assertNotContains(resp_failed, self.payment.payment_id)

    def test_payment_list_search_by_transaction_and_client(self):
        resp = self.client_.get(f"/superadmin/{ENV}/payments/", {"q": self.org.name})
        self.assertContains(resp, self.payment.payment_id)

    def test_invoice_list_shows_invoice_and_filters_by_status(self):
        self.invoice.refresh_from_db(using=ENV)  # PAID after the setUp payment
        resp = self.client_.get(f"/superadmin/{ENV}/invoices/")
        self.assertContains(resp, self.invoice.invoice_number)

        resp_paid = self.client_.get(f"/superadmin/{ENV}/invoices/", {"status": "PAID"})
        self.assertContains(resp_paid, self.invoice.invoice_number)

        resp_overdue = self.client_.get(f"/superadmin/{ENV}/invoices/", {"status": "OVERDUE"})
        self.assertNotContains(resp_overdue, self.invoice.invoice_number)

    def test_invoice_detail_shows_totals_and_linked_payment(self):
        resp = self.client_.get(f"/superadmin/{ENV}/invoices/{self.invoice.pk}/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, self.invoice.invoice_number)
        self.assertContains(resp, self.payment.payment_id)

    def test_invoice_download_returns_pdf(self):
        resp = self.client_.get(f"/superadmin/{ENV}/invoices/{self.invoice.pk}/download/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.headers["Content-Type"], "application/pdf")
        self.assertIn(self.invoice.invoice_number, resp.headers["Content-Disposition"])
        self.assertTrue(len(resp.content) > 0)

    def test_financial_history_lists_invoice_and_payment_for_client(self):
        resp = self.client_.get(f"/superadmin/{ENV}/{self.org.pk}/financial-history/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, self.invoice.invoice_number)
        self.assertContains(resp, self.payment.payment_id)

    def test_non_superadmin_cannot_access_payments_or_invoices(self):
        regular = User.objects.create_user(email="regular2@test.local", password="pw12345678", role=User.Role.OWNER)
        c = Client()
        c.force_login(regular)
        self.assertEqual(c.get(f"/superadmin/{ENV}/payments/").status_code, 403)
        self.assertEqual(c.get(f"/superadmin/{ENV}/invoices/").status_code, 403)
        self.assertEqual(c.get(f"/superadmin/{ENV}/invoices/{self.invoice.pk}/").status_code, 403)

    def test_record_payment_creates_manual_payment(self):
        resp = self.client_.post(
            f"/superadmin/{ENV}/{self.org.pk}/payments/record/",
            {
                "invoice": "", "amount": "500", "currency": "INR", "payment_method": "CASH",
                "transaction_id": "CASH-REF-TEST", "payment_date": "2026-09-14", "status": "SUCCESS", "notes": "test",
            },
        )
        self.assertEqual(resp.status_code, 302)
        payment = Payment.objects.using(ENV).get(transaction_id="CASH-REF-TEST")
        self.assertEqual(payment.gateway, Payment.Gateway.MANUAL)
        self.assertEqual(payment.amount, Decimal("500.00"))
        self.assertEqual(payment.organization_id, self.org.id)

    def test_record_payment_against_invoice_updates_invoice_status(self):
        invoice2 = invoicing.create_invoice(self.org, using=ENV, subtotal=800, discount=0, tax=0)
        resp = self.client_.post(
            f"/superadmin/{ENV}/{self.org.pk}/payments/record/",
            {
                "invoice": invoice2.pk, "amount": "800", "currency": "INR", "payment_method": "BANK_TRANSFER",
                "transaction_id": "", "payment_date": "2026-09-14", "status": "SUCCESS", "notes": "",
            },
        )
        self.assertEqual(resp.status_code, 302)
        invoice2.refresh_from_db(using=ENV)
        self.assertEqual(invoice2.status, Invoice.Status.PAID)


class PlanDiscountTests(TestCase):
    databases = DATABASES

    def setUp(self):
        self.superadmin = _make_superadmin("sa8@test.local")
        self.client_ = Client()
        self.client_.force_login(self.superadmin)

    def tearDown(self):
        Plan.objects.using(ENV).filter(slug__startswith="qa-").delete()

    def test_effective_price_rounds_to_nearest_rupee(self):
        plan = Plan.objects.using(ENV).create(
            name="QA Discount Plan", slug="qa-discount-plan",
            monthly_price=Decimal("2999.00"), monthly_discount_percent=20,
            yearly_price=Decimal("29990.00"), yearly_discount_percent=30,
        )
        self.assertEqual(plan.effective_monthly_price, Decimal("2399"))
        self.assertEqual(plan.effective_yearly_price, Decimal("20993"))

    def test_zero_discount_returns_original_price_unchanged(self):
        plan = Plan.objects.using(ENV).create(
            name="QA No Discount Plan", slug="qa-no-discount-plan", monthly_price=Decimal("999.00")
        )
        self.assertEqual(plan.effective_monthly_price, Decimal("999.00"))

    def test_edit_plan_sets_discount_percentages(self):
        plan = Plan.objects.using(ENV).create(name="QA Edit Discount Plan", slug="qa-edit-discount-plan")
        resp = self.client_.post(
            f"/superadmin/{ENV}/plans/{plan.pk}/edit/",
            {
                "name": "QA Edit Discount Plan", "is_active": "on", "monthly_price": "1000",
                "yearly_price": "10000", "monthly_discount_percent": "15", "yearly_discount_percent": "10",
                "trial_days": "0", "user_limit": "", "business_limit": "", "features": [],
            },
        )
        self.assertEqual(resp.status_code, 302)
        plan.refresh_from_db(using=ENV)
        self.assertEqual(plan.monthly_discount_percent, 15)
        self.assertEqual(plan.yearly_discount_percent, 10)


class LandingPagePricingTests(TestCase):
    # The landing page always reads the "prod" alias explicitly (public
    # pricing must reflect real production plans, never dev/test data),
    # regardless of which alias "default" currently points at.
    databases = DATABASES

    def test_landing_page_shows_active_plans_with_discount(self):
        plan = Plan.objects.using("prod").create(
            name="QA Landing Plan", slug="qa-landing-plan", is_active=True, show_on_landing_page=True,
            monthly_price=Decimal("500.00"), monthly_discount_percent=10,
        )
        try:
            resp = Client().get("/")
            self.assertContains(resp, "QA Landing Plan")
            self.assertContains(resp, "-10%")
        finally:
            plan.delete()

    def test_landing_page_hides_inactive_plans(self):
        plan = Plan.objects.using("prod").create(
            name="QA Hidden Plan", slug="qa-hidden-plan", is_active=False, show_on_landing_page=True,
        )
        try:
            resp = Client().get("/")
            self.assertNotContains(resp, "QA Hidden Plan")
        finally:
            plan.delete()

    def test_landing_page_hides_dev_only_plans(self):
        # A plan that only exists on "dev" must never leak onto the public
        # landing page, which always reads "prod".
        dev_plan = Plan.objects.using("dev").create(
            name="QA Dev Only Plan", slug="qa-dev-only-plan", is_active=True, show_on_landing_page=True,
            monthly_price=Decimal("100.00"),
        )
        try:
            resp = Client().get("/")
            self.assertNotContains(resp, "QA Dev Only Plan")
        finally:
            dev_plan.delete()

    def test_landing_page_hides_plan_not_flagged_for_landing_page(self):
        plan = Plan.objects.using("prod").create(
            name="QA Hidden Flag Plan", slug="qa-hidden-flag-plan", is_active=True, show_on_landing_page=False,
            monthly_price=Decimal("300.00"),
        )
        try:
            resp = Client().get("/")
            self.assertNotContains(resp, "QA Hidden Flag Plan")
        finally:
            plan.delete()


class ServiceControlTests(TestCase):
    """Phase 5: client service control. Covers the explicit requirements —
    confirmation + reason enforced server-side, audit log contents, data
    (users/subscription/payments/invoices) surviving suspension untouched,
    account status staying separate from service status, and superadmin
    retaining access to a suspended client.
    """

    databases = DATABASES

    def setUp(self):
        self.superadmin = _make_superadmin("sa9@test.local")
        self.org, self.owner = _make_client()
        self.client_ = Client()
        self.client_.force_login(self.superadmin)

    def tearDown(self):
        delete_organization_and_tenant(self.org, using=ENV)

    def test_suspend_requires_confirmation(self):
        resp = self.client_.post(
            f"/superadmin/{ENV}/{self.org.pk}/service/suspend/",
            {"reason": "PAYMENT_OVERDUE", "notes": "no confirm box"},
        )
        self.assertEqual(resp.status_code, 302)
        self.org.refresh_from_db(using=ENV)
        self.assertEqual(self.org.service_status, Organization.ServiceStatus.ACTIVE)
        self.assertEqual(ServiceStatusChange.objects.using(ENV).filter(organization_id=self.org.id).count(), 0)

    def test_suspend_requires_reason(self):
        resp = self.client_.post(f"/superadmin/{ENV}/{self.org.pk}/service/suspend/", {"confirm": "on"})
        self.assertEqual(resp.status_code, 302)
        self.org.refresh_from_db(using=ENV)
        self.assertEqual(self.org.service_status, Organization.ServiceStatus.ACTIVE)

    def test_stop_requires_reason_and_confirmation(self):
        resp = self.client_.post(f"/superadmin/{ENV}/{self.org.pk}/service/stop/", {"confirm": "on"})
        self.org.refresh_from_db(using=ENV)
        self.assertEqual(self.org.service_status, Organization.ServiceStatus.ACTIVE)

        resp = self.client_.post(
            f"/superadmin/{ENV}/{self.org.pk}/service/stop/", {"reason": "MAINTENANCE", "confirm": "on"}
        )
        self.assertEqual(resp.status_code, 302)
        self.org.refresh_from_db(using=ENV)
        self.assertEqual(self.org.service_status, Organization.ServiceStatus.SUSPENDED)

    def test_resume_does_not_require_a_reason(self):
        service_control.suspend_service(self.org, using=ENV, admin_email="setup@test.local", reason="OTHER")
        resp = self.client_.post(f"/superadmin/{ENV}/{self.org.pk}/service/resume/", {"confirm": "on"})
        self.assertEqual(resp.status_code, 302)
        self.org.refresh_from_db(using=ENV)
        self.assertEqual(self.org.service_status, Organization.ServiceStatus.ACTIVE)

    def test_resume_requires_confirmation(self):
        service_control.suspend_service(self.org, using=ENV, admin_email="setup@test.local", reason="OTHER")
        resp = self.client_.post(f"/superadmin/{ENV}/{self.org.pk}/service/resume/", {})
        self.org.refresh_from_db(using=ENV)
        self.assertEqual(self.org.service_status, Organization.ServiceStatus.SUSPENDED)

    def test_audit_log_records_all_required_fields(self):
        self.client_.post(
            f"/superadmin/{ENV}/{self.org.pk}/service/suspend/",
            {"reason": "SECURITY", "notes": "credential leak reported", "confirm": "on"},
        )
        log = ServiceStatusChange.objects.using(ENV).get(organization_id=self.org.id)
        self.assertEqual(log.performed_by_email, "sa9@test.local")
        self.assertEqual(log.action, ServiceStatusChange.Action.SUSPEND)
        self.assertEqual(log.previous_status, Organization.ServiceStatus.ACTIVE)
        self.assertEqual(log.new_status, Organization.ServiceStatus.SUSPENDED)
        self.assertEqual(log.reason, "SECURITY")
        self.assertEqual(log.notes, "credential leak reported")
        self.assertIsNotNone(log.created_at)

    def test_start_and_stop_actions_also_create_audit_entries(self):
        self.client_.post(
            f"/superadmin/{ENV}/{self.org.pk}/service/stop/", {"reason": "ADMINISTRATIVE", "confirm": "on"}
        )
        self.client_.post(f"/superadmin/{ENV}/{self.org.pk}/service/start/", {"confirm": "on"})
        # order_by("id") rather than created_at: two inserts in rapid HTTP
        # succession can land on the same auto_now_add timestamp depending
        # on platform clock resolution — the auto-increment id is the
        # reliable signal for insertion order.
        actions = list(
            ServiceStatusChange.objects.using(ENV)
            .filter(organization_id=self.org.id)
            .order_by("id")
            .values_list("action", flat=True)
        )
        self.assertEqual(actions, [ServiceStatusChange.Action.STOP, ServiceStatusChange.Action.START])

    def test_suspend_never_deletes_or_modifies_client_data(self):
        subscription_before = billing_services.get_current_subscription(self.org.id, using=ENV)
        invoice = invoicing.create_invoice(self.org, using=ENV, subtotal=500, discount=0, tax=0)
        payment_services.record_payment(self.org, using=ENV, invoice=invoice, amount=500, status=Payment.Status.SUCCESS)
        users_before = list(User.objects.using(ENV).filter(organization_id=self.org.id).values_list("id", flat=True))

        self.client_.post(
            f"/superadmin/{ENV}/{self.org.pk}/service/suspend/", {"reason": "PAYMENT_OVERDUE", "confirm": "on"}
        )

        self.org.refresh_from_db(using=ENV)
        self.assertEqual(self.org.service_status, Organization.ServiceStatus.SUSPENDED)
        self.assertTrue(self.org.is_active)  # account status untouched

        users_after = list(User.objects.using(ENV).filter(organization_id=self.org.id).values_list("id", flat=True))
        self.assertEqual(users_before, users_after)

        subscription_after = billing_services.get_current_subscription(self.org.id, using=ENV)
        self.assertEqual(subscription_before.pk, subscription_after.pk)

        invoice.refresh_from_db(using=ENV)
        self.assertEqual(invoice.status, Invoice.Status.PAID)
        self.assertEqual(Payment.objects.using(ENV).filter(organization_id=self.org.id).count(), 1)

    def test_superadmin_retains_access_to_suspended_client(self):
        self.client_.post(
            f"/superadmin/{ENV}/{self.org.pk}/service/suspend/", {"reason": "PAYMENT_OVERDUE", "confirm": "on"}
        )
        self.assertEqual(self.client_.get(f"/superadmin/{ENV}/{self.org.pk}/").status_code, 200)
        self.assertEqual(self.client_.get(f"/superadmin/{ENV}/{self.org.pk}/service/").status_code, 200)

    def test_service_control_page_shows_pre_resume_context(self):
        invoice = invoicing.create_invoice(self.org, using=ENV, subtotal=1000, discount=0, tax=0)
        self.client_.post(
            f"/superadmin/{ENV}/{self.org.pk}/service/suspend/", {"reason": "PAYMENT_OVERDUE", "confirm": "on"}
        )
        resp = self.client_.get(f"/superadmin/{ENV}/{self.org.pk}/service/")
        self.assertContains(resp, "Payment pending / overdue")
        self.assertContains(resp, "1000")  # outstanding amount


class BackendServiceEnforcementTests(TransactionTestCase):
    """The explicit IMPORTANT requirement: blocking must happen at the
    backend/view layer, not just by hiding frontend buttons — proven by
    hitting several different finance endpoints directly while suspended,
    not just the login page or dashboard."""

    databases = DATABASES
    serialized_rollback = True

    def setUp(self):
        self.superadmin = _make_superadmin("sa10@test.local")

    def tearDown(self):
        User.objects.filter(email="sa10@test.local").delete()

    def test_suspended_org_owner_blocked_from_every_finance_endpoint(self):
        org, owner = _make_client()
        try:
            admin_client = Client()
            admin_client.force_login(self.superadmin)

            owner_client = Client()
            owner_client.post("/accounts/login/", {"email": owner.email, "password": "ownerpass123"})

            admin_client.post(
                f"/superadmin/{ENV}/{org.pk}/service/suspend/", {"reason": "SECURITY", "confirm": "on"}
            )

            protected_paths = [
                "/app/", "/app/entries/", "/app/reports/", "/app/settings/", "/app/analytics/", "/app/daily/",
            ]
            for path in protected_paths:
                resp = owner_client.get(path, follow=True)
                self.assertFalse(
                    resp.wsgi_request.user.is_authenticated,
                    f"{path} did not block the suspended user at the backend",
                )
        finally:
            delete_organization_and_tenant(org, using=ENV)

    def test_suspended_org_owner_cannot_bypass_via_post(self):
        """A suspended user must not be able to bypass the block by POSTing
        directly to a mutating endpoint (e.g. adding a sale) either."""
        org, owner = _make_client()
        try:
            admin_client = Client()
            admin_client.force_login(self.superadmin)
            admin_client.post(
                f"/superadmin/{ENV}/{org.pk}/service/suspend/", {"reason": "SECURITY", "confirm": "on"}
            )

            owner_client = Client()
            owner_client.post("/accounts/login/", {"email": owner.email, "password": "ownerpass123"})
            resp = owner_client.post("/app/sales/add/", {"amount": "100"}, follow=True)
            self.assertFalse(resp.wsgi_request.user.is_authenticated)
        finally:
            delete_organization_and_tenant(org, using=ENV)


class CreateInvoiceTests(TestCase):
    databases = DATABASES

    def setUp(self):
        self.superadmin = _make_superadmin("sa11@test.local")
        self.org, self.owner = _make_client()
        self.client_ = Client()
        self.client_.force_login(self.superadmin)

    def tearDown(self):
        delete_organization_and_tenant(self.org, using=ENV)

    def test_create_invoice_appears_in_financial_history(self):
        resp = self.client_.post(
            f"/superadmin/{ENV}/{self.org.pk}/invoices/create/",
            {
                "subscription": "", "subtotal": "1000", "discount": "0", "tax": "180",
                "invoice_date": "2026-09-14", "due_date": "2026-09-21", "status": "ISSUED",
            },
        )
        self.assertEqual(resp.status_code, 302)
        invoice = Invoice.objects.using(ENV).get(organization_id=self.org.id)
        self.assertEqual(invoice.total, Decimal("1180.00"))
        self.assertEqual(invoice.status, Invoice.Status.ISSUED)

        history_resp = self.client_.get(f"/superadmin/{ENV}/{self.org.pk}/financial-history/")
        self.assertContains(history_resp, invoice.invoice_number)


class CreateInvoiceClientVisibilityTests(TransactionTestCase):
    """Cross-alias: the org/owner live on "dev", but login/session reads
    always go through "default" — needs a real commit, not a TestCase
    rollback, same as CrossConnectionLoginTests above."""

    databases = DATABASES
    serialized_rollback = True

    def setUp(self):
        self.superadmin = _make_superadmin("sa12@test.local")

    def tearDown(self):
        User.objects.filter(email="sa12@test.local").delete()

    def test_created_invoice_is_visible_on_clients_own_billing_page(self):
        org, owner = _make_client()
        try:
            admin_client = Client()
            admin_client.force_login(self.superadmin)
            admin_client.post(
                f"/superadmin/{ENV}/{org.pk}/invoices/create/",
                {
                    "subscription": "", "subtotal": "500", "discount": "0", "tax": "0",
                    "invoice_date": "2026-09-14", "due_date": "2026-09-21", "status": "ISSUED",
                },
            )
            invoice = Invoice.objects.using(ENV).get(organization_id=org.id)

            owner_client = Client()
            owner_client.post("/accounts/login/", {"email": owner.email, "password": "ownerpass123"})
            resp = owner_client.get("/app/billing/")
            self.assertContains(resp, invoice.invoice_number)
            self.assertContains(resp, "Outstanding balance")
        finally:
            delete_organization_and_tenant(org, using=ENV)
