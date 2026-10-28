import base64
import json
from decimal import Decimal
from unittest import mock

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from django.core.cache import cache
from django.test import Client, TestCase

from apps.accounts.models import User
from apps.finance.models import SalesEntry
from apps.mobile_api.auth import make_token
from apps.organizations.models import Organization

from . import push
from .models import DeviceToken, QuoteRequest, QuoteRequestSettings
from .tests import make_org


def bearer(user):
    return {"HTTP_AUTHORIZATION": f"Bearer {make_token(user)}"}


def api(client, user, method, path, body=None, **extra):
    kwargs = {"content_type": "application/json", **bearer(user), **extra}
    data = json.dumps(body) if body is not None else None
    return getattr(client, method)(f"/api/mobile/{path}", data=data, **kwargs)


@mock.patch("apps.billing.services.is_account_suspended", return_value=False)
@mock.patch("apps.billing.services.has_active_access", return_value=True)
class MobileRequestsApiTests(TestCase):
    def setUp(self):
        cache.clear()
        self.shop = make_org("corner-shop", Organization.BusinessType.RETAIL_ECOMMERCE)
        self.hospital = make_org("city-hospital", Organization.BusinessType.HEALTHCARE)
        self.owner = User.objects.create_user(
            email="o@shop.example", password="x-pass-123", organization=self.shop, role=User.Role.OWNER)
        self.hosp_owner = User.objects.create_user(
            email="o@hosp.example", password="x-pass-123", organization=self.hospital, role=User.Role.OWNER)
        QuoteRequestSettings.objects.create(organization=self.shop, is_enabled=True)
        self.c = Client()

    def _submit(self, name="Ravi", **over):
        data = {"name": name, "phone": "9876543210", "product": ["Colgate paste", "Sugar"],
                "qty": ["2", "5"], "unit": ["pc", "kg"]}
        data.update(over)
        self.c.post("/request/corner-shop/", data)
        return QuoteRequest.objects.get(customer_name=name)

    # ---- who sees the tab ----

    def test_me_tells_the_app_whether_to_show_the_requests_tab(self, *_):
        self.assertTrue(api(self.c, self.owner, "get", "me/").json()["features"]["customer_requests"])
        self.assertFalse(api(self.c, self.hosp_owner, "get", "me/").json()["features"]["customer_requests"])

    def test_signed_out_and_hospital_accounts_are_refused(self, *_):
        self.assertEqual(self.c.get("/api/mobile/customer-requests/").status_code, 401)
        resp = api(self.c, self.hosp_owner, "get", "customer-requests/")
        self.assertEqual((resp.status_code, resp.json()["code"]), (403, "unavailable"))

    # ---- the list ----

    def test_list_opens_on_today_with_counts_and_amounts(self, *_):
        req = self._submit()
        data = api(self.c, self.owner, "get", "customer-requests/").json()
        self.assertEqual(data["day"]["key"], "today")
        self.assertEqual(data["counts"]["total"], 1)
        self.assertEqual(data["waiting_total"], 1)
        self.assertEqual(data["currency"], "INR")
        row = data["requests"][0]
        self.assertEqual((row["number"], row["customer_name"], row["item_count"], row["status"]),
                         (req.number, "Ravi", 2, "PENDING"))
        self.assertEqual(row["amount"], "0.00")

    def test_list_filters_search_and_day_work_like_the_web_page(self, *_):
        self._submit("Ravi")
        self._submit("Meena")
        by = lambda **p: [r["customer_name"] for r in api(self.c, self.owner, "get", "customer-requests/?" + "&".join(
            f"{k}={v}" for k, v in p.items())).json()["requests"]]
        self.assertEqual(by(q="meena"), ["Meena"])
        self.assertEqual(by(day="yesterday"), [])
        self.assertEqual(by(day="all"), ["Ravi", "Meena"])
        self.assertEqual(by(status="QUOTED"), [])

    def test_list_only_shows_your_own_organization(self, *_):
        self._submit()
        other = make_org("other-shop", Organization.BusinessType.RESTAURANTS_FOOD)
        other_owner = User.objects.create_user(
            email="o@other.example", password="x-pass-123", organization=other, role=User.Role.OWNER)
        self.assertEqual(api(self.c, other_owner, "get", "customer-requests/").json()["requests"], [])
        self.assertEqual(api(self.c, other_owner, "get", "customer-requests/1/").status_code, 404)

    # ---- one request, quoting, payment ----

    def test_detail_has_items_total_and_a_version(self, *_):
        req = self._submit()
        data = api(self.c, self.owner, "get", f"customer-requests/{req.number}/").json()
        self.assertEqual([i["product_name"] for i in data["items"]], ["Colgate paste", "Sugar"])
        self.assertEqual((data["items"][0]["qty"], data["items"][0]["unit_label"]), ("2", "pc"))
        self.assertEqual(data["request"]["customer_phone"], "9876543210")
        self.assertTrue(data["request"]["version"])
        self.assertIsNone(data["request"]["pdf_link"])           # only once quoted
        self.assertEqual(api(self.c, self.owner, "get", "customer-requests/99/").status_code, 404)

    def _quote_body(self, req, send, **over):
        a, b = req.items.all()
        body = {"send": send, "version": api(self.c, self.owner, "get", f"customer-requests/{req.number}/").json()["request"]["version"],
                "items": [{"id": a.pk, "available": True, "price": "90", "remark": ""},
                          {"id": b.pk, "available": True, "price": "45", "remark": "fresh"}]}
        body.update(over)
        return body

    def test_quote_saves_a_draft_then_sends(self, *_):
        req = self._submit()
        draft = api(self.c, self.owner, "post", f"customer-requests/{req.number}/quote/", self._quote_body(req, False))
        self.assertEqual(draft.status_code, 200)
        self.assertEqual(draft.json()["request"]["status"], "PENDING")
        self.assertEqual(draft.json()["items"][1]["remark"], "fresh")

        sent = api(self.c, self.owner, "post", f"customer-requests/{req.number}/quote/", self._quote_body(req, True))
        self.assertEqual(sent.status_code, 200)
        self.assertEqual((sent.json()["request"]["status"], sent.json()["request"]["total"]), ("QUOTED", "405.00"))
        self.assertTrue(sent.json()["request"]["pdf_link"].endswith("/quote.pdf"))
        self.assertEqual(sent.json()["items"][0]["line_total"], "180.00")

    def test_quote_refuses_bad_input(self, *_):
        req = self._submit()
        a, b = req.items.all()
        post = lambda body: api(self.c, self.owner, "post", f"customer-requests/{req.number}/quote/", body)
        self.assertEqual(post({"items": "nope"}).status_code, 400)
        self.assertEqual(post({"send": True, "items": [{"id": a.pk, "available": True, "price": "1"}]}).status_code, 400)  # item b missing
        self.assertEqual(post({"send": True, "items": [{"id": 999999, "available": True}]}).status_code, 400)
        no_price = post({"send": True, "items": [{"id": a.pk, "available": True, "price": ""},
                                                  {"id": b.pk, "available": True, "price": "5"}]})
        self.assertEqual(no_price.status_code, 400)
        self.assertIn("enter a price", no_price.json()["error"])
        req.refresh_from_db()
        self.assertEqual(req.status, QuoteRequest.Status.PENDING)

    def test_quote_is_refused_when_the_customer_changed_the_list(self, *_):
        from django.utils import timezone

        req = self._submit()
        seen = api(self.c, self.owner, "get", f"customer-requests/{req.number}/").json()
        old_body = {"send": True, "version": seen["request"]["version"],
                    "items": [{"id": i["id"], "available": True, "price": "10"} for i in seen["items"]]}

        self.c.post(f"/r/{req.tracking_token}/edit/", {          # the customer replaces the whole list
            "name": "Ravi", "phone": "9876543210", "product": ["Rice"], "qty": ["1"], "unit": ["kg"]})
        resp = api(self.c, self.owner, "post", f"customer-requests/{req.number}/quote/", old_body)
        self.assertEqual(resp.status_code, 400)                  # the owner's item ids no longer exist
        req.refresh_from_db()
        self.assertEqual(req.status, QuoteRequest.Status.PENDING)

        fresh = api(self.c, self.owner, "get", f"customer-requests/{req.number}/").json()
        QuoteRequest.objects.filter(pk=req.pk).update(edited_at=timezone.now())      # changed again after the owner looked
        body = {"send": True, "version": fresh["request"]["version"],
                "items": [{"id": fresh["items"][0]["id"], "available": True, "price": "10"}]}
        resp = api(self.c, self.owner, "post", f"customer-requests/{req.number}/quote/", body)
        self.assertEqual((resp.status_code, resp.json()["code"]), (409, "stale_list"))
        req.refresh_from_db()
        self.assertEqual(req.status, QuoteRequest.Status.PENDING)

    def test_payment_books_and_removes_the_sale(self, *_):
        req = self._submit()
        before = api(self.c, self.owner, "post", f"customer-requests/{req.number}/payment/", {"payment": "paid"})
        self.assertEqual(before.status_code, 400)                # not quoted yet
        api(self.c, self.owner, "post", f"customer-requests/{req.number}/quote/", self._quote_body(req, True))

        paid = api(self.c, self.owner, "post", f"customer-requests/{req.number}/payment/", {"payment": "paid", "mode": "BANK"})
        self.assertEqual((paid.status_code, paid.json()["request"]["payment_status"]), (200, "PAID"))
        sale = SalesEntry.objects.get()
        self.assertEqual((sale.amount, sale.payment_mode), (Decimal("405.00"), "BANK"))

        pending = api(self.c, self.owner, "post", f"customer-requests/{req.number}/payment/", {"payment": "pending"})
        self.assertEqual(pending.json()["request"]["payment_status"], "PENDING")
        self.assertEqual(SalesEntry.objects.count(), 0)

    # ---- registering a phone ----

    def test_a_phone_registers_moves_to_the_latest_user_and_signs_out(self, *_):
        manager = User.objects.create_user(
            email="m@shop.example", password="x-pass-123", organization=self.shop, role=User.Role.MANAGER)
        manager.manager_pages = ["daily_report"]
        manager.save()
        self.assertEqual(api(self.c, self.owner, "post", "devices/", {"token": "tok-1", "platform": "android"}).status_code, 200)
        self.assertEqual(DeviceToken.objects.get(token="tok-1").user, self.owner)
        api(self.c, manager, "post", "devices/", {"token": "tok-1", "platform": "android"})   # same phone, new user
        self.assertEqual(DeviceToken.objects.count(), 1)
        self.assertEqual(DeviceToken.objects.get().user, manager)
        api(self.c, self.owner, "delete", "devices/", {"token": "tok-1"})                       # not theirs any more
        self.assertEqual(DeviceToken.objects.count(), 1)
        api(self.c, manager, "delete", "devices/", {"token": "tok-1"})
        self.assertEqual(DeviceToken.objects.count(), 0)

    def test_bad_or_unavailable_device_registrations_are_refused(self, *_):
        self.assertEqual(api(self.c, self.owner, "post", "devices/", {"token": ""}).status_code, 400)
        self.assertEqual(api(self.c, self.owner, "post", "devices/", {"token": "x" * 300}).status_code, 400)
        self.assertEqual(api(self.c, self.hosp_owner, "post", "devices/", {"token": "t"}).status_code, 403)
        self.assertEqual(DeviceToken.objects.count(), 0)


