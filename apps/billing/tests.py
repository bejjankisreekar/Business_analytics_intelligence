import datetime
import hashlib
import hmac
import json
from decimal import Decimal

from django.test import Client, TestCase, override_settings

from apps.organizations.models import Organization
from apps.organizations.services import create_organization_with_tenant_schema_and_admin, delete_organization_and_tenant

from . import invoicing
from . import payments as payment_services
from .models import Invoice, Payment, PaymentWebhookEvent


def _make_org(env="default", name="Billing Test Org"):
    org, _owner = create_organization_with_tenant_schema_and_admin(
        org_data={"name": name, "business_type": Organization.BusinessType.RETAIL},
        admin_data={"email": f"owner-{name.lower().replace(' ', '-')}@billingtest.example", "password": "pw12345678"},
        using=env,
    )
    return org


class InvoiceLifecycleTests(TestCase):
    def setUp(self):
        self.org = _make_org()

    def tearDown(self):
        delete_organization_and_tenant(self.org)

    def test_create_invoice_computes_total_and_amount_due(self):
        invoice = invoicing.create_invoice(self.org, subtotal=1000, discount=100, tax=90)
        self.assertEqual(invoice.total, Decimal("990.00"))
        self.assertEqual(invoice.amount_due, Decimal("990.00"))
        self.assertTrue(invoice.invoice_number.startswith("INV-"))
        self.assertEqual(invoice.status, Invoice.Status.ISSUED)

    def test_full_payment_marks_invoice_paid(self):
        invoice = invoicing.create_invoice(self.org, subtotal=1000, discount=0, tax=0)
        payment_services.record_payment(self.org, invoice=invoice, amount=invoice.total, status=Payment.Status.SUCCESS)
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, Invoice.Status.PAID)
        self.assertEqual(invoice.amount_due, Decimal("0.00"))

    def test_partial_payment_marks_invoice_partially_paid(self):
        invoice = invoicing.create_invoice(self.org, subtotal=1000, discount=0, tax=0)
        payment_services.record_payment(self.org, invoice=invoice, amount=400, status=Payment.Status.SUCCESS)
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, Invoice.Status.PARTIALLY_PAID)
        self.assertEqual(invoice.amount_paid, Decimal("400.00"))
        self.assertEqual(invoice.amount_due, Decimal("600.00"))

    def test_overdue_when_due_date_passed_and_unpaid(self):
        invoice = invoicing.create_invoice(
            self.org, subtotal=500, discount=0, tax=0,
            invoice_date=datetime.date(2020, 1, 1), due_date=datetime.date(2020, 1, 8),
        )
        invoicing.refresh_invoice_status(invoice)
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, Invoice.Status.OVERDUE)

    def test_cancelled_invoice_status_is_not_overwritten_by_refresh(self):
        invoice = invoicing.create_invoice(self.org, subtotal=500, discount=0, tax=0)
        invoicing.cancel_invoice(invoice)
        invoicing.refresh_invoice_status(invoice)
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, Invoice.Status.CANCELLED)

    def test_refund_reduces_invoice_amount_paid_and_updates_status(self):
        invoice = invoicing.create_invoice(self.org, subtotal=1000, discount=0, tax=0)
        payment, _ = payment_services.record_payment(self.org, invoice=invoice, amount=1000, status=Payment.Status.SUCCESS)
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, Invoice.Status.PAID)

        payment_services.refund_payment(payment, amount=1000, reason="client requested")
        payment.refresh_from_db()
        invoice.refresh_from_db()
        self.assertEqual(payment.status, Payment.Status.REFUNDED)
        self.assertEqual(invoice.amount_paid, Decimal("0.00"))
        self.assertEqual(invoice.status, Invoice.Status.ISSUED)

    def test_partial_refund_sets_partially_refunded_status(self):
        invoice = invoicing.create_invoice(self.org, subtotal=1000, discount=0, tax=0)
        payment, _ = payment_services.record_payment(self.org, invoice=invoice, amount=1000, status=Payment.Status.SUCCESS)
        payment_services.refund_payment(payment, amount=300, reason="partial goodwill refund")
        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.Status.PARTIALLY_REFUNDED)
        self.assertEqual(payment.refunded_amount, Decimal("300.00"))


