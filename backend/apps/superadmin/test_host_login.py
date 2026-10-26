"""The dedicated superadmin host and its own sign-in screen."""
from django.test import Client, TestCase, override_settings

from apps.organizations.testing import SheetsBackedMixin

from apps.accounts.models import User

from .tests import DATABASES, ENV, _make_client, _make_superadmin

ADMIN_HOST = "admin.example.test"


@override_settings(SUPERADMIN_HOST=ADMIN_HOST, ALLOWED_HOSTS=["*"])
class SuperadminHostLoginTests(SheetsBackedMixin, TestCase):
    databases = DATABASES

    def setUp(self):
        super().setUp()
        self.admin = _make_superadmin("host-admin@test.local")
        self.admin.set_password("S3cret-pass!")
        self.admin.save()
        _, self.client_user = _make_client(self)
        self.c = Client(HTTP_HOST=ADMIN_HOST)

    def _login(self, email, password="S3cret-pass!"):
        return self.c.post("/superadmin/login/", {"email": email, "password": password})

    def test_superadmin_credentials_work(self):
        r = self._login("host-admin@test.local")
        self.assertRedirects(r, "/superadmin/", fetch_redirect_response=False)

    def test_client_credentials_rejected_with_generic_error(self):
        r = self._login("owner@testorg.example", "ownerpass123")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Incorrect email or password.")
        self.assertNotIn("_auth_user_id", self.c.session)

    def test_wrong_password_rejected(self):
        self.assertEqual(self._login("host-admin@test.local", "nope").status_code, 200)

    def test_client_login_page_not_served_on_admin_host(self):
        r = self.c.get("/accounts/login/")
        self.assertRedirects(r, "/superadmin/login/", fetch_redirect_response=False)

    def test_console_not_served_on_other_hosts(self):
        self.assertEqual(Client(HTTP_HOST="app.example.test").get("/superadmin/login/").status_code, 404)

    def test_superadmin_cannot_use_client_login_when_host_configured(self):
        r = Client(HTTP_HOST="app.example.test").post(
            "/accounts/login/", {"email": "host-admin@test.local", "password": "S3cret-pass!"}
        )
        self.assertContains(r, "Incorrect email or password.")

    def test_unauthenticated_console_redirects_to_console_login(self):
        r = self.c.get("/superadmin/")
        self.assertEqual(r.status_code, 302)
        self.assertTrue(r["Location"].startswith("/superadmin/login/"))


class DbUsedColumnTests(SheetsBackedMixin, TestCase):
    databases = DATABASES

    def test_list_shows_db_used_for_own_database_client(self):
        from .health import db_bytes_for

        _make_superadmin("dbused-admin@test.local")
        org, _owner = _make_client(self)
        c = Client()
        c.force_login(User.objects.get(email="dbused-admin@test.local"))
        r = c.get(f"/superadmin/{ENV}/")
        self.assertContains(r, "DB used")
        self.assertContains(r, "DB used (all clients)")
        self.assertIsInstance(db_bytes_for(ENV, [org.pk])[org.pk], int)


class MissingEntriesTests(SheetsBackedMixin, TestCase):
    databases = DATABASES

    def _entries(self, org, days_ago_list):
        from apps.organizations.models import Organization

        Organization.objects.using(ENV).filter(pk=org.pk).update(storage_mode=Organization.StorageMode.OUR_DATABASE)
        org.refresh_from_db(using=ENV)
        import datetime
        from decimal import Decimal

        from django.utils import timezone

        from apps.finance.models import ExpenseEntry

        today = timezone.localdate()
        for n in days_ago_list:
            ExpenseEntry._base_manager.using(ENV).create(
                organization=org, date=today - datetime.timedelta(days=n), amount=Decimal("10"),
            )

    def test_regular_client_gone_quiet_is_flagged_but_not_emailed(self):
        from .alerts import attention_items
        from .health import silent_clients

        org, _ = _make_client(self)
        self._entries(org, [4, 5, 6, 7, 8, 9, 10])
        self.assertEqual(silent_clients(ENV), {org.pk: 4})
        self.assertIn("missing_entries", [i["key"] for i in attention_items(ENV)])
        self.assertNotIn("missing_entries", [i["key"] for i in attention_items(ENV, include_activity=False)])

    def test_recent_or_irregular_clients_not_flagged(self):
        from .health import silent_clients

        org, _ = _make_client(self)
        self._entries(org, [4, 20])  # only 2 entry days: not a regular rhythm
        self.assertEqual(silent_clients(ENV), {})
        self._entries(org, [0, 1, 2, 3, 5, 6])  # posted today
        self.assertEqual(silent_clients(ENV), {})