def fake_service_account():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
    return key, {"client_email": "push@proj.iam.gserviceaccount.com", "private_key": pem, "project_id": "finday-test"}


def reply(status=200, body=None, text=""):
    resp = mock.Mock(status_code=status, text=text or json.dumps(body or {}))
    resp.json.return_value = body or {}
    resp.raise_for_status.return_value = None
    return resp


class PushSenderTests(TestCase):
    def setUp(self):
        push._cached.update(email=None, token=None, expires=0.0)
        self.key, self.creds = fake_service_account()

    def test_push_is_off_without_a_service_account(self):
        with mock.patch.object(push, "config", return_value=""):
            self.assertIsNone(push.credentials())
            self.assertFalse(push.is_enabled())
        with mock.patch.object(push, "config", side_effect=lambda name, default="": "not json" if name == "FCM_SERVICE_ACCOUNT_JSON" else ""):
            self.assertIsNone(push.credentials())
        with mock.patch.object(push, "config", side_effect=lambda name, default="": json.dumps({"client_email": "x"}) if name == "FCM_SERVICE_ACCOUNT_JSON" else ""):
            self.assertIsNone(push.credentials())

    def test_service_account_is_read_from_the_environment(self):
        with mock.patch.object(push, "config", side_effect=lambda name, default="": json.dumps(self.creds) if name == "FCM_SERVICE_ACCOUNT_JSON" else ""):
            self.assertEqual(push.credentials()["project_id"], "finday-test")
            self.assertTrue(push.is_enabled())

    def test_sends_a_signed_login_then_one_message_per_phone_and_reuses_the_login(self):
        calls = []

        def fake_post(url, **kwargs):
            calls.append((url, kwargs))
            if url == push.GOOGLE_TOKEN_URL:
                return reply(body={"access_token": "ACCESS", "expires_in": 3600})
            return reply(body={"name": "projects/x/messages/1"})

        with mock.patch.object(push, "credentials", return_value=self.creds), \
                mock.patch.object(push.requests, "post", side_effect=fake_post):
            push.send_to_tokens(["tok-a", "tok-b"], "New request #7", "Ravi - 2 items", {"type": "customer_request", "number": 7})
            push.send_to_tokens(["tok-c"], "T", "B", {})

        logins = [c for c in calls if c[0] == push.GOOGLE_TOKEN_URL]
        messages = [c for c in calls if c[0] != push.GOOGLE_TOKEN_URL]
        self.assertEqual(len(logins), 1)                                    # the access token was reused
        self.assertEqual(len(messages), 3)

        # The login assertion is a real RS256 signature over the right claims.
        assertion = logins[0][1]["data"]["assertion"]
        head, claim, sig = assertion.split(".")
        pad = lambda s: s + "=" * (-len(s) % 4)
        claims = json.loads(base64.urlsafe_b64decode(pad(claim)))
        self.assertEqual((claims["iss"], claims["aud"], claims["scope"]),
                         ("push@proj.iam.gserviceaccount.com", push.GOOGLE_TOKEN_URL, push.FCM_SCOPE))
        self.key.public_key().verify(base64.urlsafe_b64decode(pad(sig)), f"{head}.{claim}".encode(),
                                     padding.PKCS1v15(), hashes.SHA256())    # raises if the signature is wrong

        url, kwargs = messages[0]
        self.assertEqual(url, "https://fcm.googleapis.com/v1/projects/finday-test/messages:send")
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer ACCESS")
        sent = kwargs["json"]["message"]
        self.assertEqual(sent["token"], "tok-a")
        self.assertEqual(sent["notification"], {"title": "New request #7", "body": "Ravi - 2 items"})
        self.assertEqual(sent["data"], {"type": "customer_request", "number": "7"})   # data values are strings
        self.assertEqual(sent["android"]["notification"]["channel_id"], push.CHANNEL_ID)

    def test_a_phone_that_uninstalled_the_app_is_forgotten_but_other_errors_are_not(self):
        user = User.objects.create_user(email="u@x.example", password="x-pass-123")
        for t in ("gone", "flaky", "fine"):
            DeviceToken.objects.create(user=user, token=t)

        def fake_post(url, **kwargs):
            if url == push.GOOGLE_TOKEN_URL:
                return reply(body={"access_token": "A", "expires_in": 3600})
            token = kwargs["json"]["message"]["token"]
            return {"gone": reply(404, text='{"error":{"status":"NOT_FOUND","details":[{"errorCode":"UNREGISTERED"}]}}'),
                    "flaky": reply(500, text="boom")}.get(token, reply())

        with mock.patch.object(push, "credentials", return_value=self.creds), \
                mock.patch.object(push.requests, "post", side_effect=fake_post):
            push.send_to_tokens(["gone", "flaky", "fine"], "T", "B", {})
        self.assertEqual(sorted(DeviceToken.objects.values_list("token", flat=True)), ["fine", "flaky"])

    def test_a_google_login_failure_never_raises(self):
        with mock.patch.object(push, "credentials", return_value=self.creds), \
                mock.patch.object(push.requests, "post", side_effect=OSError("no network")):
            push.send_to_tokens(["tok"], "T", "B", {})        # must simply return


