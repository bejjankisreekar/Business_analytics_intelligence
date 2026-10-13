from django.test import SimpleTestCase

from apps.finance import imports


class KickbackImportRequiredFieldsTests(SimpleTestCase):
    """Bill number is optional; patient name, MRN and admission date are
    required on every imported patient bill."""

    HEADERS = ["Bill Number", "Patient Name", "MRN", "Admission Date"]

    def _build(self, rows):
        mapping = imports.map_kickback_columns(self.HEADERS)
        return imports.build_kickback_rows(self.HEADERS, rows, mapping)

    def test_bill_number_is_optional(self):
        rows, errors = self._build([["", "Ramesh Kumar", "MRN-1", "01-10-2026"]])
        self.assertEqual(errors, [])
        self.assertEqual(rows[0]["bill_number"], "")

    def test_patient_name_mrn_and_admission_date_are_required(self):
        rows, errors = self._build([
            ["BL-1", "", "MRN-2", "01-10-2026"],
            ["BL-3", "Sita Devi", "", "01-10-2026"],
            ["BL-4", "Asha", "MRN-4", ""],
        ])
        self.assertEqual(rows, [])
        self.assertEqual(errors, [
            (2, "patient name is missing"),
            (3, "MRN is missing"),
            (4, "admission date is missing or not recognized"),
        ])

    def test_required_columns_must_be_present_in_the_file(self):
        self.assertEqual(
            [f for f in imports.KICKBACK_REQUIRED_FIELDS
             if not imports.map_kickback_columns(["Bill Number", "Admission Date"]).get(f)],
            ["patient_name", "mrn"],
        )
