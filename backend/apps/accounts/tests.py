from datetime import timedelta

from django.core import mail
from django.test import RequestFactory, SimpleTestCase, TestCase
from django.urls import resolve, reverse

from apps.finance import urls as finance_urls

from .manager_pages import (
    ALWAYS_ALLOWED_URL_NAMES,
    CLOSED_URL_NAMES,
    MANAGER_PAGES,
    PageAccess,
    manager_can_open,
)
from .middleware import ManagerPageAccessMiddleware
from .models import User


def _user(role, pages=()):
    # Unsaved instances: the access rules only read `role` and `manager_pages`.
    return User(email=f"{role.lower()}@example.com", role=role, manager_pages=list(pages))


class ManagerPageRegistryTests(SimpleTestCase):
    def test_every_finance_url_is_registered(self):
        names = {p.name for p in finance_urls.urlpatterns if p.name}
        unregistered = names - ALWAYS_ALLOWED_URL_NAMES - CLOSED_URL_NAMES - {
            name for _, _, page_names in MANAGER_PAGES for name in page_names
        }
        self.assertEqual(unregistered, set(), "Add these finance URLs to apps/accounts/manager_pages.py")

    def test_no_url_name_belongs_to_two_pages(self):
        seen = {}
        for key, _, page_names in MANAGER_PAGES:
            for name in page_names:
                self.assertNotIn(name, seen, f"{name} is in both {seen.get(name)} and {key}")
                seen[name] = key


class ManagerCanOpenTests(SimpleTestCase):
    def test_only_ticked_pages_are_open(self):
        pages = ["daily_summary"]
        self.assertTrue(manager_can_open(pages, "daily_summary"))
        self.assertTrue(manager_can_open(pages, "daily_summary_excel"))
        self.assertTrue(manager_can_open(pages, "daily_summary_day_detail"))
        self.assertFalse(manager_can_open(pages, "daily_report"))
        self.assertFalse(manager_can_open(pages, "dashboard"))
        self.assertFalse(manager_can_open(pages, "kickbacks"))

    def test_billing_is_always_open(self):
        self.assertTrue(manager_can_open([], "billing"))

    def test_unregistered_url_is_denied(self):
        self.assertFalse(manager_can_open(["daily_report"], "not_a_real_page"))
        self.assertFalse(manager_can_open(["daily_report"], None))


class PageAccessTests(SimpleTestCase):
    def test_owner_and_admin_see_every_page(self):
        for role in (User.Role.OWNER, User.Role.ADMIN):
            access = PageAccess(_user(role))
            self.assertIn("daily_summary", access)
            self.assertIn("setup", access)

    def test_manager_sees_only_ticked_pages(self):
        access = PageAccess(_user(User.Role.MANAGER, ["daily_summary", "dashboard"]))
        self.assertIn("daily_summary", access)
        self.assertIn("dashboard", access)
        self.assertNotIn("kickbacks", access)


class ManagerPageAccessMiddlewareTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.middleware = ManagerPageAccessMiddleware(lambda request: None)

    def _run(self, user, path):
        request = self.factory.get(path)
        request.user = user
        request.resolver_match = resolve(path)
        return self.middleware.process_view(request, None, (), {})

    def test_manager_is_redirected_from_unticked_page(self):
        response = self._run(_user(User.Role.MANAGER, ["daily_report"]), "/app/daily-performance/")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/app/daily/")

    def test_manager_without_daily_book_goes_to_profile(self):
        response = self._run(_user(User.Role.MANAGER, ["daily_summary"]), "/app/patient-bills/")
        self.assertEqual(response["Location"], "/accounts/profile/")

    def test_manager_can_open_ticked_page(self):
        self.assertIsNone(self._run(_user(User.Role.MANAGER, ["daily_summary"]), "/app/daily-performance/"))

    def test_owner_is_never_redirected(self):
        self.assertIsNone(self._run(_user(User.Role.OWNER), "/app/patient-bills/"))

    def test_non_finance_pages_are_not_affected(self):
        self.assertIsNone(self._run(_user(User.Role.MANAGER, []), "/accounts/profile/"))


