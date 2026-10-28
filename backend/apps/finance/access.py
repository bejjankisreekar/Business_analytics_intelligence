"""Who gets the Kickbacks feature (and its Settings card).

Kickbacks is for Healthcare & Medical and Diagnostic Centre organizations on
an Advance or Business Drive plan. Every page that shows the menu, and every
Kickbacks view, asks this one question, so they can never disagree.

A diagnostic centre uses the same records as a hospital, but has no admission,
discharge or ward, so `patient_terms` gives its pages visit wording instead.
"""

from apps.billing import services as billing_services
from apps.organizations.models import Organization

PATIENT_BILL_TYPES = (Organization.BusinessType.HEALTHCARE, Organization.BusinessType.DIAGNOSTIC)

HOSPITAL_TERMS = {
    "is_diagnostic": False,
    "page": "IP Admissions",
    "page_subtitle": "Inpatient admissions, bills & referral commission — one row per admission",
    "record": "admission",
    "add_title": "Add a patient admission",
    "add_help": "Record an IP admission. Its revenue and referral commission come from the Daily Book.",
    "date_label": "Admission date",
    "picker": "Admission (IP)",
    "date_column": "Admitted On",
}
DIAGNOSTIC_TERMS = {
    "is_diagnostic": True,
    "page": "Patient Bills",
    "page_subtitle": "Patient visits, test bills & referral commission — one row per visit",
    "record": "visit",
    "add_title": "Add a patient visit",
    "add_help": "Record a patient visit. Its revenue and referral commission come from the Daily Book.",
    "date_label": "Visit date",
    "picker": "Patient bill",
    "date_column": "Visit Date",
}


def patient_terms(org) -> dict:
    """The wording for the Kickbacks pages: hospital or diagnostic centre."""
    if org is not None and org.business_type == Organization.BusinessType.DIAGNOSTIC:
        return DIAGNOSTIC_TERMS
    return HOSPITAL_TERMS


def has_kickbacks_access(request) -> bool:
    """Cached on the request, so a page's menu and its checks agree and the
    plan is looked up at most once per request."""
    cached = getattr(request, "_kickbacks_access", None)
    if cached is not None:
        return cached
    user = getattr(request, "user", None)
    access = False
    if user is not None and user.is_authenticated and user.organization_id:
        org = user.organization
        access = org.business_type in PATIENT_BILL_TYPES and billing_services.has_business_tier_plan(org)
    request._kickbacks_access = access
    return access