@mock.patch("apps.billing.services.is_account_suspended", return_value=False)
@mock.patch("apps.billing.services.has_active_access", return_value=True)
class PushOnNewRequestTests(TestCase):
    def setUp(self):
        cache.clear()
        push._cached.update(email=None, token=None, expires=0.0)
        self.shop = make_org("corner-shop", Organization.BusinessType.RETAIL_ECOMMERCE)
        self.other = make_org("other-shop", Organization.BusinessType.RETAIL_ECOMMERCE)
        QuoteRequestSettings.objects.create(organization=self.shop, is_enabled=True)
        self.owner = User.objects.create_user(
            email="o@shop.example", password="x-pass-123", organization=self.shop, role=User.Role.OWNER)
        self.manager = User.objects.create_user(
            email="m@shop.example", password="x-pass-123", organization=self.shop, role=User.Role.MANAGER)
        self.stranger = User.objects.create_user(
            email="o@other.example", password="x-pass-123", organization=self.other, role=User.Role.OWNER)
        DeviceToken.objects.create(user=self.owner, token="owner-phone")
        DeviceToken.objects.create(user=self.manager, token="manager-phone")
        DeviceToken.objects.create(user=self.stranger, token="stranger-phone")
        _key, self.creds = fake_service_account()

    def _submit(self):
        return Client().post("/request/corner-shop/", {
            "name": "Ravi", "phone": "9876543210", "product": ["Rice", "Dal"], "qty": ["1", "2"], "unit": ["kg", "kg"]})

    def _pushed_to(self, post):
        return sorted(c.kwargs["json"]["message"]["token"] for c in post.call_args_list if c.args[0] != push.GOOGLE_TOKEN_URL)

    def test_a_new_request_pushes_to_the_shops_phones_only(self, *_):
        def fake_post(url, **kwargs):
            return reply(body={"access_token": "A", "expires_in": 3600}) if url == push.GOOGLE_TOKEN_URL else reply()

        with mock.patch.object(push, "credentials", return_value=self.creds), \
                mock.patch.object(push.requests, "post", side_effect=fake_post) as post:
            self._submit()
        self.assertEqual(self._pushed_to(post), ["manager-phone", "owner-phone"])      # not the other shop's
        message = next(c.kwargs["json"]["message"] for c in post.call_args_list if c.args[0] != push.GOOGLE_TOKEN_URL)
        self.assertEqual(message["notification"], {"title": "New request #1", "body": "Ravi - 2 items"})
        self.assertEqual(message["data"]["number"], "1")

    def test_an_edited_list_pushes_an_update(self, *_):
        self._submit()
        req = QuoteRequest.objects.get()
        with mock.patch.object(push, "credentials", return_value=self.creds), \
                mock.patch.object(push.requests, "post", side_effect=lambda url, **k: reply(
                    body={"access_token": "A", "expires_in": 3600}) if url == push.GOOGLE_TOKEN_URL else reply()) as post:
            Client().post(f"/r/{req.tracking_token}/edit/", {
                "name": "Ravi", "phone": "9876543210", "product": ["Rice"], "qty": ["3"], "unit": ["kg"]})
        message = next(c.kwargs["json"]["message"] for c in post.call_args_list if c.args[0] != push.GOOGLE_TOKEN_URL)
        self.assertEqual(message["notification"]["title"], "Request #1 updated")
        self.assertEqual(message["data"]["updated"], "1")

    def test_without_firebase_set_up_nothing_is_sent_and_the_customer_is_fine(self, *_):
        with mock.patch.object(push, "credentials", return_value=None), \
                mock.patch.object(push.requests, "post", side_effect=AssertionError("must not call Google")):
            resp = self._submit()
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(QuoteRequest.objects.count(), 1)

    def test_a_push_failure_never_breaks_the_customers_submit(self, *_):
        with mock.patch.object(push, "credentials", return_value=self.creds), \
                mock.patch.object(push.requests, "post", side_effect=OSError("network down")):
            resp = self._submit()
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(QuoteRequest.objects.count(), 1)

    def test_an_account_without_access_is_not_pushed_to(self, *_):
        QuoteRequestSettings.objects.filter(organization=self.shop).update(is_enabled=True)
        Organization.objects.filter(pk=self.shop.pk).update(allowed_pages=["daily_report"])   # customer_requests not allowed
        with mock.patch.object(push, "credentials", return_value=self.creds), \
                mock.patch.object(push.requests, "post", side_effect=AssertionError("must not call Google")):
            self.assertEqual(self._submit().status_code, 302)
