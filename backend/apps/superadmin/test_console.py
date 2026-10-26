"""Tests for the console features added on top of the core client/billing
screens: the audit trail, list export/sort/pagination, bulk actions, notes
and tags, health scores, global search, admins, log-in-as and the new
read-only pages. Same alias conventions as tests.py (see its docstring)."""
import datetime

from django.test import Client, TestCase, TransactionTestCase
from django.utils import timezone

from apps.accounts.models import User
from apps.billing import invoicing
from apps.billing import payments as payment_services
from apps.billing.models import Coupon, Payment, Subscription
from apps.organizations.models import Organization
from apps.organizations.services import delete_organization_and_tenant
from apps.organizations.testing import SheetsBackedMixin

from .alerts import attention_items, nav_counts
from .health import score, usage_for
from .models import AuditLogEntry, ClientNote, ClientTag
from .tests import DATABASES, ENV, _make_client, _make_superadmin


class ConsoleTestBase(SheetsBackedMixin, TestCase):
    databases = DATABASES
    admin_email = "console@test.local"

    def setUp(self):
        super().setUp()
        self.superadmin = _make_superadmin(self.admin_email)
        self.org, self.owner = _make_client(self)
        self.c = Client()
        self.c.force_login(self.superadmin)

    def tearDown(self):
        delete_organization_and_tenant(self.org, using=ENV)

    def url(self, suffix=""):
        return f"/superadmin/{ENV}/{suffix}"


class AuditTrailTests(ConsoleTestBase):
    def test_successful_change_is_logged_with_actor_summary_and_fields(self):
        resp = self.c.post(self.url("coupons/create/"), {
            "code": "AUDIT10", "description": "", "discount_type": "PERCENT", "discount_value": "10",
            "is_active": "on", "max_redemptions_per_org": "1",
        })
        self.assertEqual(resp.status_code, 302)
        entry = AuditLogEntry.objects.using(ENV).get(action="coupon_create")
        self.assertEqual(entry.actor_email, self.admin_email)
        self.assertIn("AUDIT10", entry.summary)
        self.assertEqual(entry.details["code"], "AUDIT10")
        self.assertNotIn("csrfmiddlewaretoken", entry.details)

    def test_failed_form_is_not_logged(self):
        self.c.post(self.url("coupons/create/"), {"code": ""})
        self.assertFalse(AuditLogEntry.objects.using(ENV).filter(action="coupon_create").exists())

    def test_client_actions_record_the_client_and_hide_generated_passwords(self):
        self.c.post(self.url(f"{self.org.pk}/team/{self.owner.pk}/reset-password/"), {"new_password": ""})
        entry = AuditLogEntry.objects.using(ENV).get(action="reset_client_password")
        self.assertEqual(entry.organization_id, self.org.pk)
        self.assertEqual(entry.organization_name, self.org.name)
        self.assertIn("password: [hidden]", entry.summary)
        self.assertNotIn("new_password", entry.details)

    def test_audit_page_lists_and_exports_entries(self):
        self.c.post(self.url(f"{self.org.pk}/notes/add/"), {"body": "Followed up"})
        resp = self.c.get(self.url("audit/"))
        self.assertContains(resp, "Added a note")
        export = self.c.get(self.url("audit/?export=csv"))
        self.assertEqual(export["Content-Type"], "text/csv; charset=utf-8")
        self.assertIn("Added note", export.content.decode("utf-8-sig"))


class ListExportSortTests(ConsoleTestBase):
    def setUp(self):
        super().setUp()
        self.invoice = invoicing.create_invoice(self.org, using=ENV, subtotal=1000, discount=0, tax=180)
        payment_services.record_payment(
            self.org, using=ENV, invoice=self.invoice, amount=500,
            gateway=Payment.Gateway.MANUAL, payment_method=Payment.Method.UPI, status=Payment.Status.SUCCESS,
        )

    def test_every_list_exports_csv_and_excel(self):
        for path in ("", "subscriptions/", "payments/", "invoices/"):
            csv = self.c.get(self.url(path) + "?export=csv")
            self.assertEqual(csv.status_code, 200, path)
            self.assertIn(self.org.name, csv.content.decode("utf-8-sig"), path)
            xlsx = self.c.get(self.url(path) + "?export=xlsx")
            self.assertEqual(
                xlsx["Content-Type"], "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", path,
            )

    def test_sorting_and_paging_parameters_are_accepted(self):
        for path in ("?sort=-health", "?sort=name&per_page=50", "invoices/?sort=-amount_due",
                     "payments/?sort=amount&page=99", "subscriptions/?sort=bogus"):
            self.assertEqual(self.c.get(self.url(path)).status_code, 200, path)

    def test_client_list_shows_health_and_usage(self):
        resp = self.c.get(self.url())
        self.assertContains(resp, "Healthy")
        self.assertContains(resp, "Usage (30d)")


