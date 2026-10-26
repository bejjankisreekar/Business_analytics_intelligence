import datetime
from decimal import Decimal
from unittest import mock

from django.test import Client, TestCase
from django.urls import reverse

from apps.accounts.models import User
from apps.finance.models import ClaimPayment, InsuranceClaim, KickbackEntry, SalesEntry
from apps.organizations.models import Organization
from apps.organizations.tenant_context import set_current_organization


class _ClaimsTenantTestCase(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(
            name="Access Hospital", slug="access-hospital", organization_code="ACCHSP",
            storage_mode=Organization.StorageMode.OUR_DATABASE,
            business_type=Organization.BusinessType.HEALTHCARE,
        )
        set_current_organization(self.org)
        self.addCleanup(set_current_organization, None)
        self.owner = User.objects.create_user(
            email="owner@access.example", password="testpass-123", organization=self.org, role=User.Role.OWNER,
        )
        for target in (
            "apps.finance.claim_views.has_kickbacks_access",
            "apps.finance.views.has_kickbacks_access",
            "apps.finance.access.has_kickbacks_access",
            "config.context_processors.has_kickbacks_access",
        ):
            mock.patch(target, return_value=True).start()
        mock.patch("apps.billing.services.has_active_access", return_value=True).start()
        mock.patch("apps.billing.services.is_account_suspended", return_value=False).start()
        self.addCleanup(mock.patch.stopall)
        self.bill = KickbackEntry.objects.create(
            bill_number="IP-1", patient_name="Ramesh", admission_date=datetime.date(2026, 10, 1),
            final_amount=Decimal("60000"), payer_type=KickbackEntry.PayerType.INSURANCE,
            insurer_name="Star Health", insurer_amount=Decimal("45000"),
        )


class RevenueFromClaimPaymentTests(_ClaimsTenantTestCase):
    def setUp(self):
        super().setUp()
        self.client_ = Client()
        self.client_.force_login(self.owner)
        self.claim = InsuranceClaim.objects.create(
            bill=self.bill, claim_number="CLM-9", insurer_name="Star Health",
            claimed_amount=Decimal("45000"), approved_amount=Decimal("45000"),
            status=InsuranceClaim.Status.APPROVED,
        )

    def test_payment_is_booked_as_revenue_on_the_bill(self):
        self.client_.post(f"/app/insurance-claims/{self.claim.pk}/payments/add/", {
            "date": "2026-10-09", "amount": "30000", "deduction": "1000", "reference": "UTR-1",
        })
        entry = SalesEntry.objects.get(kickback_bill=self.bill)
        self.assertEqual(entry.amount, Decimal("30000"))  # the deduction is not revenue
        self.assertEqual(entry.date, datetime.date(2026, 10, 9))
        self.assertIn("CLM-9", entry.note)
        self.bill.refresh_from_db()
        self.assertEqual(self.bill.amount_received, Decimal("30000"))
        payment = ClaimPayment.objects.get(claim=self.claim)
        self.assertEqual(payment.sales_entry_id, entry.pk)

    def test_removing_a_payment_removes_its_revenue(self):
        self.client_.post(f"/app/insurance-claims/{self.claim.pk}/payments/add/", {
            "date": "2026-10-09", "amount": "30000",
        })
        payment = ClaimPayment.objects.get(claim=self.claim)
        self.client_.post(f"/app/insurance-claims/payments/{payment.pk}/delete/")
        self.assertFalse(SalesEntry.objects.filter(kickback_bill=self.bill).exists())
        self.bill.refresh_from_db()
        self.assertEqual(self.bill.amount_received, Decimal("0"))

    def test_deleting_a_claim_removes_its_revenue(self):
        self.client_.post(f"/app/insurance-claims/{self.claim.pk}/payments/add/", {
            "date": "2026-10-09", "amount": "30000",
        })
        self.client_.post(f"/app/insurance-claims/{self.claim.pk}/delete/")
        self.assertFalse(SalesEntry.objects.filter(kickback_bill=self.bill).exists())


class OpdIpRevenueTests(_ClaimsTenantTestCase):
    def setUp(self):
        super().setUp()
        from apps.finance.models import Category

        self.channel = Category.objects.create(name="IP Services", kind=Category.Kind.SALES)
        # Only the IP billing revenue category can take an admission.
        from apps.finance import services

        settings = services.get_finance_settings()
        settings.ip_revenue_category = self.channel
        settings.save()
        self.client_ = Client()
        self.client_.force_login(self.owner)

    def _data(self, **overrides):
        data = {
            "date": "2026-10-12", "channel": str(self.channel.pk), "gross_amount": "5000",
            "payment_mode": "CASH", "kickback_bill": "",
        }
        data.update(overrides)
        return data

    def test_ip_receipt_is_linked_to_the_admission(self):
        from apps.finance.forms import SalesEntryForm

        form = SalesEntryForm(data=self._data(kickback_bill=str(self.bill.pk)))
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        entry = SalesEntry.objects.get(kickback_bill=self.bill)
        self.assertEqual(entry.amount, Decimal("5000"))

    def test_admission_is_dropped_for_a_category_that_is_not_ip(self):
        from apps.finance.forms import SalesEntryForm
        from apps.finance.models import Category

        other = Category.objects.create(name="OPD Consultation", kind=Category.Kind.SALES)
        form = SalesEntryForm(data=self._data(channel=str(other.pk), kickback_bill=str(self.bill.pk)))
        self.assertTrue(form.is_valid(), form.errors)
        entry = form.save()
        self.assertIsNone(entry.kickback_bill_id)

    def test_receipt_with_no_admission_is_opd(self):
        from apps.finance.forms import SalesEntryForm

        form = SalesEntryForm(data=self._data(kickback_bill=""))
        self.assertTrue(form.is_valid(), form.errors)
        entry = form.save()
        self.assertIsNone(entry.kickback_bill_id)

    def test_admission_page_shows_revenue_collected(self):
        from apps.finance.forms import SalesEntryForm

        SalesEntryForm(data=self._data(kickback_bill=str(self.bill.pk))).save()
        resp = self.client_.get(f"/app/patient-bills/{self.bill.pk}/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Revenue collected")
        self.assertEqual(resp.context["revenue_total"], Decimal("5000"))

    def test_admission_picker_lists_most_recent_admission_first(self):
        from apps.finance.forms import SalesEntryForm

        older = KickbackEntry.objects.create(
            bill_number="IP-OLD", admission_date=datetime.date(2026, 1, 1), final_amount=Decimal("1"),
        )
        form = SalesEntryForm(data=self._data(kickback_bill=str(self.bill.pk)))
        ordered = list(form.fields["kickback_bill"].queryset)
        self.assertLess(ordered.index(self.bill), ordered.index(older))


class IpBillingRevenueCategoryTests(_ClaimsTenantTestCase):
    def setUp(self):
        super().setUp()
        from apps.finance import services
        from apps.finance.models import Category

        self.ip_category = Category.objects.create(name="IP Billing", kind=Category.Kind.SALES)
        settings = services.get_finance_settings()
        settings.ip_revenue_category = self.ip_category
        settings.save()
        self.client_ = Client()
        self.client_.force_login(self.owner)

    def test_ip_receipt_without_a_category_takes_the_ip_category(self):
        from apps.finance.forms import SalesEntryForm

        form = SalesEntryForm(data={
            "date": "2026-10-12", "gross_amount": "5000", "payment_mode": "CASH",
            "kickback_bill": str(self.bill.pk),
        })
        self.assertTrue(form.is_valid(), form.errors)
        entry = form.save()
        self.assertEqual(entry.channel_id, self.ip_category.pk)

    def test_insurance_payment_is_booked_under_the_ip_category(self):
        claim = InsuranceClaim.objects.create(
            bill=self.bill, claim_number="CLM-IP", insurer_name="Star Health",
            claimed_amount=Decimal("1000"), approved_amount=Decimal("1000"),
            status=InsuranceClaim.Status.APPROVED,
        )
        self.client_.post(f"/app/insurance-claims/{claim.pk}/payments/add/", {
            "date": "2026-10-09", "amount": "400",
        })
        entry = SalesEntry.objects.get(kickback_bill=self.bill)
        self.assertEqual(entry.channel_id, self.ip_category.pk)

    def test_setup_page_shows_the_ip_billing_revenue_card(self):
        resp = self.client_.get(reverse("finance:settings"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "IP Billing Revenue")
        self.assertContains(resp, "Booked under “IP Billing”")

    def test_setup_page_saves_the_ip_billing_revenue_category(self):
        from apps.finance.models import Category

        other = Category.objects.create(name="OPD Consultation", kind=Category.Kind.SALES)
        self.client_.post(reverse("finance:settings"), {
            "save_ip_revenue_category": "1", "ip_revenue_category": str(other.pk),
        })
        from apps.finance import services

        self.assertEqual(services.get_finance_settings().ip_revenue_category_id, other.pk)


class BulkEntryCustomerColumnTests(_ClaimsTenantTestCase):
    def test_customer_column_is_gone_but_the_saved_customer_is_kept(self):
        from apps.finance.models import Category, Customer, SalesEntry

        channel = Category.objects.create(name="Walk-in", kind=Category.Kind.SALES)
        customer = Customer.objects.create(name="Asha Walk-in")
        entry = SalesEntry.objects.create(
            date=datetime.date(2026, 10, 13), channel=channel, customer=customer,
            gross_amount=Decimal("300"), amount=Decimal("300"),
        )
        set_current_organization(self.org)  # a request clears the tenant context
        client = Client()
        client.force_login(self.owner)
        resp = client.get("/app/daily/bulk-entry/?date=2026-10-13")
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, 'data-col="Customer"')
        self.assertContains(resp, f'name="sale-0-customer"')
        self.assertContains(resp, f'value="{customer.pk}"')
        self.assertTrue(SalesEntry.objects.filter(pk=entry.pk, customer=customer).exists())


class AdmissionLabelAndSingleEntryTests(_ClaimsTenantTestCase):
    def setUp(self):
        super().setUp()
        from apps.finance import services
        from apps.finance.models import Category

        self.ip_category = Category.objects.create(name="IPD / Inpatient Services", kind=Category.Kind.SALES)
        settings = services.get_finance_settings()
        settings.ip_revenue_category = self.ip_category
        settings.save()
        self.bill.mrn = "MRN-42"
        self.bill.save()
        self.client_ = Client()
        self.client_.force_login(self.owner)

    def test_admission_is_labelled_mrn_name_and_admission_date(self):
        from apps.finance.forms import SalesEntryForm

        labels = [label for _, label in SalesEntryForm().fields["kickback_bill"].choices]
        self.assertIn("MRN-42 - Ramesh - 01 Oct 2026", labels)

    def test_single_entry_page_has_the_admission_field_wired_to_the_ip_category(self):
        from apps.finance.models import Category

        set_current_organization(self.org)
        resp = self.client_.get("/app/daily/single-entry/?type=sale&date=2026-10-13")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'id="se-admission"')
        self.assertNotContains(resp, 'id="id_customer"')
        self.assertEqual(resp.context["ip_revenue_category_id"], str(self.ip_category.pk))