class PaymentIdempotencyTests(TestCase):
    def setUp(self):
        self.org = _make_org(name="Idempotency Org")

    def tearDown(self):
        delete_organization_and_tenant(self.org)

    def test_record_payment_with_same_transaction_id_does_not_duplicate(self):
        p1, created1 = payment_services.record_payment(
            self.org, amount=500, gateway=Payment.Gateway.RAZORPAY, transaction_id="pay_dup_1",
            status=Payment.Status.SUCCESS,
        )
        p2, created2 = payment_services.record_payment(
            self.org, amount=500, gateway=Payment.Gateway.RAZORPAY, transaction_id="pay_dup_1",
            status=Payment.Status.SUCCESS,
        )
        self.assertTrue(created1)
        self.assertFalse(created2)
        self.assertEqual(p1.pk, p2.pk)
        self.assertEqual(Payment.objects.filter(transaction_id="pay_dup_1").count(), 1)

    def test_blank_transaction_id_does_not_trigger_dedup(self):
        # Manual/off-platform payments legitimately have no transaction id —
        # the uniqueness constraint is scoped to non-blank values only.
        p1, c1 = payment_services.record_payment(self.org, amount=100, gateway=Payment.Gateway.MANUAL)
        p2, c2 = payment_services.record_payment(self.org, amount=200, gateway=Payment.Gateway.MANUAL)
        self.assertTrue(c1)
        self.assertTrue(c2)
        self.assertNotEqual(p1.pk, p2.pk)


class PaymentWebhookEventIdempotencyTests(TestCase):
    def setUp(self):
        self.org = _make_org(name="Webhook Org")

    def tearDown(self):
        delete_organization_and_tenant(self.org)

    def test_process_webhook_event_twice_creates_one_payment_and_one_event(self):
        payment1, processed1 = payment_services.process_webhook_event(
            Payment.Gateway.RAZORPAY, "evt_001", {"raw": True},
            organization=self.org, transaction_id="txn_001", amount=750,
        )
        payment2, processed2 = payment_services.process_webhook_event(
            Payment.Gateway.RAZORPAY, "evt_001", {"raw": True},
            organization=self.org, transaction_id="txn_001", amount=750,
        )
        self.assertTrue(processed1)
        self.assertFalse(processed2)  # second delivery is a recognized no-op
        self.assertEqual(payment1.pk, payment2.pk)
        self.assertEqual(Payment.objects.filter(transaction_id="txn_001").count(), 1)
        self.assertEqual(PaymentWebhookEvent.objects.filter(event_id="evt_001").count(), 1)

    def test_missing_organization_marks_event_failed_without_creating_payment(self):
        payment, processed = payment_services.process_webhook_event(
            Payment.Gateway.RAZORPAY, "evt_no_org", {}, organization=None, transaction_id="txn_no_org", amount=100,
        )
        self.assertIsNone(payment)
        self.assertFalse(processed)
        event = PaymentWebhookEvent.objects.get(event_id="evt_no_org")
        self.assertEqual(event.status, PaymentWebhookEvent.Status.FAILED)
        self.assertEqual(Payment.objects.filter(transaction_id="txn_no_org").count(), 0)


