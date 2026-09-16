from decimal import Decimal

from django.test import Client, TestCase

from apps.billing import invoicing
from apps.billing import payments as payment_services
from apps.billing.models import Payment
from apps.organizations.models import Organization
from apps.organizations.services import create_organization_with_tenant_schema_and_admin, delete_organization_and_tenant


class BillingPageTests(TestCase):
    """The client's own view of their subscription/invoices/payments —
    apps.billing models live on the default alias (public schema), same as
    Organization/User, so this needs no schema_context."""

    def setUp(self):
        self.org, self.owner = create_organization_with_tenant_schema_and_admin(
            org_data={"name": "Billing Page Test Org", "business_type": Organization.BusinessType.RETAIL},
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
            org_data={"name": "Other Billing Org", "business_type": Organization.BusinessType.RETAIL},
            admin_data={"email": "owner@otherbillingorg.example", "password": "ownerpass123"},
        )
        try:
            other_invoice = invoicing.create_invoice(other_org, subtotal=999, discount=0, tax=0)
            resp = self.client_.get("/app/billing/")
            self.assertNotContains(resp, other_invoice.invoice_number)
        finally:
            delete_organization_and_tenant(other_org)
