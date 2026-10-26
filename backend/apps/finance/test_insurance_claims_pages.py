import datetime
from decimal import Decimal
from unittest import mock

from django.test import Client, TestCase

from apps.accounts.models import User
from apps.organizations.models import Organization
from apps.organizations.tenant_context import set_current_organization

from .models import ClaimPayment, InsuranceClaim, KickbackEntry


class InsuranceClaimPagesTests(TestCase):
    """Walks the claim pages end to end in our-own-database mode: the list,
    adding a claim from a bill, recording payments, and deleting."""

    def setUp(self):
        self.org = Organization.objects.create(
            name="Claims Hospital",
            slug="claims-hospital",
            organization_code="CLMHSP",
            storage_mode=Organization.StorageMode.OUR_DATABASE,
            business_type=Organization.BusinessType.HEALTHCARE,
        )
        set_current_organization(self.org)
        self.addCleanup(set_current_organization, None)
        self.user = User.objects.create_user(
            email="billing@claims.example", password="testpass-123", organization=self.org,
        )
        self.client_ = Client()
        self.client_.force_login(self.user)
        # Plan gating is covered elsewhere; these tests are about the pages.
        access = mock.patch("apps.finance.claim_views.has_kickbacks_access", return_value=True)
        access_views = mock.patch("apps.finance.views.has_kickbacks_access", return_value=True)
        access.start()
        access_views.start()
        # Subscription gate: an org with an active paid period, not a lapsed one.
        mock.patch("apps.billing.services.has_active_access", return_value=True).start()
        mock.patch("apps.billing.services.is_account_suspended", return_value=False).start()
        self.addCleanup(mock.patch.stopall)

        self.bill = KickbackEntry.objects.create(
            bill_number="BL-100",
            patient_name="Ramesh",
            admission_date=datetime.date(2026, 10, 1),
            final_amount=Decimal("60000"),
            payer_type=KickbackEntry.PayerType.INSURANCE,
            insurer_name="Star Health",
            insurer_amount=Decimal("45000"),
            patient_amount=Decimal("15000"),
        )

    def test_list_page_renders(self):
        resp = self.client_.get("/app/insurance-claims/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Insurance &amp; TPA Claims")
        self.assertContains(resp, "Discount")

    def test_add_form_prefills_from_bill(self):
        resp = self.client_.get(f"/app/insurance-claims/add/?bill={self.bill.pk}")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["form"].initial["claimed_amount"], Decimal("45000"))
        self.assertNotIn("patient_name", resp.context["form"].fields)
        form = resp.context["form"]
        for name in ("bill", "claim_type", "status"):  # the pickers, as on the bill form
            self.assertIn("sel-wide", form.fields[name].widget.attrs["class"])

    def test_bad_bill_id_in_url_gives_a_blank_form(self):
        resp = self.client_.get("/app/insurance-claims/add/?bill=not-a-uuid")
        self.assertEqual(resp.status_code, 200)

    def test_claims_section_only_shows_for_insurance_bills(self):
        insurance = self.client_.get(f"/app/patient-bills/{self.bill.pk}/")
        self.assertContains(insurance, "Insurance &amp; TPA claims")
        set_current_organization(self.org)  # a request clears the tenant context
        cash = KickbackEntry.objects.create(
            bill_number="BL-CASH-2", patient_name="Sita", admission_date=datetime.date(2026, 10, 1),
            final_amount=Decimal("500"), payer_type=KickbackEntry.PayerType.CASH,
        )
        resp = self.client_.get(f"/app/patient-bills/{cash.pk}/")
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, "Insurance &amp; TPA claims")

    def test_bill_page_lists_its_claims(self):
        resp = self.client_.get(f"/app/patient-bills/{self.bill.pk}/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Insurance &amp; TPA claims")
        self.assertContains(resp, "No claims for this bill yet.")

    def test_full_claim_lifecycle(self):
        resp = self.client_.post("/app/insurance-claims/add/", {
            "bill": str(self.bill.pk), "claim_type": "CLAIM", "status": "SUBMITTED",
            "claim_number": "CLM-77", "patient_name": "Ramesh", "insurer_name": "",
            "tpa_name": "Medi Assist", "policy_number": "P-9",
            "claimed_amount": "45000", "approved_amount": "40000",
            "submitted_date": "2026-10-02", "decision_date": "", "remarks": "",
        })
        self.assertEqual(resp.status_code, 302)
        claim = InsuranceClaim.objects.get(claim_number="CLM-77")
        self.assertEqual(claim.bill_id, self.bill.pk)
        self.assertEqual(claim.insurer_display, "Medi Assist")
        self.assertEqual(claim.created_by_email, self.user.email)
        self.assertEqual(claim.patient_name, "Ramesh")  # taken from the bill

        detail = self.client_.get(f"/app/insurance-claims/{claim.pk}/")
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, "CLM-77")

        pay = self.client_.post(f"/app/insurance-claims/{claim.pk}/payments/add/", {
            "date": "2026-10-09", "amount": "30000", "deduction": "0",
            "reference": "UTR123", "note": "first instalment",
        })
        self.assertEqual(pay.status_code, 302)
        claim.refresh_from_db()
        self.assertEqual(claim.received_amount, Decimal("30000"))
        self.assertEqual(claim.outstanding_amount, Decimal("10000"))
        self.assertEqual(claim.status, InsuranceClaim.Status.SUBMITTED)

        over = self.client_.post(f"/app/insurance-claims/{claim.pk}/payments/add/", {
            "date": "2026-10-10", "amount": "20000", "deduction": "0",
        })
        self.assertEqual(over.status_code, 200)  # re-rendered with the error
        self.assertContains(over, "more than is left to settle")
        self.assertEqual(ClaimPayment.objects.filter(claim=claim).count(), 1)

        payment = ClaimPayment.objects.get(claim=claim)
        removed = self.client_.post(f"/app/insurance-claims/payments/{payment.pk}/delete/")
        self.assertEqual(removed.status_code, 302)
        claim.refresh_from_db()
        self.assertEqual(claim.received_amount, Decimal("0"))

        self.client_.post(f"/app/insurance-claims/{claim.pk}/payments/add/", {
            "date": "2026-10-10", "amount": "40000", "deduction": "0",
        })
        claim.refresh_from_db()
        self.assertEqual(claim.status, InsuranceClaim.Status.SETTLED)

        listing = self.client_.get("/app/insurance-claims/?status=SETTLED")
        self.assertContains(listing, "CLM-77")
        self.assertNotContains(self.client_.get("/app/insurance-claims/?status=DRAFT"), "CLM-77")

        deleted = self.client_.post(f"/app/insurance-claims/{claim.pk}/delete/")
        self.assertEqual(deleted.status_code, 302)
        self.assertFalse(InsuranceClaim.objects.filter(pk=claim.pk).exists())
        self.assertFalse(ClaimPayment.objects.filter(claim_id=claim.pk).exists())

    def test_bill_form_asks_only_cash_or_insurance(self):
        from .forms import KickbackEntryForm

        fields = set(KickbackEntryForm().fields)
        self.assertIn("payer_type", fields)
        for name in ("insurer_name", "policy_number", "preauth_number",
                     "insurer_amount", "patient_amount", "discount_amount"):
            self.assertNotIn(name, fields)

    def test_switching_a_bill_to_cash_clears_its_insurance_details(self):
        resp = self.client_.post(f"/app/patient-bills/{self.bill.pk}/edit/", {
            "bill_number": "BL-100", "admission_date": "2026-10-01", "final_amount": "60000",
            "payer_type": "CASH",
        })
        self.assertEqual(resp.status_code, 302)
        self.bill.refresh_from_db()
        self.assertEqual(self.bill.payer_type, KickbackEntry.PayerType.CASH)
        self.assertEqual(self.bill.insurer_name, "")
        self.assertEqual(self.bill.insurer_amount, Decimal("0"))
        self.assertEqual(self.bill.patient_amount, Decimal("0"))

    def test_insurance_bill_keeps_its_insurance_details_on_edit(self):
        self.client_.post(f"/app/patient-bills/{self.bill.pk}/edit/", {
            "bill_number": "BL-100", "admission_date": "2026-10-01", "final_amount": "60000",
            "payer_type": "INSURANCE",
        })
        self.bill.refresh_from_db()
        self.assertEqual(self.bill.insurer_name, "Star Health")
        self.assertEqual(self.bill.insurer_amount, Decimal("45000"))

    def test_claim_form_shows_the_bill_amounts(self):
        claim = InsuranceClaim.objects.create(bill=self.bill, insurer_name="Star Health", status=InsuranceClaim.Status.DRAFT)
        resp = self.client_.get(f"/app/insurance-claims/{claim.pk}/edit/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'id="bill-summary"')
        shown = dict(resp.context["bill_amounts"][str(self.bill.pk)])
        shown.pop("final_raw")
        self.assertEqual(shown, {"final": "60,000.00", "insurer": "45,000.00", "patient": "15,000.00"})
        self.assertContains(resp, "60,000.00")

    def test_admission_can_be_saved_before_bill_number_discharge_and_amount_are_known(self):
        resp = self.client_.post("/app/patient-bills/add/", {
            "patient_name": "Lakshmi", "mrn": "MRN-7", "admission_date": "2026-10-12",
            "payer_type": "INSURANCE", "insurer_name": "",
        })
        self.assertEqual(resp.status_code, 302)
        admission = KickbackEntry.objects.get(patient_name="Lakshmi")
        self.assertEqual(admission.bill_number, "")
        self.assertIsNone(admission.discharge_date)
        self.assertEqual(admission.final_amount, Decimal("0"))

    def test_claim_without_a_bill_is_rejected(self):
        resp = self.client_.post("/app/insurance-claims/add/", {
            "claim_type": "CLAIM", "status": "DRAFT", "insurer_name": "Star Health",
            "claimed_amount": "1000",
        })
        self.assertEqual(resp.status_code, 200)
        self.assertIn("bill", resp.context["form"].errors)
        self.assertFalse(InsuranceClaim.objects.exists())

    def test_only_insurance_bills_are_offered(self):
        cash = KickbackEntry.objects.create(
            bill_number="BL-CASH", patient_name="Sita", admission_date=datetime.date(2026, 10, 1),
            final_amount=Decimal("500"), payer_type=KickbackEntry.PayerType.CASH,
        )
        resp = self.client_.get("/app/insurance-claims/add/")
        offered = set(resp.context["form"].fields["bill"].queryset)
        self.assertIn(self.bill, offered)
        self.assertNotIn(cash, offered)

    def test_bill_dropdown_shows_mrn_name_and_address(self):
        self.bill.mrn = "MRN-42"
        self.bill.patient_address = "Kothapalli village"
        self.bill.save()
        resp = self.client_.get("/app/insurance-claims/add/")
        labels = [label for _, label in resp.context["form"].fields["bill"].choices]
        self.assertIn("MRN-42 - Ramesh - Kothapalli village", labels)

    def test_claim_without_insurer_or_tpa_is_rejected(self):
        resp = self.client_.post("/app/insurance-claims/add/", {
            "claim_type": "PREAUTH", "status": "DRAFT", "insurer_name": "", "tpa_name": "",
            "claimed_amount": "1000",
        })
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(InsuranceClaim.objects.exists())


class AdmissionDetailFieldsTests(InsuranceClaimPagesTests):
    def test_admission_saves_its_details(self):
        resp = self.client_.post("/app/patient-bills/add/", {
            "patient_name": "Gopal", "patient_phone": "9876543210", "patient_age": "54",
            "patient_gender": "M", "admission_date": "2026-10-13", "ward": "ICU-3 / Bed 2",
            "diagnosis": "Pneumonia", "payer_type": "CASH",
        })
        self.assertEqual(resp.status_code, 302)
        admission = KickbackEntry.objects.get(patient_name="Gopal")
        self.assertEqual(admission.patient_phone, "9876543210")
        self.assertEqual(admission.patient_age, 54)
        self.assertEqual(admission.patient_gender, "M")
        self.assertEqual(admission.ward, "ICU-3 / Bed 2")
        self.assertEqual(admission.diagnosis, "Pneumonia")
        resp = self.client_.get(f"/app/patient-bills/{admission.pk}/")
        self.assertContains(resp, "ICU-3 / Bed 2")
