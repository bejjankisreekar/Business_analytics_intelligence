import datetime
from decimal import Decimal
from unittest import mock

from django.test import Client, TestCase

from apps.accounts.models import User
from apps.finance.models import Category, ExpenseEntry, SalesEntry
from apps.organizations.models import Organization
from apps.organizations.tenant_context import set_current_organization


class MobileApiTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(
            name="Shop", slug="shop", organization_code="SHOP01",
            storage_mode=Organization.StorageMode.OUR_DATABASE,
        )
        set_current_organization(self.org)
        self.addCleanup(set_current_organization, None)
        self.user = User.objects.create_user(
            email="owner@shop.example", password="testpass-123", organization=self.org, role=User.Role.OWNER,
        )
        mock.patch("apps.billing.services.has_active_access", return_value=True).start()
        mock.patch("apps.billing.services.is_account_suspended", return_value=False).start()
        mock.patch("apps.billing.services.historical_window_start", return_value=None).start()
        self.addCleanup(mock.patch.stopall)
        self.sales = Category.objects.create(organization=self.org, kind=Category.Kind.SALES, name="Walk-in")
        self.rent = Category.objects.create(organization=self.org, kind=Category.Kind.EXPENSE, name="Rent")
        self.client = Client()

    def _login(self):
        res = self.client.post(
            "/api/mobile/login/",
            {"email": "owner@shop.example", "password": "testpass-123"},
            content_type="application/json",
        )
        self.assertEqual(res.status_code, 200)
        return {"HTTP_AUTHORIZATION": f"Bearer {res.json()['token']}"}

    def test_login_rejects_bad_password(self):
        res = self.client.post(
            "/api/mobile/login/", {"email": "owner@shop.example", "password": "nope"},
            content_type="application/json",
        )
        self.assertEqual(res.status_code, 401)

    def test_me_returns_the_token_owner(self):
        res = self.client.get("/api/mobile/me/", **self._login())
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["user"]["email"], "owner@shop.example")
        self.assertEqual(res.json()["organization"]["name"], "Shop")
        self.assertEqual(self.client.get("/api/mobile/me/").status_code, 401)

    def test_endpoints_need_a_token(self):
        self.assertEqual(self.client.get("/api/mobile/entry-options/").status_code, 401)

    def test_options_list_categories(self):
        res = self.client.get("/api/mobile/entry-options/", **self._login())
        self.assertEqual(res.status_code, 200)
        self.assertEqual([c["name"] for c in res.json()["categories"]["sale"]], ["Walk-in"])
        self.assertEqual([c["name"] for c in res.json()["categories"]["expense"]], ["Rent"])

    def test_create_sale_and_expense_then_list(self):
        auth = self._login()
        today = datetime.date.today().isoformat()
        sale = self.client.post(
            "/api/mobile/entries/",
            {"type": "sale", "date": today, "category": str(self.sales.id), "gross_amount": "500", "discount": "50"},
            content_type="application/json", **auth,
        )
        self.assertEqual(sale.status_code, 201, sale.content)
        self.assertEqual(SalesEntry.objects.get().amount, Decimal("450.00"))
        self.assertEqual(SalesEntry.objects.get().created_by_email, "owner@shop.example")

        expense = self.client.post(
            "/api/mobile/entries/",
            {"type": "expense", "date": today, "category": str(self.rent.id), "amount": "1200"},
            content_type="application/json", **auth,
        )
        self.assertEqual(expense.status_code, 201, expense.content)
        self.assertEqual(ExpenseEntry.objects.get().amount, Decimal("1200"))

        listed = self.client.get(f"/api/mobile/entries/?date={today}", **auth).json()["entries"]
        self.assertEqual({e["type"] for e in listed}, {"sale", "expense"})

    def test_missing_category_is_a_validation_error(self):
        res = self.client.post(
            "/api/mobile/entries/", {"type": "expense", "amount": "10"},
            content_type="application/json", **self._login(),
        )
        self.assertEqual(res.status_code, 400)
        self.assertIn("category", res.json()["fields"])

    def test_password_change_invalidates_token(self):
        auth = self._login()
        self.user.set_password("another-pass-456")
        self.user.save()
        self.assertEqual(self.client.get("/api/mobile/entry-options/", **auth).status_code, 401)

    def test_dashboard_totals(self):
        auth = self._login()
        today = datetime.date.today()
        SalesEntry.objects.create(organization=self.org, date=today, channel=self.sales, gross_amount=500, amount=500)
        ExpenseEntry.objects.create(organization=self.org, date=today, category=self.rent, amount=120)
        data = self.client.get("/api/mobile/dashboard/", **auth).json()
        self.assertEqual(Decimal(data["today"]["net"]), Decimal("380"))
        self.assertEqual(Decimal(data["month"]["sale"]), Decimal("500"))
        self.assertEqual(len(data["last_7_days"]), 7)
        self.assertEqual(data["top_categories"]["expense"][0]["name"], "Rent")

    def test_report_endpoints(self):
        auth = self._login()
        today = datetime.date.today()
        SalesEntry.objects.create(organization=self.org, date=today, channel=self.sales, gross_amount=500, amount=500)
        ExpenseEntry.objects.create(organization=self.org, date=today, category=self.rent, amount=120)

        dash = self.client.get("/api/mobile/dashboard/", **auth).json()
        self.assertEqual(Decimal(dash["kpis"]["net_profit"]), Decimal("380"))
        self.assertEqual(dash["expense_breakdown"][0]["name"], "Rent")
        self.assertEqual(len(dash["recent_entries"]), 2)

        daily = self.client.get("/api/mobile/daily-performance/", **auth).json()
        self.assertEqual(Decimal(daily["totals"]["net"]), Decimal("380"))
        self.assertEqual(daily["days"][0]["date"], today.isoformat())

        intel = self.client.get("/api/mobile/intelligence/?period=this_month", **auth).json()
        self.assertEqual(intel["revenue_by_channel"][0]["name"], "Walk-in")
        self.assertEqual(len(intel["weekday_averages"]), 7)
        self.assertEqual(self.client.get("/api/mobile/intelligence/?period=bogus", **auth).status_code, 200)

        self.assertEqual(len(self.client.get("/api/mobile/monthly-summary/", **auth).json()["months"]), 12)
        cash = self.client.get("/api/mobile/cash-position/", **auth)
        self.assertEqual(cash.status_code, 200, cash.content)
        self.assertIn("cash_flow", cash.json())

    def test_more_report_endpoints(self):
        auth = self._login()
        for path in ("financial-reports/", "bank-accounts/", "vendors/", "aging/"):
            response = self.client.get(f"/api/mobile/{path}", **auth)
            self.assertEqual(response.status_code, 200, (path, response.content))
        self.assertIn("pnl", self.client.get("/api/mobile/financial-reports/", **auth).json())

    def test_detail_endpoints(self):
        auth = self._login()
        today = datetime.date.today()
        SalesEntry.objects.create(organization=self.org, date=today, channel=self.sales, gross_amount=500, amount=500)
        day = self.client.get(f"/api/mobile/day-detail/?date={today.isoformat()}", **auth).json()
        self.assertEqual(Decimal(day["totals"]["sale"]), Decimal("500"))
        self.assertEqual(day["entries"]["sale"][0]["category"], "Walk-in")
        cat = self.client.get("/api/mobile/category-detail/?kind=sale&name=Walk-in", **auth).json()
        self.assertEqual(cat["count"], 1)
        self.assertEqual(Decimal(cat["total"]), Decimal("500"))
        self.assertEqual(self.client.get("/api/mobile/category-detail/?kind=x", **auth).status_code, 400)
        missing = "/api/mobile/ledger/?kind=bank&id=00000000-0000-0000-0000-000000000000"
        self.assertEqual(self.client.get(missing, **auth).status_code, 404)
        months = self.client.get("/api/mobile/monthly-summary/", **auth).json()["months"]
        self.assertIn("from", months[0])
