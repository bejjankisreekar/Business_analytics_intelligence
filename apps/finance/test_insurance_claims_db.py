import datetime
from decimal import Decimal

from django.test import TestCase

from apps.organizations.models import Organization
from apps.organizations.tenant_context import set_current_organization

from .models import ClaimPayment, InsuranceClaim, sync_claim_totals

Status = InsuranceClaim.Status


class ClaimPaymentSyncTests(TestCase):
    """Payments are the source of truth; sync_claim_totals() copies them onto
    the claim. Runs against the real database, in our-own-database mode."""

    def setUp(self):
        self.org = Organization.objects.create(
            name="Test Hospital",
            slug="test-hospital",
            organization_code="TSTHSP",
            storage_mode=Organization.StorageMode.OUR_DATABASE,
        )
        set_current_organization(self.org)
        self.addCleanup(set_current_organization, None)
        self.claim = InsuranceClaim.objects.create(
            insurer_name="Star Health",
            claimed_amount=Decimal("1000"),
            approved_amount=Decimal("1000"),
            status=Status.APPROVED,
            submitted_date=datetime.date(2026, 10, 1),
        )

    def _pay(self, date, amount, deduction="0"):
        ClaimPayment.objects.create(
            claim=self.claim, date=date, amount=Decimal(amount), deduction=Decimal(deduction),
        )
        sync_claim_totals(self.claim.pk)
        self.claim.refresh_from_db()

    def test_partial_payment_updates_totals_and_stays_approved(self):
        self._pay(datetime.date(2026, 10, 5), "600", deduction="50")
        self.assertEqual(self.claim.received_amount, Decimal("600"))
        self.assertEqual(self.claim.deduction_amount, Decimal("50"))
        self.assertEqual(self.claim.last_payment_date, datetime.date(2026, 10, 5))
        self.assertEqual(self.claim.outstanding_amount, Decimal("350"))
        self.assertEqual(self.claim.status, Status.APPROVED)
        self.assertTrue(self.claim.is_open)

    def test_final_payment_settles_the_claim(self):
        self._pay(datetime.date(2026, 10, 5), "600")
        self._pay(datetime.date(2026, 10, 9), "350", deduction="50")
        self.assertEqual(self.claim.outstanding_amount, Decimal("0"))
        self.assertEqual(self.claim.status, Status.SETTLED)
        self.assertEqual(self.claim.last_payment_date, datetime.date(2026, 10, 9))

    def test_removing_a_payment_reopens_the_totals(self):
        self._pay(datetime.date(2026, 10, 5), "600")
        payment = ClaimPayment.objects.get(claim=self.claim)
        self._pay(datetime.date(2026, 10, 9), "400")
        self.assertEqual(self.claim.status, Status.SETTLED)

        payment.delete()
        sync_claim_totals(self.claim.pk)
        self.claim.refresh_from_db()
        self.assertEqual(self.claim.received_amount, Decimal("400"))
        self.assertEqual(self.claim.outstanding_amount, Decimal("600"))
        self.assertEqual(self.claim.status, Status.APPROVED)

    def test_a_claim_with_no_payments_has_zero_totals(self):
        sync_claim_totals(self.claim.pk)
        self.claim.refresh_from_db()
        self.assertEqual(self.claim.received_amount, Decimal("0"))
        self.assertIsNone(self.claim.last_payment_date)
        self.assertEqual(self.claim.status, Status.APPROVED)

    def test_sync_for_a_missing_claim_is_a_no_op(self):
        sync_claim_totals(None)
        sync_claim_totals("00000000-0000-0000-0000-000000000000")