class MobileAppTests(SheetsBackedMixin, TestCase):
    databases = DATABASES
    APK = b"PK\x03\x04" + b"0" * 2048

    def setUp(self):
        super().setUp()
        self.admin = _make_superadmin("apk-admin@test.local")
        self.org, self.owner = _make_client(self)
        self.sa = Client()
        self.sa.force_login(self.admin)

    def _upload(self, version, data=None, name="app.apk"):
        from django.core.files.uploadedfile import SimpleUploadedFile

        return self.sa.post(
            f"/superadmin/{ENV}/mobile-app/upload/",
            {"version": version, "notes": "n", "file": SimpleUploadedFile(name, data or self.APK)},
        )

    def _versions(self):
        from .models import MobileAppVersion

        return MobileAppVersion.objects.using(ENV).defer("file")

    def test_upload_makes_newest_current_and_old_can_be_deleted(self):
        self._upload("1.0.0")
        self._upload("1.0.1")
        current = [v.version for v in self._versions() if v.is_current]
        self.assertEqual(current, ["1.0.1"])
        old = self._versions().get(version="1.0.0")
        self.sa.post(f"/superadmin/{ENV}/mobile-app/{old.pk}/current/")
        self.assertEqual([v.version for v in self._versions() if v.is_current], ["1.0.0"])
        self.sa.post(f"/superadmin/{ENV}/mobile-app/{old.pk}/delete/")  # deleting current promotes the newest
        self.assertEqual([(v.version, v.is_current) for v in self._versions()], [("1.0.1", True)])

    def test_rejects_non_apk_and_duplicate_version(self):
        self._upload("1.0.0", name="notes.txt")
        self._upload("1.0.0", data=b"not a zip")
        self.assertEqual(self._versions().count(), 0)
        self._upload("1.0.0")
        self._upload("1.0.0")
        self.assertEqual(self._versions().count(), 1)

    def test_client_downloads_current_version(self):
        from django.contrib.auth.models import AnonymousUser
        from django.http import Http404
        from django.test import RequestFactory

        from apps.accounts.views import MobileAppDownloadView

        view = MobileAppDownloadView.as_view()
        rf = RequestFactory()

        def get(user):
            request = rf.get("/accounts/mobile-app/download/")
            request.user = user
            return view(request)

        self.assertEqual(get(AnonymousUser()).status_code, 302)  # sent to sign in
        client_user = User(email="c@test.local", organization_id=self.org.pk)
        with self.assertRaises(Http404):  # nothing uploaded yet
            get(client_user)
        from .models import MobileAppVersion

        MobileAppVersion.objects.create(
            version="2.0.0", file=self.APK, file_name="app.apk", file_size=len(self.APK),
            sha256="0" * 64, is_current=True, uploaded_by_email="a@test.local",
        )
        r = get(client_user)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.content, self.APK)
        self.assertIn("finday-2.0.0.apk", r["Content-Disposition"])
        with self.assertRaises(Http404):  # an account without an organization
            get(User(email="nobody@test.local"))

    def test_clients_cannot_manage_uploads(self):
        c = Client()
        c.force_login(User.objects.create_user(email="plain@test.local", password="x" * 12, role=User.Role.OWNER))
        self.assertEqual(c.get(f"/superadmin/{ENV}/mobile-app/").status_code, 403)

    def test_console_page_renders(self):
        self._upload("1.0.0")
        self.assertContains(self.sa.get(f"/superadmin/{ENV}/mobile-app/"), "Upload new version")