class PaymentWebhookViewTests(TestCase):
    def setUp(self):
        self.org = _make_org(name="Webhook View Org")

    def tearDown(self):
        delete_organization_and_tenant(self.org)

    def _post(self, gateway, payload, headers=None):
        return Client().post(
            f"/billing/webhooks/{gateway}/",
            data=json.dumps(payload),
            content_type="application/json",
            **(headers or {}),
        )

    def _razorpay_payload(self, event_id, transaction_id, amount_rupees, status="captured"):
        return {
            "event": "payment.captured",
            "payload": {
                "payment": {
                    "entity": {
                        "id": transaction_id,
                        "amount": int(Decimal(amount_rupees) * 100),
                        "currency": "INR",
                        "status": status,
                        "notes": {"organization_code": self.org.organization_code},
                    }
                }
            },
        }

    def _signed_headers(self, raw_body: bytes):
        secret = "test-webhook-secret"
        signature = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
        return secret, {"HTTP_X_RAZORPAY_SIGNATURE": signature}

    def test_unknown_gateway_rejected(self):
        resp = self._post("unknown-gateway", {"event_id": "e1"})
        self.assertEqual(resp.status_code, 400)

    def test_missing_event_id_rejected(self):
        resp = self._post("razorpay", {"organization_code": self.org.organization_code})
        self.assertEqual(resp.status_code, 400)

    def test_unsigned_razorpay_webhook_rejected(self):
        payload = self._razorpay_payload("evt_unsigned", "txn_unsigned", "250.00")
        resp = self._post("razorpay", payload)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(Payment.objects.filter(transaction_id="txn_unsigned").count(), 0)

    def test_valid_webhook_creates_payment(self):
        payload = self._razorpay_payload("evt_view_1", "txn_view_1", "250.00")
        raw_body = json.dumps(payload).encode("utf-8")
        secret, headers = self._signed_headers(raw_body)
        with override_settings(RAZORPAY_WEBHOOK_SECRET=secret):
            resp = self._post("razorpay", payload, headers={**headers, "HTTP_X_RAZORPAY_EVENT_ID": "evt_view_1"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(Payment.objects.filter(transaction_id="txn_view_1").count(), 1)

    def test_duplicate_webhook_delivery_does_not_duplicate_payment(self):
        payload = self._razorpay_payload("evt_view_dup", "txn_view_dup", "300.00")
        raw_body = json.dumps(payload).encode("utf-8")
        secret, headers = self._signed_headers(raw_body)
        request_headers = {**headers, "HTTP_X_RAZORPAY_EVENT_ID": "evt_view_dup"}
        with override_settings(RAZORPAY_WEBHOOK_SECRET=secret):
            resp1 = self._post("razorpay", payload, headers=request_headers)
            resp2 = self._post("razorpay", payload, headers=request_headers)
        self.assertEqual(resp1.status_code, 200)
        self.assertEqual(resp2.status_code, 200)
        self.assertEqual(Payment.objects.filter(transaction_id="txn_view_dup").count(), 1)
        self.assertEqual(PaymentWebhookEvent.objects.filter(event_id="evt_view_dup").count(), 1)


class MultiAliasBillingTests(TestCase):
    """Confirms the `using` parameter actually routes to the given
    database alias, matching how superadmin views call these same
    functions for "dev"/"prod"."""

    databases = {"default", "dev", "prod"}

    def setUp(self):
        self.org = _make_org(env="dev", name="Alias Billing Org")

    def tearDown(self):
        delete_organization_and_tenant(self.org, using="dev")

    def test_invoice_and_payment_created_on_explicit_alias(self):
        invoice = invoicing.create_invoice(self.org, using="dev", subtotal=1000, discount=0, tax=0)
        payment, created = payment_services.record_payment(
            self.org, using="dev", invoice=invoice, amount=1000, status=Payment.Status.SUCCESS
        )
        self.assertTrue(created)
        self.assertEqual(Invoice.objects.using("dev").filter(pk=invoice.pk).count(), 1)
        self.assertEqual(Payment.objects.using("dev").filter(pk=payment.pk).count(), 1)
        # Must NOT have leaked onto "default"/"prod".
        self.assertEqual(Invoice.objects.using("prod").filter(invoice_number=invoice.invoice_number).count(), 0)