class AdmissionFinalAmountTests(_ClaimsTenantTestCase):
    def test_bill_form_has_an_editable_final_amount(self):
        from apps.finance.forms import KickbackEntryForm

        self.assertIn("final_amount", KickbackEntryForm().fields)

    def test_daily_book_revenue_sets_amount_received_and_leaves_final_amount_alone(self):
        from apps.finance.forms import SalesEntryForm
        from apps.finance.models import Category

        from apps.finance import services

        channel = Category.objects.create(name="IP Billing", kind=Category.Kind.SALES)
        settings = services.get_finance_settings()
        settings.ip_revenue_category = channel
        settings.save()
        for amount in ("3000", "1500"):
            form = SalesEntryForm(data={
                "date": "2026-10-12", "channel": str(channel.pk), "gross_amount": amount,
                "payment_mode": "CASH", "kickback_bill": str(self.bill.pk),
            })
            self.assertTrue(form.is_valid(), form.errors)
            form.save()
        self.bill.refresh_from_db()
        self.assertEqual(self.bill.amount_received, Decimal("4500"))
        self.assertEqual(self.bill.final_amount, Decimal("60000"))


class InsurerShareTests(_ClaimsTenantTestCase):
    def test_insurer_share_is_bill_amount_less_patient_share_and_discount(self):
        from apps.finance.models import KickbackEntry

        bill = KickbackEntry.objects.create(
            bill_number="IP-S", admission_date=datetime.date(2026, 10, 1), final_amount=Decimal("20000"),
            payer_type=KickbackEntry.PayerType.INSURANCE, patient_amount=Decimal("5000"),
            discount_amount=Decimal("1000"), insurer_amount=Decimal("70000"),
        )
        self.assertEqual(bill.insurer_share, Decimal("14000"))
        bill.patient_amount = Decimal("20000")
        bill.discount_amount = Decimal("0")
        self.assertEqual(bill.insurer_share, Decimal("0"))


