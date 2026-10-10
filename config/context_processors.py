from django.conf import settings

from apps.finance.access import has_kickbacks_access


def site_meta(request):
    return {
        "SITE_NAME": settings.SITE_NAME,
        "CONTACT_EMAIL": settings.CONTACT_EMAIL,
        "CONTACT_PHONE": settings.CONTACT_PHONE,
    }


def kickbacks_access(request):
    """`kickbacks_enabled` for every template, so the menu and the Settings card
    show on any page, not only pages whose view ran the Kickbacks check."""
    return {"kickbacks_enabled": has_kickbacks_access(request)}