class BulkActionTests(ConsoleTestBase):
    def bulk(self, **data):
        return self.c.post(self.url("clients/bulk/"), {"ids": [str(self.org.pk)], **data})

    def test_extend_trial(self):
        sub = Subscription.objects.using(ENV).get(organization=self.org, is_current=True)
        before = sub.trial_end_date
        self.bulk(action="extend_trial", days="10")
        sub.refresh_from_db(using=ENV)
        self.assertEqual(sub.trial_end_date, max(before, timezone.localdate()) + datetime.timedelta(days=10))

    def test_suspend_then_resume(self):
        self.bulk(action="suspend", reason="ADMINISTRATIVE")
        self.org.refresh_from_db(using=ENV)
        self.assertFalse(self.org.is_service_active)
        self.bulk(action="resume")
        self.org.refresh_from_db(using=ENV)
        self.assertTrue(self.org.is_service_active)

    def test_tagging_and_filter_by_tag(self):
        self.bulk(action="add_tag", tag="VIP")
        self.assertTrue(ClientTag.objects.using(ENV).filter(organization=self.org, name="VIP").exists())
        self.assertContains(self.c.get(self.url("?tag=VIP")), self.org.name)
        self.bulk(action="remove_tag", tag="VIP")
        self.assertFalse(ClientTag.objects.using(ENV).filter(organization=self.org).exists())

    def test_export_selected(self):
        resp = self.bulk(action="export")
        self.assertEqual(resp["Content-Type"], "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    def test_needs_an_action_and_rows(self):
        resp = self.c.post(self.url("clients/bulk/"), {"action": "suspend"})
        self.assertEqual(resp.status_code, 302)
        self.org.refresh_from_db(using=ENV)
        self.assertTrue(self.org.is_service_active)


class NotesTagsTests(ConsoleTestBase):
    def test_add_pin_delete_note(self):
        self.c.post(self.url(f"{self.org.pk}/notes/add/"), {"body": "Owner will pay Friday"})
        note = ClientNote.objects.using(ENV).get(organization=self.org)
        self.assertEqual(note.author_email, self.admin_email)
        self.c.post(self.url(f"{self.org.pk}/notes/{note.pk}/pin/"))
        note.refresh_from_db(using=ENV)
        self.assertTrue(note.is_pinned)
        detail = self.c.get(self.url(f"{self.org.pk}/"))
        self.assertContains(detail, "Owner will pay Friday")
        self.c.post(self.url(f"{self.org.pk}/notes/{note.pk}/delete/"))
        self.assertFalse(ClientNote.objects.using(ENV).exists())

    def test_tag_is_unique_per_client(self):
        for _ in range(2):
            self.c.post(self.url(f"{self.org.pk}/tags/add/"), {"name": "Churn risk", "color": "red"})
        self.assertEqual(ClientTag.objects.using(ENV).filter(organization=self.org).count(), 1)


class HealthAndAlertTests(ConsoleTestBase):
    def test_never_logged_in_client_loses_points(self):
        usage = usage_for(ENV, [self.org.id])[self.org.id]
        sub = Subscription.objects.using(ENV).get(organization=self.org, is_current=True)
        health = score(self.org, sub, usage)
        self.assertIn("Nobody has ever logged in", health["reasons"])
        self.assertLess(health["score"], 100)

    def test_overdue_invoice_is_an_alert_and_a_badge(self):
        invoice = invoicing.create_invoice(self.org, using=ENV, subtotal=100, discount=0, tax=0)
        invoice.due_date = timezone.localdate() - datetime.timedelta(days=3)
        invoice.save(using=ENV)
        keys = {i["key"] for i in attention_items(ENV)}
        self.assertIn("overdue_invoices", keys)
        self.assertGreaterEqual(nav_counts(ENV)["overdue_invoices"], 1)
        self.assertContains(self.c.get("/superadmin/"), "Overdue invoices")


class NewPagesTests(ConsoleTestBase):
    def test_pages_render(self):
        coupon = Coupon.objects.using(ENV).create(code="SHOWME", discount_value=5)
        for path in ("analytics/", "analytics/?months=24", "search/?q=Test", "audit/", "webhooks/",
                     "system/", "admins/", f"coupons/{coupon.pk}/", "plans/", "coupons/", "storage-requests/"):
            self.assertEqual(self.c.get(self.url(path)).status_code, 200, path)

    def test_global_search_finds_client_and_user(self):
        resp = self.c.get(self.url("search/?q=testorg"))
        self.assertContains(resp, self.org.name)
        self.assertContains(resp, self.owner.email)

    def test_non_superadmin_is_forbidden_from_new_pages(self):
        other = Client()
        other.force_login(User.objects.create_user(email="member@test.local", password="x" * 12, role=User.Role.OWNER))
        for path in ("audit/", "system/", "admins/", "search/?q=a", "webhooks/"):
            self.assertEqual(other.get(self.url(path)).status_code, 403, path)


class AdminManagementTests(ConsoleTestBase):
    def test_add_admin_and_cannot_deactivate_self(self):
        self.c.post(self.url("admins/create/"), {"email": "new-admin@test.local", "password": "Str0ng-pass-123"})
        new = User.objects.using(ENV).get(email="new-admin@test.local")
        self.assertEqual(new.role, User.Role.SUPER_ADMIN)
        # The signed-in admin lives on the default alias (see tests.py), so give
        # this environment a second active admin before deactivating one.
        User.objects.db_manager(ENV).create_user(
            email="other-admin@test.local", password="x" * 12, role=User.Role.SUPER_ADMIN)
        self.c.post(self.url(f"admins/{new.pk}/toggle/"))
        new.refresh_from_db(using=ENV)
        self.assertFalse(new.is_active)
        self.c.post(self.url(f"admins/{self.superadmin.pk}/toggle/"))
        self.superadmin.refresh_from_db()
        self.assertTrue(self.superadmin.is_active)

    def test_weak_password_rejected(self):
        self.c.post(self.url("admins/create/"), {"email": "weak@test.local", "password": "123"})
        self.assertFalse(User.objects.using(ENV).filter(email="weak@test.local").exists())


class ImpersonationTests(SheetsBackedMixin, TransactionTestCase):
    databases = DATABASES
    serialized_rollback = True

    def setUp(self):
        super().setUp()
        self.superadmin = _make_superadmin("imp@test.local")
        self.org, self.owner = _make_client(self)

    def tearDown(self):
        delete_organization_and_tenant(self.org, using=ENV)
        User.objects.filter(email="imp@test.local").delete()

    def test_log_in_as_client_and_return(self):
        c = Client()
        c.force_login(self.superadmin)
        before = User.objects.get(pk=self.owner.pk).last_login
        resp = c.post(f"/superadmin/{ENV}/{self.org.pk}/team/{self.owner.pk}/impersonate/")
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(c.session["_auth_user_id"], str(self.owner.pk))
        self.assertEqual(c.session["impersonator_email"], "imp@test.local")
        # The client's own last login isn't touched by a support visit.
        self.assertEqual(User.objects.get(pk=self.owner.pk).last_login, before)
        # The client can't reach the console while impersonated.
        self.assertEqual(c.get("/superadmin/").status_code, 403)

        back = c.post("/superadmin/impersonate/stop/")
        self.assertEqual(back.status_code, 302)
        self.assertEqual(c.session["_auth_user_id"], str(self.superadmin.pk))
        self.assertNotIn("impersonator_email", c.session)
        actions = set(AuditLogEntry.objects.using(ENV).values_list("action", flat=True))
        self.assertTrue({"impersonate_start", "impersonate_stop"} <= actions)

    def test_stop_without_impersonation_goes_to_login(self):
        resp = Client().post("/superadmin/impersonate/stop/")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/accounts/login/", resp["Location"])
