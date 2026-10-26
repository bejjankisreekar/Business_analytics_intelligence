from decimal import Decimal
from unittest import mock

from django.test import SimpleTestCase

from apps.finance import imports
from apps.finance.forms import insurer_choices
from apps.finance.models import KickbackEntry


class PayerTypeParsingTests(SimpleTestCase):
    def test_common_payer_wordings(self):
        cases = {
            "Insurance": KickbackEntry.PayerType.INSURANCE,
            "Mediclaim insurance": KickbackEntry.PayerType.INSURANCE,
            "TPA - corporate": KickbackEntry.PayerType.INSURANCE,
            "Govt scheme": KickbackEntry.PayerType.INSURANCE,
            "Cash": KickbackEntry.PayerType.CASH,
            "Self": KickbackEntry.PayerType.CASH,
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(imports._parse_payer_type(text), expected)

    def test_unknown_or_blank_payer_is_left_blank(self):
        self.assertEqual(imports._parse_payer_type(""), "")
        self.assertEqual(imports._parse_payer_type("Maybe later"), "")


class InsuranceImportTests(SimpleTestCase):
    HEADERS = [
        "Invoice Number", "Admission Date", "Patient Name", "MRN", "Payer", "Insurer / TPA", "Policy No",
        "Pre-auth No", "Insurer Pays", "Patient Pays", "Final Amount",
    ]

    def test_insurance_columns_are_mapped_and_read(self):
        mapping = imports.map_kickback_columns(self.HEADERS)
        self.assertEqual(mapping["payer_type"], "Payer")
        self.assertEqual(mapping["insurer_name"], "Insurer / TPA")
        self.assertEqual(mapping["policy_number"], "Policy No")
        self.assertEqual(mapping["preauth_number"], "Pre-auth No")
        self.assertEqual(mapping["insurer_amount"], "Insurer Pays")
        self.assertEqual(mapping["patient_amount"], "Patient Pays")
        # "Patient Name" must still win over the patient-share columns.
        self.assertEqual(mapping["patient_name"], "Patient Name")

    def test_rows_carry_insurance_details(self):
        mapping = imports.map_kickback_columns(self.HEADERS)
        data_rows = [["BL-9", "2026-10-01", "Ramesh", "MRN-9", "Insurance", "Star Health", "P-123",
                      "PA-7", "45000", "15000", "60000"]]
        rows, errors = imports.build_kickback_rows(self.HEADERS, data_rows, mapping)
        self.assertEqual(errors, [])
        row = rows[0]
        self.assertEqual(row["payer_type"], KickbackEntry.PayerType.INSURANCE)
        self.assertEqual(row["insurer_name"], "Star Health")
        self.assertEqual(row["policy_number"], "P-123")
        self.assertEqual(row["preauth_number"], "PA-7")
        self.assertEqual(row["insurer_amount"], Decimal("45000"))
        self.assertEqual(row["patient_amount"], Decimal("15000"))

    def test_old_file_without_insurance_columns_still_imports(self):
        headers = ["Invoice Number", "Admission Date", "Patient Name", "MRN", "Final Amount"]
        mapping = imports.map_kickback_columns(headers)
        rows, errors = imports.build_kickback_rows(
            headers, [["BL-1", "2026-10-01", "Ramesh", "MRN-1", "100"]], mapping
        )
        self.assertEqual(errors, [])
        self.assertEqual(rows[0]["payer_type"], "")
        self.assertEqual(rows[0]["insurer_amount"], Decimal("0"))


class InsurerDropdownTests(SimpleTestCase):
    def test_defaults_are_offered(self):
        with mock.patch("apps.finance.forms._used_insurer_names", return_value=[]):
            names = insurer_choices()
        self.assertIn("Star Health", names)
        self.assertIn("Aarogyasri", names)

    def test_used_and_current_names_are_added_without_duplicates(self):
        with mock.patch("apps.finance.forms._used_insurer_names", return_value=["Acme Mutual", "star health"]):
            names = insurer_choices("Zeta Assurance", "  ")
        self.assertIn("Acme Mutual", names)
        self.assertIn("Zeta Assurance", names)
        # "star health" (any case) is already a default, so it appears once.
        self.assertEqual([n for n in names if n.lower() == "star health"], ["Star Health"])
        self.assertEqual(names, sorted(names, key=str.lower))


class MrnImportTests(SimpleTestCase):
    def test_mrn_column_is_mapped_and_read(self):
        headers = ["Bill Number", "Admission Date", "Patient Name", "UMRN", "Final Amount"]
        mapping = imports.map_kickback_columns(headers)
        self.assertEqual(mapping["mrn"], "UMRN")
        self.assertEqual(mapping["patient_name"], "Patient Name")
        rows, errors = imports.build_kickback_rows(
            headers, [["BL-5", "2026-10-02", "Sita", " MRN-77 ", "1000"]], mapping
        )
        self.assertEqual(errors, [])
        self.assertEqual(rows[0]["mrn"], "MRN-77")

    def test_file_without_an_mrn_column_is_rejected(self):
        headers = ["Invoice Number", "Admission Date", "Patient Name"]
        mapping = imports.map_kickback_columns(headers)
        self.assertIsNone(mapping["mrn"])
        self.assertIn("mrn", [f for f in imports.KICKBACK_REQUIRED_FIELDS if not mapping.get(f)])


class ImportDateFormatTests(SimpleTestCase):
    def test_day_month_year_dates_are_read_as_day_first(self):
        import datetime

        self.assertEqual(imports._parse_date_cell("05-10-2026"), datetime.date(2026, 10, 5))
        self.assertEqual(imports._parse_date_cell("31-12-2026"), datetime.date(2026, 12, 31))

    def test_sample_file_uses_day_month_year_and_imports_cleanly(self):
        header, *data = imports.KICKBACK_SAMPLE_ROWS
        mapping = imports.map_kickback_columns(header)
        rows, errors = imports.build_kickback_rows(header, data, mapping)
        self.assertEqual(errors, [])
        self.assertEqual(rows[0]["admission_date"].isoformat(), "2026-10-01")
        self.assertEqual(rows[0]["discharge_date"].isoformat(), "2026-10-05")
