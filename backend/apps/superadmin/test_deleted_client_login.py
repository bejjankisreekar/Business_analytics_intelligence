"""A client the superadmin deletes must not be able to sign in again, on the
web or in the mobile app, and must be told their credentials are not valid."""

from unittest import mock

from django.test import Client, TransactionTestCase

from apps.accounts.models import User
from apps.organizations.models import Organization
from apps.organizations.services import delete_organization_and_tenant
from apps.organizations.testing import SheetsBackedMixin

from .tests import DATABASES, ENV, _make_client, _make_superadmin


class DeletedClientLoginTests(SheetsBackedMixin, TransactionTestCase):
    databases = DATABASES
    serialized_rollback = True

    def setUp(self):
        super().setUp()
        self.superadmin = _make_superadmin("sa-del@test.local")
        self.org, self.owner = _make_client(self)

    def tearDown(self):
        User.objects.filter(email="sa-del@test.local").delete()
        if Organization.objects.using(ENV).filter(pk=self.org.pk).exists():
            delete_organization_and_tenant(self.org, using=ENV)

    def _delete_org(self):
        admin = Client()
        admin.force_login(self.superadmin)
        admin.post(f"/superadmin/{ENV}/{self.org.pk}/delete/")

    def _sign_in_everywhere(self):
        web = Client()
        web.post("/accounts/login/", {"email": self.owner.email, "password": "ownerpass123"})
        app = Client()
        token = app.post(
            "/api/mobile/login/",
            {"email": self.owner.email, "password": "ownerpass123"},
            content_type="application/json",
        ).json()["token"]
        return web, app, token

    def _assert_locked_out(self, web, app, token):
        # The open web session no longer works...
        resp = web.get("/app/", follow=True)
        self.assertFalse(resp.wsgi_request.user.is_authenticated)
        # ...and signing in again is refused like any wrong password.
        resp = web.post("/accounts/login/", {"email": self.owner.email, "password": "ownerpass123"})
        self.assertFalse(resp.wsgi_request.user.is_authenticated)
        self.assertContains(resp, "Incorrect email or password.")

        # Same for the mobile app: the saved token is rejected, and so is a new sign-in.
        resp = app.get("/api/mobile/me/", HTTP_AUTHORIZATION=f"Bearer {token}")
        self.assertEqual(resp.status_code, 401)
        resp = app.post(
            "/api/mobile/login/",
            {"email": self.owner.email, "password": "ownerpass123"},
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp.json()["error"], "Incorrect email or password.")

    def test_deleted_client_cannot_sign_in(self):
        web, app, token = self._sign_in_everywhere()
        self._delete_org()
        self.assertFalse(Organization.objects.using(ENV).filter(pk=self.org.pk).exists())
        self._assert_locked_out(web, app, token)

        # Even if the full delete fails part-way (e.g. the request times out),
        # the client is already locked out.
        self.org, self.owner = _make_client(self)
        web, app, token = self._sign_in_everywhere()
        with mock.patch.object(Organization, "delete", side_effect=RuntimeError("timed out")):
            self._delete_org()
        self.assertTrue(Organization.objects.using(ENV).filter(pk=self.org.pk).exists())
        self._assert_locked_out(web, app, token)