class WelcomeEmailTests(TestCase):
    def setUp(self):
        from apps.billing.models import Plan

        Plan.objects.update_or_create(name="Standard", defaults={
            "is_active": True, "trial_days": 21, "features": ["business_dashboard", "reports"],
        })

    def _signup(self, **overrides):
        data = {
            "organization_name": "Welcome Test Co", "business_type": "RETAIL_ECOMMERCE", "size": "SMALL",
            "manager_logins": "1", "storage_mode": "OUR_DATABASE", "first_name": "Asha", "last_name": "",
            "email": "asha@welcometest.example", "username": "asha", "password": "ownerpass123",
            "confirm_password": "ownerpass123", "terms_agree": "on",
        }
        data.update(overrides)
        return self.client.post(reverse("accounts:signup"), data)

    def test_signup_sends_one_welcome_email_with_plan_trial_and_storage(self):
        self._signup()
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual(message.to, ["asha@welcometest.example"])
        self.assertIn("21-day free trial", message.subject)
        html = message.alternatives[0][0]
        for text in ("Standard", "21 days", "Finday&#x27;s secure managed database", "Reports"):
            self.assertIn(text, html)

    def test_welcome_email_is_never_sent_twice(self):
        from apps.billing.models import Subscription
        from apps.organizations.models import Organization

        from .emails import send_welcome_email

        self._signup()
        org = Organization.objects.get(name="Welcome Test Co")
        self.assertIsNotNone(org.welcome_email_sent_at)
        sent_again = send_welcome_email(
            org, User.objects.get(username="asha"),
            Subscription.objects.get(organization=org, is_current=True), dashboard_url="http://x/",
        )
        self.assertFalse(sent_again)
        self.assertEqual(len(mail.outbox), 1)


class PasswordResetEmailTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="reset@resettest.example", username="resetter", password="oldpass123", first_name="Ravi"
        )

    def _request_code(self):
        return self.client.post(reverse("accounts:forgot_password"), {"email": self.user.email})

    def _latest(self):
        from .models import PasswordResetOTP

        return PasswordResetOTP.objects.filter(user=self.user).latest("created_at")

    def _reset(self, code):
        return self.client.post(reverse("accounts:reset_password"), {
            "otp": code, "new_password": "newpass123", "confirm_password": "newpass123",
        })

    def test_sends_branded_code_email(self):
        self._request_code()
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual(message.to, [self.user.email])
        self.assertIn(self._latest().code, message.body)
        self.assertIn(self._latest().code, message.alternatives[0][0])

    def test_resend_within_cooldown_does_not_send_again(self):
        self._request_code()
        self._request_code()
        self.assertEqual(len(mail.outbox), 1)

    def test_new_code_invalidates_older_one(self):
        from .models import PasswordResetOTP

        self._request_code()
        old = self._latest()
        PasswordResetOTP.objects.filter(pk=old.pk).update(created_at=old.created_at - timedelta(minutes=2))
        self._request_code()
        self.assertEqual(len(mail.outbox), 2)
        old.refresh_from_db()
        self.assertTrue(old.is_used)

    def test_code_is_burned_after_too_many_wrong_guesses(self):
        from .models import OTP_MAX_ATTEMPTS

        self._request_code()
        code = self._latest().code
        wrong = "000000" if code != "000000" else "111111"
        for _ in range(OTP_MAX_ATTEMPTS):
            self._reset(wrong)
        self._reset(code)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("oldpass123"))

    def test_correct_code_resets_password_once(self):
        self._request_code()
        code = self._latest().code
        self.assertRedirects(self._reset(code), reverse("accounts:login"), fetch_redirect_response=False)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("newpass123"))
        self.assertTrue(self._latest().is_used)


class ClosedUrlTests(SimpleTestCase):
    def test_plan_change_stays_closed_to_managers_and_limited_clients(self):
        self.assertFalse(manager_can_open(("daily_report", "setup"), "change_plan"))
