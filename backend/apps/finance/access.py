"""Who gets the Kickbacks feature (and its Settings card).

Kickbacks is for Healthcare & Medical organizations on a Business or
Business Drive plan. Every page that shows the menu, and every Kickbacks
view, asks this one question, so they can never disagree.
"""

from apps.billing import services as billing_services
from apps.organizations.models import Organization


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
        access = (
            org.business_type == Organization.BusinessType.HEALTHCARE
            and billing_services.has_business_tier_plan(org)
        )
    request._kickbacks_access = access
    return access
