from django.test import RequestFactory, SimpleTestCase
from django.urls import resolve

from apps.finance import urls as finance_urls

from .manager_pages import (
    ALWAYS_ALLOWED_URL_NAMES,
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
        unregistered = names - ALWAYS_ALLOWED_URL_NAMES - {
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
