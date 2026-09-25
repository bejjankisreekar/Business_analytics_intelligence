import datetime
from decimal import Decimal

from django.test import Client, TestCase

from apps.billing import invoicing
from apps.billing import payments as payment_services
from apps.billing import services as billing_services
from apps.billing.models import Invoice, Payment, Plan
from apps.finance import services as finance_services
from apps.finance.models import (
    BankAccount,
    BankChoices,
    CashTransfer,
    Category,
    Customer,
    ExpenseEntry,
    FinanceSettings,
    Partner,
    PartnerTransaction,
    Payable,
    PaymentMode,
    PurchaseEntry,
    Receivable,
    SalesEntry,
    Subcategory,
)
from apps.finance.periods import Period
from apps.organizations.models import Organization
from apps.organizations.services import create_organization_with_tenant_schema_and_admin, delete_organization_and_tenant
from apps.organizations.utils import schema_context


class BillingPageTests(TestCase):
    """The client's own view of their subscription/invoices/payments —
    apps.billing models live on the default alias (public schema), same as
    Organization/User, so this needs no schema_context."""

    def setUp(self):
        self.org, self.owner = create_organization_with_tenant_schema_and_admin(
            org_data={"name": "Billing Page Test Org", "business_type": Organization.BusinessType.RETAIL_ECOMMERCE},
            admin_data={"email": "owner@billingpagetest.example", "password": "ownerpass123"},
        )
        self.client_ = Client()
        self.client_.force_login(self.owner)

    def tearDown(self):
        delete_organization_and_tenant(self.org)

    def test_billing_page_loads_with_no_data(self):
        resp = self.client_.get("/app/billing/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "No invoices yet.")
        self.assertNotContains(resp, "Outstanding balance")

    def test_billing_page_shows_pending_invoice_and_outstanding_banner(self):
        invoice = invoicing.create_invoice(self.org, subtotal=1000, discount=0, tax=180)
        resp = self.client_.get("/app/billing/")
        self.assertContains(resp, invoice.invoice_number)
        self.assertContains(resp, "Outstanding balance")
        self.assertContains(resp, "1,180")

    def test_billing_page_hides_banner_once_invoice_is_fully_paid(self):
        invoice = invoicing.create_invoice(self.org, subtotal=500, discount=0, tax=0)
        payment_services.record_payment(self.org, invoice=invoice, amount=500, status=Payment.Status.SUCCESS)
        resp = self.client_.get("/app/billing/")
        self.assertContains(resp, invoice.invoice_number)
        self.assertNotContains(resp, "Outstanding balance")

    def test_billing_page_shows_recorded_payment(self):
        invoice = invoicing.create_invoice(self.org, subtotal=200, discount=0, tax=0)
        payment_services.record_payment(self.org, invoice=invoice, amount=200, status=Payment.Status.SUCCESS)
        resp = self.client_.get("/app/billing/")
        self.assertContains(resp, "200")

    def test_other_organizations_invoices_are_not_visible(self):
        other_org, other_owner = create_organization_with_tenant_schema_and_admin(
            org_data={"name": "Other Billing Org", "business_type": Organization.BusinessType.RETAIL_ECOMMERCE},
            admin_data={"email": "owner@otherbillingorg.example", "password": "ownerpass123"},
        )
        try:
            other_invoice = invoicing.create_invoice(other_org, subtotal=999, discount=0, tax=0)
            resp = self.client_.get("/app/billing/")
            self.assertNotContains(resp, other_invoice.invoice_number)
        finally:
            delete_organization_and_tenant(other_org)


class BalanceSheetTests(TestCase):
    """services.balance_sheet must satisfy Assets == Liabilities + Equity
    at all times — regression coverage for the bug where partner capital
    counted toward total_assets (via cash_and_bank_as_of) but not toward
    total_equity, so the sheet silently failed to balance whenever any
    partner had invested or withdrawn capital."""

    def setUp(self):
        self.org, self.owner = create_organization_with_tenant_schema_and_admin(
            org_data={"name": "Balance Sheet Test Org", "business_type": Organization.BusinessType.RETAIL_ECOMMERCE},
            admin_data={"email": "owner@balancesheettest.example", "password": "ownerpass123"},
        )

    def tearDown(self):
        delete_organization_and_tenant(self.org)

    def test_balance_sheet_balances_with_partner_capital(self):
        with schema_context(self.org.schema_name):
            FinanceSettings.objects.update(
                opening_balance=Decimal("1000"), opening_bank_balance=Decimal("0"),
                opening_date=datetime.date(2024, 4, 1),
            )
            partner = Partner.objects.create(name="Alice")
            PartnerTransaction.objects.create(
                partner=partner, date=datetime.date(2024, 4, 5), kind=PartnerTransaction.Kind.INVESTMENT,
                amount=Decimal("5000"), payment_mode=PaymentMode.BANK,
            )
            PartnerTransaction.objects.create(
                partner=partner, date=datetime.date(2024, 5, 1), kind=PartnerTransaction.Kind.WITHDRAWAL,
                amount=Decimal("1200"), payment_mode=PaymentMode.CASH,
            )

            sheet = finance_services.balance_sheet(datetime.date(2024, 6, 1))
            self.assertTrue(sheet["balances"])
            self.assertEqual(sheet["total_assets"], sheet["total_equity"])
            self.assertEqual(sheet["partner_capital"], Decimal("3800"))


class GstSummaryTests(TestCase):
    """Output tax (sales) and input tax credit (purchases) are computed
    from each entry's channel/category `gst_rate`, rate-wise, with
    uncategorized/0%-rated entries reported separately as untaxed rather
    than silently folded into the tax totals."""

    def setUp(self):
        self.org, self.owner = create_organization_with_tenant_schema_and_admin(
            org_data={"name": "GST Test Org", "business_type": Organization.BusinessType.RETAIL_ECOMMERCE},
            admin_data={"email": "owner@gsttest.example", "password": "ownerpass123"},
        )

    def tearDown(self):
        delete_organization_and_tenant(self.org)

    def test_gst_summary_nets_output_against_input_tax(self):
        with schema_context(self.org.schema_name):
            channel_18 = Category.objects.create(kind=Category.Kind.SALES, name="Taxed Channel", gst_rate=Decimal("18"))
            purchase_12 = Category.objects.create(kind=Category.Kind.PURCHASE, name="Stock", gst_rate=Decimal("12"))
            untaxed_channel = Category.objects.create(kind=Category.Kind.SALES, name="Cash counter")

            period = Period(datetime.date(2024, 4, 1), datetime.date(2024, 4, 30), "April 2024", "custom")

            SalesEntry.objects.create(date=datetime.date(2024, 4, 5), channel=channel_18, amount=Decimal("1000"))
            SalesEntry.objects.create(date=datetime.date(2024, 4, 10), channel=untaxed_channel, amount=Decimal("500"))
            PurchaseEntry.objects.create(date=datetime.date(2024, 4, 6), category=purchase_12, amount=Decimal("400"))

            summary = finance_services.gst_summary(period)
            self.assertEqual(summary["output_tax"], Decimal("180.00"))
            self.assertEqual(summary["input_tax"], Decimal("48.00"))
            self.assertEqual(summary["net_gst_payable"], Decimal("132.00"))
            self.assertEqual(summary["untaxed_sales"], Decimal("500"))
            self.assertEqual(summary["taxable_purchases"], Decimal("400"))


class AgingReportTests(TestCase):
    """receivables_aging/payables_aging bucket every open balance by how
    overdue it is, per customer/vendor, and the bucket totals must add up
    to the same outstanding total Cash Position already shows."""

    def setUp(self):
        self.org, self.owner = create_organization_with_tenant_schema_and_admin(
            org_data={"name": "Aging Test Org", "business_type": Organization.BusinessType.RETAIL_ECOMMERCE},
            admin_data={"email": "owner@agingtest.example", "password": "ownerpass123"},
        )

    def tearDown(self):
        delete_organization_and_tenant(self.org)

    def test_receivables_aging_buckets_by_days_overdue(self):
        with schema_context(self.org.schema_name):
            customer = Customer.objects.create(name="Acme Retail")
            as_of = datetime.date(2024, 6, 30)
            Receivable.objects.create(
                customer=customer, invoice_date=datetime.date(2024, 6, 1),
                due_date=datetime.date(2024, 6, 20), amount=Decimal("1000"),
            )
            Receivable.objects.create(
                customer=customer, invoice_date=datetime.date(2024, 3, 1),
                due_date=datetime.date(2024, 3, 15), amount=Decimal("2000"),
            )

            aging = finance_services.receivables_aging(as_of)
            self.assertEqual(len(aging["rows"]), 1)
            row = aging["rows"][0]
            self.assertEqual(row["d1_30"], Decimal("1000"))
            self.assertEqual(row["d90_plus"], Decimal("2000"))
            self.assertEqual(row["total"], Decimal("3000"))
            self.assertEqual(aging["totals"]["total"], finance_services.receivables_total_outstanding())

    def test_payables_aging_current_bucket_for_not_yet_due(self):
        with schema_context(self.org.schema_name):
            as_of = datetime.date(2024, 6, 30)
            Payable.objects.create(
                vendor="Supplier Co", bill_date=datetime.date(2024, 6, 25),
                due_date=datetime.date(2024, 7, 15), amount=Decimal("750"),
            )

            aging = finance_services.payables_aging(as_of)
            row = aging["rows"][0]
            self.assertEqual(row["current"], Decimal("750"))
            self.assertEqual(sum(row[b] for b, _ in aging["buckets"] if b != "current"), Decimal("0"))


class AccessLocksAutomaticallyOnLapseTests(TestCase):
    """The billing period ending (Subscription.end_date, shown as 'Billing
    end' in superadmin) must lock the org out of the app on its own —
    no superadmin action required — until a real payment is recorded.
    Enforced by TenantLoginRequiredMixin.dispatch() reading
    billing_services.has_active_access() on every request; see
    apps/finance/views.py."""

    def setUp(self):
        self.org, self.owner = create_organization_with_tenant_schema_and_admin(
            org_data={"name": "Lapse Lock Test Org", "business_type": Organization.BusinessType.RETAIL_ECOMMERCE},
            admin_data={"email": "owner@lapselocktest.example", "password": "ownerpass123"},
        )
        plan = Plan.objects.get(slug="enterprise")  # non-zero price, so a lapse actually has something to invoice
        sub = billing_services.start_trial(self.org, plan)
        # Simulate a billing period that already ended, as if it were never renewed.
        sub.status = sub.Status.ACTIVE
        sub.end_date = datetime.date.today() - datetime.timedelta(days=1)
        sub.save()
        self.client_ = Client()
        self.client_.force_login(self.owner)

    def tearDown(self):
        delete_organization_and_tenant(self.org)

    def test_lapsed_billing_period_bounces_every_page_to_billing(self):
        self.assertTrue(self.org.is_service_active)  # not manually suspended — the lock is automatic
        resp = self.client_.get("/app/", follow=True)
        self.assertRedirects(resp, "/app/billing/")

    def test_lapse_auto_generates_a_payable_invoice(self):
        self.assertEqual(Invoice.objects.filter(organization_id=self.org.id).count(), 0)
        self.client_.get("/app/")
        self.assertEqual(Invoice.objects.filter(organization_id=self.org.id).count(), 1)

    def test_billing_page_itself_stays_reachable_while_locked(self):
        resp = self.client_.get("/app/billing/")
        self.assertEqual(resp.status_code, 200)

    def test_paying_the_invoice_lifts_the_lock_immediately(self):
        self.client_.get("/app/")  # generates the invoice
        invoice = Invoice.objects.get(organization_id=self.org.id)
        payment_services.record_payment(self.org, invoice=invoice, amount=invoice.amount_due, status=Payment.Status.SUCCESS)
        self.assertTrue(billing_services.has_active_access(self.org))
        resp = self.client_.get("/app/")
        self.assertEqual(resp.status_code, 200)


class NewPagesSmokeTests(TestCase):
    """Basic 200-status coverage for the new pages/exports — catches
    template/URL wiring mistakes (bad {% url %} names, undefined context
    variables) that unit-testing the services layer alone wouldn't."""

    def setUp(self):
        self.org, self.owner = create_organization_with_tenant_schema_and_admin(
            org_data={"name": "Smoke Test Org", "business_type": Organization.BusinessType.RETAIL_ECOMMERCE},
            admin_data={"email": "owner@smoketest.example", "password": "ownerpass123"},
        )
        billing_services.start_trial(self.org, Plan.objects.get(slug="free"))
        self.client_ = Client()
        self.client_.force_login(self.owner)

    def tearDown(self):
        delete_organization_and_tenant(self.org)

    def test_aging_report_page_loads(self):
        resp = self.client_.get("/app/cash-position/aging/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Aging Report")

    def test_reports_page_shows_gst_tab(self):
        resp = self.client_.get("/app/reports/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "GST Summary")

    def test_gst_pdf_downloads(self):
        resp = self.client_.get("/app/reports/gst-summary.pdf")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "application/pdf")

    def test_category_form_accepts_gst_rate(self):
        resp = self.client_.post(
            "/app/categories/add/", {"kind": "SALES", "name": "Export Sales", "gst_rate": "18"}
        )
        self.assertEqual(resp.status_code, 302)
        with schema_context(self.org.schema_name):
            self.assertEqual(Category.objects.get(name="Export Sales").gst_rate, Decimal("18"))

    def test_expense_category_saves_without_gst_rate_field_present(self):
        """Expense/Product category forms never render a `gst_rate` input
        (see categories.html), so the POST body legitimately omits it —
        this must still save cleanly with gst_rate defaulting to 0."""
        resp = self.client_.post("/app/categories/add/", {"kind": "EXPENSE", "name": "Office Supplies"})
        self.assertEqual(resp.status_code, 302)
        with schema_context(self.org.schema_name):
            category = Category.objects.get(name="Office Supplies")
            self.assertEqual(category.gst_rate, Decimal("0"))


class TransferEditDeleteTests(TestCase):
    """Cash/bank transfers are logged from the Daily Report and corrected
    there too — a mistyped deposit must be fixable without touching the DB."""

    def setUp(self):
        self.org, self.owner = create_organization_with_tenant_schema_and_admin(
            org_data={"name": "Transfer Test Org", "business_type": Organization.BusinessType.RETAIL_ECOMMERCE},
            admin_data={"email": "owner@transfertest.example", "password": "ownerpass123"},
        )
        billing_services.start_trial(self.org, Plan.objects.get(slug="free"))
        self.client_ = Client()
        self.client_.force_login(self.owner)
        self.day = datetime.date.today()
        with schema_context(self.org.schema_name):
            self.transfer = CashTransfer.objects.create(
                date=self.day, direction=CashTransfer.Direction.CASH_TO_BANK, amount=Decimal("500"), note="orig"
            )

    def tearDown(self):
        delete_organization_and_tenant(self.org)

    def _get(self):
        with schema_context(self.org.schema_name):
            return CashTransfer.objects.get(pk=self.transfer.pk)

    def test_daily_report_offers_edit_and_delete(self):
        resp = self.client_.get(f"/app/daily/?date={self.day:%Y-%m-%d}")
        self.assertContains(resp, f"/app/transfers/{self.transfer.pk}/edit/")
        self.assertContains(resp, f"/app/transfers/{self.transfer.pk}/delete/")

    def test_edit_updates_the_transfer(self):
        resp = self.client_.post(f"/app/transfers/{self.transfer.pk}/edit/", {
            "date": self.day, "direction": CashTransfer.Direction.BANK_TO_CASH, "amount": "750", "note": "fixed",
        })
        self.assertEqual(resp.status_code, 302)
        edited = self._get()
        self.assertEqual(edited.amount, Decimal("750"))
        self.assertEqual(edited.direction, CashTransfer.Direction.BANK_TO_CASH)
        self.assertEqual(edited.note, "fixed")

    def test_invalid_edit_leaves_the_transfer_unchanged(self):
        self.client_.post(f"/app/transfers/{self.transfer.pk}/edit/", {
            "date": self.day, "direction": CashTransfer.Direction.CASH_TO_BANK, "amount": "", "note": "x",
        })
        self.assertEqual(self._get().amount, Decimal("500"))

    def test_delete_removes_the_transfer(self):
        resp = self.client_.post(f"/app/transfers/{self.transfer.pk}/delete/")
        self.assertEqual(resp.status_code, 302)
        with schema_context(self.org.schema_name):
            self.assertFalse(CashTransfer.objects.filter(pk=self.transfer.pk).exists())

    def test_delete_requires_post(self):
        resp = self.client_.get(f"/app/transfers/{self.transfer.pk}/delete/")
        self.assertEqual(resp.status_code, 405)
        self.assertEqual(self._get().amount, Decimal("500"))


class LedgerEditDeleteTests(TestCase):
    """Ledger rows are derived from underlying records, so each ledger offers
    edit/delete on the record behind a row — with guards where money has
    already moved (a paid invoice can't be deleted or re-homed)."""

    def setUp(self):
        self.org, self.owner = create_organization_with_tenant_schema_and_admin(
            org_data={"name": "Ledger Test Org", "business_type": Organization.BusinessType.RETAIL_ECOMMERCE},
            admin_data={"email": "owner@ledgertest.example", "password": "ownerpass123"},
        )
        billing_services.start_trial(self.org, Plan.objects.get(slug="free"))
        self.client_ = Client()
        self.client_.force_login(self.owner)
        today = datetime.date.today()
        with schema_context(self.org.schema_name):
            self.customer = Customer.objects.create(name="Acme Buyer")
            self.other_customer = Customer.objects.create(name="Someone Else")
            self.unpaid = Receivable.objects.create(
                customer=self.customer, invoice_date=today, due_date=today, amount=Decimal("1000"))
            self.paid = Receivable.objects.create(
                customer=self.customer, invoice_date=today, due_date=today, amount=Decimal("2000"),
                amount_received=Decimal("500"))
            self.bill = Payable.objects.create(vendor="Sup Ltd", bill_date=today, due_date=today, amount=Decimal("800"))
            self.paid_bill = Payable.objects.create(
                vendor="Sup Ltd", bill_date=today, due_date=today, amount=Decimal("900"), amount_paid=Decimal("300"))
            self.partner = Partner.objects.create(name="P One", opening_balance_as_on=today)
            self.txn = PartnerTransaction.objects.create(
                partner=self.partner, date=today, kind="INVESTMENT", amount=Decimal("100"), payment_mode="CASH")
        self.today = today

    def tearDown(self):
        delete_organization_and_tenant(self.org)

    def _reload(self, obj):
        with schema_context(self.org.schema_name):
            return type(obj).objects.filter(pk=obj.pk).first()

    def _invoice_post(self, obj, **over):
        data = {"customer": self.customer.pk, "invoice_date": self.today, "due_date": self.today,
                "amount": "1500", "note": "edited"}
        data.update(over)
        return self.client_.post(f"/app/receivables/{obj.pk}/edit/", data)

    def test_ledger_rows_carry_their_source_record(self):
        with schema_context(self.org.schema_name):
            rows = finance_services.customer_ledger_entries(self.customer)
        roles = sorted((r["source"]["kind"], r["source"]["role"]) for r in rows)
        self.assertEqual(roles, [("receivable", "invoice")] * 2 + [("receivable", "payment")])

    def test_edit_unpaid_invoice(self):
        self.assertEqual(self._invoice_post(self.unpaid).status_code, 302)
        edited = self._reload(self.unpaid)
        self.assertEqual(edited.amount, Decimal("1500"))
        self.assertEqual(edited.note, "edited")

    def test_cannot_drop_invoice_total_below_amount_received(self):
        self._invoice_post(self.paid, amount="400")
        self.assertEqual(self._reload(self.paid).amount, Decimal("2000"))

    def test_customer_is_locked_once_payments_exist(self):
        self._invoice_post(self.paid, customer=self.other_customer.pk, amount="2500")
        after = self._reload(self.paid)
        self.assertEqual(after.customer_id, self.customer.pk)
        self.assertEqual(after.amount, Decimal("2500"))

    def test_delete_unpaid_invoice_and_return_to_ledger(self):
        ledger = f"/app/ledgers/customers/{self.customer.pk}/"
        resp = self.client_.post(f"/app/receivables/{self.unpaid.pk}/delete/", {"next": ledger})
        self.assertRedirects(resp, ledger, fetch_redirect_response=False)
        self.assertIsNone(self._reload(self.unpaid))

    def test_delete_paid_invoice_is_refused(self):
        self.client_.post(f"/app/receivables/{self.paid.pk}/delete/")
        self.assertIsNotNone(self._reload(self.paid))

    def test_edit_and_guards_on_bills(self):
        self.client_.post(f"/app/payables/{self.bill.pk}/edit/", {
            "vendor": "Sup Ltd", "bill_date": self.today, "due_date": self.today, "amount": "850", "note": "n"})
        self.assertEqual(self._reload(self.bill).amount, Decimal("850"))
        self.client_.post(f"/app/payables/{self.paid_bill.pk}/edit/", {
            "vendor": "Changed Vendor", "bill_date": self.today, "due_date": self.today, "amount": "200", "note": ""})
        untouched = self._reload(self.paid_bill)
        self.assertEqual((untouched.vendor, untouched.amount), ("Sup Ltd", Decimal("900")))
        self.client_.post(f"/app/payables/{self.paid_bill.pk}/delete/")
        self.assertIsNotNone(self._reload(self.paid_bill))

    def test_edit_and_delete_partner_transaction(self):
        ledger = f"/app/cash-position/partners/{self.partner.pk}/"
        resp = self.client_.post(f"/app/partners/transactions/{self.txn.pk}/edit/", {
            "partner": self.partner.pk, "date": self.today, "kind": "WITHDRAWAL", "amount": "250",
            "payment_mode": "BANK", "note": "fixed", "next": ledger})
        self.assertRedirects(resp, ledger, fetch_redirect_response=False)
        edited = self._reload(self.txn)
        self.assertEqual((edited.kind, edited.amount, edited.payment_mode), ("WITHDRAWAL", Decimal("250"), "BANK"))
        self.client_.post(f"/app/partners/transactions/{self.txn.pk}/delete/", {"next": ledger})
        self.assertIsNone(self._reload(self.txn))

    def test_ledger_pages_render_with_actions(self):
        for url, marker in [
            (f"/app/ledgers/customers/{self.customer.pk}/", f"/app/receivables/{self.unpaid.pk}/edit/"),
            ("/app/ledgers/vendors/" + str(self._vendor_pk()) + "/", f"/app/payables/{self.bill.pk}/edit/"),
            (f"/app/cash-position/partners/{self.partner.pk}/", f"/app/partners/transactions/{self.txn.pk}/edit/"),
        ]:
            resp = self.client_.get(url)
            self.assertEqual(resp.status_code, 200, url)
            self.assertContains(resp, marker)
        self.assertEqual(self.client_.get("/app/ledgers/cash/").status_code, 200)
        self.assertEqual(self.client_.get("/app/ledgers/bank/").status_code, 200)

    def _vendor_pk(self):
        from apps.finance.models import Vendor
        with schema_context(self.org.schema_name):
            return Vendor.objects.get_or_create(name="Sup Ltd")[0].pk


class CostTreeTests(TestCase):
    """The drill-down behind Cost Intelligence: category >
    sub-category > name, where every level must add up to its parent."""

    def setUp(self):
        self.org, self.owner = create_organization_with_tenant_schema_and_admin(
            org_data={"name": "Cost Tree Org", "business_type": Organization.BusinessType.RETAIL_ECOMMERCE},
            admin_data={"email": "owner@costtree.example", "password": "ownerpass123"},
        )
        billing_services.start_trial(self.org, Plan.objects.get(slug="free"))
        self.client_ = Client()
        self.client_.force_login(self.owner)
        self.day = datetime.date.today()
        with schema_context(self.org.schema_name):
            self.pay = Category.objects.create(kind="EXPENSE", name="Salaries </script>")
            self.dept = Subcategory.objects.create(category=self.pay, name="Nursing")
            self.emp = Subcategory.objects.create(category=self.pay, parent=self.dept, name="Anita")
            self.emp2 = Subcategory.objects.create(category=self.pay, parent=self.dept, name="Mary")
            self.rent = Category.objects.get_or_create(kind="EXPENSE", name="Rent")[0]
            for cat, sub, amt in [(self.pay, self.emp, 300), (self.pay, self.emp2, 200), (self.pay, self.dept, 50),
                                  (self.pay, None, 25), (self.rent, None, 400)]:
                ExpenseEntry.objects.create(date=self.day, category=cat, subcategory=sub, amount=Decimal(amt))
        self.period = Period(self.day, self.day, "today", "today")

    def tearDown(self):
        delete_organization_and_tenant(self.org)

    def _tree(self):
        with schema_context(self.org.schema_name):
            return finance_services.cost_tree(ExpenseEntry, self.day, self.day)

    def test_levels_sum_and_sort_largest_first(self):
        tree = self._tree()
        self.assertEqual([c["name"] for c in tree], ["Salaries </script>", "Rent"])
        salaries = tree[0]
        self.assertEqual(salaries["amount"], Decimal("575"))
        self.assertEqual(sum(c["amount"] for c in salaries["children"]), Decimal("575"))
        nursing = next(c for c in salaries["children"] if c["name"] == "Nursing")
        self.assertEqual([(c["name"], c["amount"]) for c in nursing["children"]],
                         [("Anita", Decimal("300")), ("Mary", Decimal("200"))])

    def test_untagged_remainder_is_kept_but_a_lone_one_is_not(self):
        tree = self._tree()
        salaries = next(c for c in tree if c["name"].startswith("Salaries"))
        self.assertIn("Not specified", [c["name"] for c in salaries["children"]])
        rent = next(c for c in tree if c["name"] == "Rent")
        self.assertEqual(rent["children"], [])   # only untagged entries: nothing to drill into

    def test_page_renders_and_escapes_script_close_in_names(self):
        resp = self.client_.get("/app/cost-intelligence/?period=today")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Where the money goes")
        # Every chart embeds names via to_json: none may be able to close the <script> block.
        self.assertNotContains(resp, 'Salaries </script>')
        self.assertContains(resp, "Salaries \\u003c/script\\u003e")


class BankAccountTests(TestCase):
    """Multiple named bank accounts (ICICI, HDFC, ...), each with its own
    opening balance and a closing balance computed from whichever
    sales/expenses/purchases/transfers were tagged to it -- purely an
    attribution layer on top of the existing cash/bank split, so it must
    never change cash_and_bank_as_of's totals."""

    def setUp(self):
        self.org, self.owner = create_organization_with_tenant_schema_and_admin(
            org_data={"name": "Bank Account Test Org", "business_type": Organization.BusinessType.RETAIL_ECOMMERCE},
            admin_data={"email": "owner@bankaccounttest.example", "password": "ownerpass123"},
        )
        billing_services.start_trial(self.org, Plan.objects.get(slug="free"))
        self.client_ = Client()
        self.client_.force_login(self.owner)

    def tearDown(self):
        delete_organization_and_tenant(self.org)

    def test_closing_balance_reflects_only_entries_tagged_to_that_account(self):
        with schema_context(self.org.schema_name):
            FinanceSettings.objects.update(
                opening_balance=Decimal("0"), opening_bank_balance=Decimal("0"),
                opening_date=datetime.date(2024, 4, 1),
            )
            icici = BankAccount.objects.create(
                name="Current - ICICI", bank_name=BankChoices.ICICI,
                opening_balance=Decimal("1000"), opening_balance_as_on=datetime.date(2024, 4, 1),
            )
            hdfc = BankAccount.objects.create(
                name="Salary - HDFC", bank_name=BankChoices.HDFC,
                opening_balance=Decimal("500"), opening_balance_as_on=datetime.date(2024, 4, 1),
            )
            SalesEntry.objects.create(
                date=datetime.date(2024, 4, 5), amount=Decimal("2000"),
                payment_mode=PaymentMode.BANK, bank_account=icici,
            )
            ExpenseEntry.objects.create(
                date=datetime.date(2024, 4, 6), amount=Decimal("300"),
                payment_mode=PaymentMode.BANK, bank_account=icici,
            )
            # Untagged BANK-mode entry -- counts in the overall bank total but
            # not against any specific account.
            ExpenseEntry.objects.create(date=datetime.date(2024, 4, 7), amount=Decimal("50"), payment_mode=PaymentMode.BANK)

            as_of = datetime.date(2024, 4, 30)
            self.assertEqual(finance_services.bank_account_balance_as_of(icici, as_of), Decimal("2700"))
            self.assertEqual(finance_services.bank_account_balance_as_of(hdfc, as_of), Decimal("500"))

            # The attribution layer must never change the totals cash_and_bank_as_of computes.
            _, bank_total = finance_services.cash_and_bank_as_of(as_of)
            self.assertEqual(bank_total, Decimal("2000") - Decimal("300") - Decimal("50"))

    def test_bank_accounts_page_lists_accounts_with_closing_balance(self):
        with schema_context(self.org.schema_name):
            BankAccount.objects.create(
                name="Current - ICICI", bank_name=BankChoices.ICICI, opening_balance=Decimal("1500"),
            )
        resp = self.client_.get("/app/bank-accounts/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Current - ICICI")
        self.assertContains(resp, "ICICI Bank")
        self.assertContains(resp, "1,500")

    def test_add_bank_account_requires_other_bank_name_when_other_selected(self):
        resp = self.client_.post("/app/bank-accounts/add/", {
            "name": "Misc Account", "bank_name": BankChoices.OTHER, "other_bank_name": "",
            "account_number": "", "opening_balance": "0", "opening_balance_as_on": "2024-04-01",
        }, follow=True)
        self.assertContains(resp, "Couldn")
        with schema_context(self.org.schema_name):
            self.assertFalse(BankAccount.objects.filter(name="Misc Account").exists())

    def test_add_edit_toggle_delete_bank_account(self):
        resp = self.client_.post("/app/bank-accounts/add/", {
            "name": "Current - HDFC", "bank_name": BankChoices.HDFC, "other_bank_name": "",
            "account_number": "1234", "opening_balance": "100", "opening_balance_as_on": "2024-04-01",
        }, follow=True)
        self.assertContains(resp, "Bank account added.")
        with schema_context(self.org.schema_name):
            account = BankAccount.objects.get(name="Current - HDFC")

        resp = self.client_.post(f"/app/bank-accounts/{account.pk}/edit/", {
            "name": "Current - HDFC", "bank_name": BankChoices.HDFC, "other_bank_name": "",
            "account_number": "5678", "opening_balance": "150", "opening_balance_as_on": "2024-04-01",
        }, follow=True)
        self.assertContains(resp, "Bank account updated.")
        with schema_context(self.org.schema_name):
            account.refresh_from_db()
            self.assertEqual(account.account_number, "5678")
            self.assertEqual(account.opening_balance, Decimal("150"))

        self.client_.post(f"/app/bank-accounts/{account.pk}/toggle/", follow=True)
        with schema_context(self.org.schema_name):
            account.refresh_from_db()
            self.assertFalse(account.is_active)

        resp = self.client_.get(f"/app/bank-accounts/{account.pk}/ledger/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Current - HDFC")

        self.client_.post(f"/app/bank-accounts/{account.pk}/delete/", follow=True)
        with schema_context(self.org.schema_name):
            self.assertFalse(BankAccount.objects.filter(pk=account.pk).exists())

    def test_bulk_sale_row_lets_a_bank_account_be_tagged(self):
        with schema_context(self.org.schema_name):
            icici = BankAccount.objects.create(name="Current - ICICI", bank_name=BankChoices.ICICI)
        day = datetime.date.today()
        resp = self.client_.post("/app/daily/bulk-sales/", {
            "selected_date": day.isoformat(),
            "sale-TOTAL_FORMS": "1", "sale-INITIAL_FORMS": "0",
            "sale-MIN_NUM_FORMS": "0", "sale-MAX_NUM_FORMS": "1000",
            "sale-0-date": day.isoformat(), "sale-0-amount": "500",
            "sale-0-payment_mode": PaymentMode.BANK, "sale-0-bank_account": str(icici.pk),
        }, follow=True)
        self.assertContains(resp, "Logged 1 sale.")
        with schema_context(self.org.schema_name):
            entry = SalesEntry.objects.get(amount=Decimal("500"))
            self.assertEqual(entry.bank_account_id, icici.pk)

    def test_bank_account_is_dropped_when_via_is_cash(self):
        """A stray bank_account on a Cash entry would silently count toward
        that account's ledger even though the money never touched it
        (bank_account_balance_as_of filters by bank_account alone, not
        payment_mode) -- the form must clear it rather than save it."""
        with schema_context(self.org.schema_name):
            icici = BankAccount.objects.create(name="Current - ICICI", bank_name=BankChoices.ICICI)
        day = datetime.date.today()
        resp = self.client_.post("/app/daily/bulk-sales/", {
            "selected_date": day.isoformat(),
            "sale-TOTAL_FORMS": "1", "sale-INITIAL_FORMS": "0",
            "sale-MIN_NUM_FORMS": "0", "sale-MAX_NUM_FORMS": "1000",
            "sale-0-date": day.isoformat(), "sale-0-amount": "750",
            "sale-0-payment_mode": PaymentMode.CASH, "sale-0-bank_account": str(icici.pk),
        }, follow=True)
        self.assertContains(resp, "Logged 1 sale.")
        with schema_context(self.org.schema_name):
            entry = SalesEntry.objects.get(amount=Decimal("750"))
            self.assertIsNone(entry.bank_account_id)

    def test_bulk_expense_and_purchase_rows_also_tag_and_drop_bank_account(self):
        """Same bank_account attribution/clearing behaviour as the Revenue
        bulk row, checked on the other two bulk-entry tables too."""
        with schema_context(self.org.schema_name):
            icici = BankAccount.objects.create(name="Current - ICICI", bank_name=BankChoices.ICICI)
        day = datetime.date.today()

        resp = self.client_.post("/app/daily/bulk-expenses/", {
            "selected_date": day.isoformat(),
            "expense-TOTAL_FORMS": "2", "expense-INITIAL_FORMS": "0",
            "expense-MIN_NUM_FORMS": "0", "expense-MAX_NUM_FORMS": "1000",
            "expense-0-date": day.isoformat(), "expense-0-amount": "200",
            "expense-0-payment_mode": PaymentMode.BANK, "expense-0-bank_account": str(icici.pk),
            "expense-1-date": day.isoformat(), "expense-1-amount": "300",
            "expense-1-payment_mode": PaymentMode.CASH, "expense-1-bank_account": str(icici.pk),
        }, follow=True)
        self.assertContains(resp, "Logged 2 expenses.")
        with schema_context(self.org.schema_name):
            bank_expense = ExpenseEntry.objects.get(amount=Decimal("200"))
            cash_expense = ExpenseEntry.objects.get(amount=Decimal("300"))
            self.assertEqual(bank_expense.bank_account_id, icici.pk)
            self.assertIsNone(cash_expense.bank_account_id)

        resp = self.client_.post("/app/daily/bulk-purchases/", {
            "selected_date": day.isoformat(),
            "purchase-TOTAL_FORMS": "2", "purchase-INITIAL_FORMS": "0",
            "purchase-MIN_NUM_FORMS": "0", "purchase-MAX_NUM_FORMS": "1000",
            "purchase-0-date": day.isoformat(), "purchase-0-amount": "400",
            "purchase-0-payment_mode": PaymentMode.BANK, "purchase-0-bank_account": str(icici.pk),
            "purchase-1-date": day.isoformat(), "purchase-1-amount": "600",
            "purchase-1-payment_mode": PaymentMode.CASH, "purchase-1-bank_account": str(icici.pk),
        }, follow=True)
        self.assertContains(resp, "Logged 2 purchases.")
        with schema_context(self.org.schema_name):
            bank_purchase = PurchaseEntry.objects.get(amount=Decimal("400"))
            cash_purchase = PurchaseEntry.objects.get(amount=Decimal("600"))
            self.assertEqual(bank_purchase.bank_account_id, icici.pk)
            self.assertIsNone(cash_purchase.bank_account_id)
