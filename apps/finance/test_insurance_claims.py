import datetime
from decimal import Decimal
from unittest import mock

from django.test import SimpleTestCase, TestCase

from apps.finance.claim_views import FOLLOW_UP_DAYS, _is_overdue, _summary
from apps.finance.forms import ClaimPaymentForm, InsuranceClaimForm, _bill_label
from apps.finance.models import InsuranceClaim, KickbackEntry
from apps.organizations.models import Organization
from apps.organizations.tenant_context import set_current_organization

Status = InsuranceClaim.Status


def _claim(**overrides):
    """An unsaved claim: the model maths below never touches the database."""
    fields = dict(
        claimed_amount=Decimal("50000"), approved_amount=Decimal("0"),
        received_amount=Decimal("0"), deduction_amount=Decimal("0"),
        status=Status.SUBMITTED, submitted_date=None,
    )
    fields.update(overrides)
    return InsuranceClaim(**fields)


class ClaimMathTests(SimpleTestCase):
    def test_outstanding_is_approved_less_received_and_deductions(self):
        claim = _claim(approved_amount=Decimal("50000"), received_amount=Decimal("30000"),
                       deduction_amount=Decimal("5000"), status=Status.APPROVED)
        self.assertEqual(claim.outstanding_amount, Decimal("15000"))

    def test_open_claim_has_money_still_to_collect(self):
        claim = _claim(approved_amount=Decimal("40000"), received_amount=Decimal("10000"), status=Status.APPROVED)
        self.assertTrue(claim.is_open)

    def test_fully_paid_or_closed_claims_are_not_open(self):
        paid = _claim(approved_amount=Decimal("40000"), received_amount=Decimal("40000"), status=Status.APPROVED)
        self.assertFalse(paid.is_open)
        rejected = _claim(approved_amount=Decimal("0"), status=Status.REJECTED)
        self.assertFalse(rejected.is_open)
        settled = _claim(approved_amount=Decimal("40000"), received_amount=Decimal("0"), status=Status.SETTLED)
        self.assertFalse(settled.is_open)

    def test_draft_claim_is_not_open_even_with_amounts(self):
        claim = _claim(approved_amount=Decimal("1000"), status=Status.DRAFT)
        self.assertFalse(claim.is_open)

    def test_insurer_display_prefers_the_tpa(self):
        claim = _claim(insurer_name="Star Health", tpa_name="Medi Assist")
        self.assertEqual(claim.insurer_display, "Medi Assist")
        self.assertEqual(_claim(insurer_name="Star Health").insurer_display, "Star Health")
        self.assertEqual(_claim().insurer_display, "—")


class OverdueTests(SimpleTestCase):
    def setUp(self):
        self.today = datetime.date(2026, 10, 12)

    def test_open_claim_past_follow_up_window_is_overdue(self):
        claim = _claim(approved_amount=Decimal("1000"), status=Status.SUBMITTED,
                       submitted_date=self.today - datetime.timedelta(days=FOLLOW_UP_DAYS + 1))
        self.assertTrue(_is_overdue(claim, self.today))

    def test_recent_claim_is_not_overdue(self):
        claim = _claim(approved_amount=Decimal("1000"), status=Status.SUBMITTED,
                       submitted_date=self.today - datetime.timedelta(days=5))
        self.assertFalse(_is_overdue(claim, self.today))

    def test_claim_without_submitted_date_is_not_overdue(self):
        claim = _claim(approved_amount=Decimal("1000"), status=Status.SUBMITTED)
        self.assertIsNone(claim.days_since_submitted(self.today))
        self.assertFalse(_is_overdue(claim, self.today))

    def test_settled_claim_is_never_overdue(self):
        claim = _claim(approved_amount=Decimal("1000"), status=Status.SETTLED,
                       submitted_date=self.today - datetime.timedelta(days=200))
        self.assertFalse(_is_overdue(claim, self.today))


class SummaryTests(SimpleTestCase):
    def test_totals_count_outstanding_only_for_open_claims(self):
        today = datetime.date(2026, 10, 12)
        open_old = _claim(claimed_amount=Decimal("100"), approved_amount=Decimal("100"),
                          received_amount=Decimal("20"), status=Status.APPROVED,
                          submitted_date=today - datetime.timedelta(days=FOLLOW_UP_DAYS + 5))
        settled = _claim(claimed_amount=Decimal("50"), approved_amount=Decimal("50"),
                         received_amount=Decimal("50"), status=Status.SETTLED)
        summary = _summary([open_old, settled], today)
        self.assertEqual(summary["claimed"], Decimal("150"))
        self.assertEqual(summary["approved"], Decimal("150"))
        self.assertEqual(summary["received"], Decimal("70"))
        self.assertEqual(summary["outstanding"], Decimal("80"))
        self.assertEqual(summary["open_count"], 1)
        self.assertEqual(summary["overdue_count"], 1)


