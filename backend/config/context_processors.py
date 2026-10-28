from django.conf import settings

from apps.finance.access import has_kickbacks_access, patient_terms


def site_meta(request):
    return {
        "SITE_NAME": settings.SITE_NAME,
        "SITE_URL": settings.SITE_URL,
        "CONTACT_EMAIL": settings.CONTACT_EMAIL,
        "CONTACT_PHONE": settings.CONTACT_PHONE,
    }


def kickbacks_access(request):
    """`kickbacks_enabled` for every template, so the menu and the Settings card
    show on any page, not only pages whose view ran the Kickbacks check."""
    return {"kickbacks_enabled": has_kickbacks_access(request)}


def patient_terms_context(request):
    """`pt` for every template: the hospital or diagnostic-centre wording."""
    user = getattr(request, "user", None)
    org = user.organization if user is not None and user.is_authenticated and user.organization_id else None
    return {"pt": patient_terms(org)}


def page_access(request):
    """`page_access` for every template: `{% if "daily_summary" in page_access %}`.
    Owners/admins see every page; managers see only the pages ticked for them."""
    from apps.accounts.manager_pages import PageAccess

    return {"page_access": PageAccess(request.user)}