class PatientPaymentTests(_ClaimsTenantTestCase):
    def test_patient_payment_is_revenue_on_the_admission_not_a_claim_payment(self):
        from apps.finance.models import ClaimPayment, InsuranceClaim, SalesEntry

        client = Client()
        client.force_login(self.owner)
        set_current_organization(self.org)
        claim = InsuranceClaim.objects.create(
            bill=self.bill, claim_number="CLM-PP", insurer_name="Star Health",
            claimed_amount=Decimal("1000"), approved_amount=Decimal("1000"),
            status=InsuranceClaim.Status.APPROVED,
        )
        resp = client.post(f"/app/insurance-claims/{claim.pk}/patient-payment/", {
            "date": "2026-10-12", "amount": "2500", "payment_mode": "BANK", "note": "UPI",
        })
        self.assertEqual(resp.status_code, 302)
        entry = SalesEntry.objects.get(kickback_bill=self.bill)
        self.assertEqual(entry.amount, Decimal("2500"))
        self.assertEqual(entry.payment_mode, "BANK")
        self.assertIn("Patient payment", entry.note)
        claim.refresh_from_db()
        self.assertEqual(claim.received_amount, Decimal("0"))
        self.assertFalse(ClaimPayment.objects.filter(claim=claim).exists())


class DailyBookChangesReachClaimTests(_ClaimsTenantTestCase):
    def test_editing_the_daily_book_entry_updates_the_claim_payment(self):
        from apps.finance.models import ClaimPayment, InsuranceClaim, SalesEntry

        client = Client()
        client.force_login(self.owner)
        set_current_organization(self.org)
        claim = InsuranceClaim.objects.create(
            bill=self.bill, claim_number="CLM-DB", insurer_name="Star Health",
            claimed_amount=Decimal("60000"), approved_amount=Decimal("45000"),
            status=InsuranceClaim.Status.APPROVED,
        )
        client.post(f"/app/insurance-claims/{claim.pk}/payments/add/", {
            "date": "2026-10-09", "amount": "30000",
        })
        entry = SalesEntry.objects.get(kickback_bill=self.bill)
        entry.amount = Decimal("25000")
        entry.gross_amount = Decimal("25000")
        entry.save()
        claim.refresh_from_db()
        self.assertEqual(claim.received_amount, Decimal("25000"))
        self.assertEqual(ClaimPayment.objects.get(claim=claim).amount, Decimal("25000"))

    def test_deleting_the_daily_book_entry_refreshes_the_claim_totals(self):
        from apps.finance.models import InsuranceClaim, SalesEntry

        client = Client()
        client.force_login(self.owner)
        set_current_organization(self.org)
        claim = InsuranceClaim.objects.create(
            bill=self.bill, claim_number="CLM-DEL", insurer_name="Star Health",
            claimed_amount=Decimal("60000"), approved_amount=Decimal("45000"),
            status=InsuranceClaim.Status.APPROVED,
        )
        client.post(f"/app/insurance-claims/{claim.pk}/payments/add/", {
            "date": "2026-10-09", "amount": "30000",
        })
        SalesEntry.objects.get(kickback_bill=self.bill).delete()
        claim.refresh_from_db()
        self.assertEqual(claim.received_amount, Decimal("30000"))
