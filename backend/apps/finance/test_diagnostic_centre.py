import datetime
from decimal import Decimal
from unittest import mock

from django.test import Client, TestCase
from django.urls import reverse

from apps.accounts.models import User
from apps.finance.forms import KickbackEntryForm
from apps.finance.models import KickbackEntry
from apps.organizations.models import Organization
from apps.organizations.tenant_context import set_current_organization


class DiagnosticCentreTests(TestCase):
    """A diagnostic centre uses the patient-bill pages with visit wording: no
    admission, discharge or ward."""

    business_type = Organization.BusinessType.DIAGNOSTIC

    def setUp(self):
        self.org = Organization.objects.create(
            name="Test Diagnostics", slug="test-diagnostics", organization_code="TSTDGN",
            storage_mode=Organization.StorageMode.OUR_DATABASE, business_type=self.business_type,
        )
        set_current_organization(self.org)
        self.addCleanup(set_current_organization, None)
        self.owner = User.objects.create_user(
            email="owner@diag.example", password="testpass-123", organization=self.org, role=User.Role.OWNER,
        )
        for target in (
            "apps.finance.views.has_kickbacks_access",
            "apps.finance.claim_views.has_kickbacks_access",
            "config.context_processors.has_kickbacks_access",
        ):
            mock.patch(target, return_value=True).start()
        mock.patch("apps.billing.services.has_active_access", return_value=True).start()
        mock.patch("apps.billing.services.is_account_suspended", return_value=False).start()
        self.addCleanup(mock.patch.stopall)
        self.bill = KickbackEntry.objects.create(
            bill_number="DC-1", patient_name="Anjali Devi", mrn="UHID1", admission_date=datetime.date(2026, 10, 5),
            final_amount=Decimal("1500"), diagnosis="CBC, Lipid profile", ward="General / 4",
        )
        self.client_ = Client()
        self.client_.force_login(self.owner)

    def test_list_page_uses_visit_wording(self):
        html = self.client_.get(reverse("finance:kickbacks")).content.decode()
        self.assertIn("Patient Bills", html)
        self.assertIn("Visit Date", html)
        self.assertNotIn("IP Admissions", html)
        self.assertNotIn("Discharge", html)
        self.assertNotIn("Ward / bed", html)

    def test_add_form_has_no_discharge_or_ward(self):
        form = KickbackEntryForm()
        self.assertNotIn("discharge_date", form.fields)
        self.assertNotIn("ward", form.fields)
        self.assertEqual(form.fields["admission_date"].label, "Visit date")
        self.assertEqual(form.fields["diagnosis"].label, "Tests / notes")

    def test_a_visit_can_be_added_without_discharge_or_ward(self):
        response = self.client_.post(reverse("finance:add_kickback_entry"), {
            "patient_name": "Ravi Kumar", "mrn": "UHID2", "admission_date": "2026-10-06",
            "payer_type": "CASH", "final_amount": "900", "diagnosis": "HbA1c",
        })
        self.assertEqual(response.status_code, 302)
        visit = KickbackEntry.objects.get(patient_name="Ravi Kumar")
        self.assertEqual(visit.admission_date, datetime.date(2026, 10, 6))
        self.assertIsNone(visit.discharge_date)

    def test_detail_and_edit_pages_use_visit_wording(self):
        detail = self.client_.get(reverse("finance:kickback_detail", args=[self.bill.pk])).content.decode()
        self.assertIn("Visit date", detail)
        self.assertIn("Tests / notes", detail)
        self.assertNotIn("Discharged on", detail)
        self.assertNotIn("Ward / bed", detail)
        edit = self.client_.get(reverse("finance:edit_kickback_entry", args=[self.bill.pk])).content.decode()
        self.assertIn("Visit date", edit)
        self.assertNotIn("Discharge date", edit)

    def test_patient_bill_picker_label_on_entry_form(self):
        html = self.client_.get(reverse("finance:single_entry")).content.decode()
        self.assertNotIn(">Admission (IP)<", html)
        self.assertIn(">Patient bill<", html)


class HospitalKeepsAdmissionWordingTests(DiagnosticCentreTests):
    business_type = Organization.BusinessType.HEALTHCARE

    def test_list_page_uses_visit_wording(self):
        html = self.client_.get(reverse("finance:kickbacks")).content.decode()
        self.assertIn("IP Admissions", html)
        self.assertIn("Admitted On", html)

    def test_add_form_has_no_discharge_or_ward(self):
        form = KickbackEntryForm()
        self.assertIn("discharge_date", form.fields)
        self.assertIn("ward", form.fields)
        self.assertEqual(form.fields["admission_date"].label, "Admission date")

    def test_a_visit_can_be_added_without_discharge_or_ward(self):
        pass

    def test_detail_and_edit_pages_use_visit_wording(self):
        detail = self.client_.get(reverse("finance:kickback_detail", args=[self.bill.pk])).content.decode()
        self.assertIn("Discharged on", detail)
        self.assertIn("Ward / bed", detail)

    def test_patient_bill_picker_label_on_entry_form(self):
        html = self.client_.get(reverse("finance:single_entry")).content.decode()
        self.assertIn(">Admission (IP)<", html)
