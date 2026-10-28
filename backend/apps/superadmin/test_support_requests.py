"""Help / Support: what a client sends, and how the superadmin console answers it.

The client's login and writes go through the default alias while the console
reads the environment alias, so the end-to-end path is covered by manual check
rather than here (see the alias note in apps/superadmin/tests.py)."""
from django.test import Client, TestCase

from apps.accounts.models import User
from apps.organizations.forms import SupportRequestForm
from apps.organizations.models import SupportRequest

from .test_console import DATABASES, ENV, ConsoleTestBase


class SupportRequestFormTests(TestCase):
    databases = DATABASES

    def test_subject_and_message_are_required(self):
        form = SupportRequestForm({"category": SupportRequest.Category.OTHER, "subject": "", "message": ""})
        self.assertFalse(form.is_valid())
        self.assertIn("subject", form.errors)
        self.assertIn("message", form.errors)

    def test_valid_message_is_accepted(self):
        form = SupportRequestForm({
            "category": SupportRequest.Category.BILLING, "subject": "Invoice question", "message": "Why is it higher?",
        })
        self.assertTrue(form.is_valid(), form.errors)


class SupportRequestAccessTests(TestCase):
    databases = DATABASES

    def test_anonymous_user_is_sent_to_login(self):
        resp = Client().get("/organizations/support/")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("login", resp["Location"])

    def test_user_without_organization_is_redirected(self):
        user = User.objects.create_user(email="loose@test.local", password="x" * 12, role=User.Role.OWNER)
        c = Client()
        c.force_login(user)
        resp = c.get("/organizations/support/")
        self.assertEqual(resp.status_code, 302)


class SupportInboxTests(ConsoleTestBase):
    def make_request(self, **overrides):
        data = {
            "organization": self.org, "submitted_by_email": self.owner.email,
            "category": SupportRequest.Category.OTHER, "subject": "Question", "message": "How do I export?",
        }
        data.update(overrides)
        return SupportRequest.objects.using(ENV).create(**data)

    def test_inbox_lists_open_messages_by_default(self):
        self.make_request(subject="Need help")
        resp = self.c.get(self.url("support/"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Need help")

    def test_resolved_messages_are_hidden_from_the_open_filter(self):
        self.make_request(subject="Already sorted", status=SupportRequest.Status.RESOLVED)
        self.assertNotContains(self.c.get(self.url("support/")), "Already sorted")
        self.assertContains(self.c.get(self.url("support/?status=ALL")), "Already sorted")

    def test_open_count_feeds_the_nav_badge_and_attention_list(self):
        from .alerts import attention_items, nav_counts

        self.make_request()
        self.assertEqual(nav_counts(ENV)["support_requests"], 1)
        keys = {item["key"] for item in attention_items(ENV)}
        self.assertIn("support_requests", keys)

    def test_reply_is_saved_with_responder_and_time(self):
        item = self.make_request()
        resp = self.c.post(self.url(f"support/{item.pk}/"), {
            "status": SupportRequest.Status.RESOLVED,
            "admin_reply": "Open Profile and tap Export to Excel.",
        })
        self.assertEqual(resp.status_code, 302)
        item.refresh_from_db(using=ENV)
        self.assertEqual(item.status, SupportRequest.Status.RESOLVED)
        self.assertEqual(item.admin_reply, "Open Profile and tap Export to Excel.")
        self.assertEqual(item.replied_by_email, self.admin_email)
        self.assertIsNotNone(item.replied_at)

    def test_saving_without_changing_the_reply_keeps_the_original_responder(self):
        item = self.make_request(admin_reply="Earlier answer", replied_by_email="first@test.local")
        self.c.post(self.url(f"support/{item.pk}/"), {
            "status": SupportRequest.Status.OPEN, "admin_reply": "Earlier answer",
        })
        item.refresh_from_db(using=ENV)
        self.assertEqual(item.replied_by_email, "first@test.local")
        self.assertEqual(item.status, SupportRequest.Status.OPEN)

    def test_non_superadmin_cannot_open_the_inbox(self):
        other = Client()
        other.force_login(User.objects.create_user(email="member@test.local", password="x" * 12, role=User.Role.OWNER))
        self.assertEqual(other.get(self.url("support/")).status_code, 403)
