from types import SimpleNamespace

from django.test import SimpleTestCase

from apps.finance.services import _display_note, _patient_label


def _bill(**fields):
    base = dict(bill_number="BL-3008", patient_name="", mrn="")
    base.update(fields)
    return SimpleNamespace(**base)


class AdmissionLabelTests(SimpleTestCase):
    def test_patient_name_comes_first(self):
        self.assertEqual(_patient_label(_bill(patient_name="Anjali Devi", mrn="MRN-1")), "Anjali Devi")

    def test_mrn_is_used_when_name_is_empty(self):
        self.assertEqual(_patient_label(_bill(mrn="MRN-1")), "MRN-1")

    def test_bill_number_is_the_last_fallback(self):
        self.assertEqual(_patient_label(_bill()), "BL-3008")

    def test_bill_number_in_a_note_is_swapped_for_the_patient(self):
        entry = SimpleNamespace(note="Referral commission — BL-3008")
        bill = _bill(patient_name="Anjali Devi")
        self.assertEqual(_display_note(entry, bill), "Referral commission — Anjali Devi")

    def test_note_without_the_bill_number_is_unchanged(self):
        entry = SimpleNamespace(note="Referral commission")
        self.assertEqual(_display_note(entry, _bill(patient_name="Anjali Devi")), "Referral commission")

    def test_no_bill_leaves_the_note_alone(self):
        self.assertEqual(_display_note(SimpleNamespace(note="Rent"), None), "Rent")