class InsuranceClaimFormTests(TestCase):
    """The bill is a required dropdown, so these need a real bill row."""

    def setUp(self):
        self.org = Organization.objects.create(
            name="Form Hospital", slug="form-hospital", organization_code="FRMHSP",
            storage_mode=Organization.StorageMode.OUR_DATABASE,
        )
        set_current_organization(self.org)
        self.addCleanup(set_current_organization, None)
        self.bill = KickbackEntry.objects.create(
            bill_number="BL-1", admission_date=datetime.date(2026, 10, 1), final_amount=Decimal("60000"),
            payer_type=KickbackEntry.PayerType.INSURANCE,
        )

    def _form(self, **data):
        base = {
            "bill": str(self.bill.pk), "claim_type": "CLAIM", "status": "SUBMITTED", "insurer_name": "Star Health",
            "claimed_amount": "50000", "approved_amount": "40000",
        }
        base.update(data)
        with mock.patch("apps.finance.forms._used_insurer_names", return_value=[]):
            return InsuranceClaimForm(data=base)

    def test_bill_is_mandatory(self):
        form = self._form(bill="")
        self.assertFalse(form.is_valid())
        self.assertIn("bill", form.errors)

    def test_valid_claim_passes(self):
        form = self._form()
        self.assertTrue(form.is_valid(), form.errors)

    def test_blank_amounts_are_saved_as_zero_not_null(self):
        form = self._form(claimed_amount="", approved_amount="")
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["claimed_amount"], Decimal("0"))
        self.assertEqual(form.cleaned_data["approved_amount"], Decimal("0"))

    def test_approved_cannot_exceed_claimed(self):
        form = self._form(approved_amount="70000")
        self.assertFalse(form.is_valid())
        self.assertIn("approved_amount", form.errors)

    def test_needs_an_insurer_or_a_tpa(self):
        form = self._form(insurer_name="", tpa_name="")
        self.assertFalse(form.is_valid())
        self.assertIn("insurer_name", form.errors)

    def test_tpa_alone_is_enough(self):
        form = self._form(insurer_name="", tpa_name="Medi Assist")
        self.assertTrue(form.is_valid(), form.errors)

    def test_decision_cannot_be_before_submission(self):
        form = self._form(submitted_date="2026-10-10", decision_date="2026-10-01")
        self.assertFalse(form.is_valid())
        self.assertIn("decision_date", form.errors)


class ClaimPaymentFormTests(SimpleTestCase):
    def _form(self, claim, **data):
        base = {"date": "2026-10-12", "amount": "", "deduction": "", "reference": "", "note": ""}
        base.update(data)
        return ClaimPaymentForm(data=base, claim=claim)

    def test_payment_needs_an_amount_or_a_deduction(self):
        form = self._form(_claim(approved_amount=Decimal("1000")))
        self.assertFalse(form.is_valid())

    def test_deduction_alone_is_recorded(self):
        form = self._form(_claim(approved_amount=Decimal("1000")), deduction="200")
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["amount"], Decimal("0"))

    def test_cannot_settle_more_than_approved(self):
        claim = _claim(approved_amount=Decimal("1000"), received_amount=Decimal("700"))
        form = self._form(claim, amount="400")
        self.assertFalse(form.is_valid())

    def test_payment_within_the_approved_amount_is_accepted(self):
        claim = _claim(approved_amount=Decimal("1000"), received_amount=Decimal("700"))
        form = self._form(claim, amount="200", deduction="100")
        self.assertTrue(form.is_valid(), form.errors)

    def test_no_approved_amount_yet_means_no_cap(self):
        form = self._form(_claim(approved_amount=Decimal("0")), amount="5000")
        self.assertTrue(form.is_valid(), form.errors)


class BillLabelTests(SimpleTestCase):
    def test_label_is_mrn_name_address(self):
        bill = KickbackEntry(bill_number="BL-1", mrn="MRN-42", patient_name="Ramesh", patient_address="Kothapalli")
        self.assertEqual(_bill_label(bill), "MRN-42 - Ramesh - Kothapalli")

    def test_missing_mrn_falls_back_to_bill_number(self):
        bill = KickbackEntry(bill_number="BL-1", patient_name="Ramesh")
        self.assertEqual(_bill_label(bill), "BL-1 - Ramesh")
