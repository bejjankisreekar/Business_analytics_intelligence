"""Who gets the Customer Requests plug-in.

Optional add-on for shop and restaurant organizations only — hospitals and
every other business type never see it. Every view and the menu ask this one
question so they can never disagree.
"""

from apps.accounts.manager_pages import CUSTOMER_REQUESTS_PAGE, org_allows
from apps.accounts.models import User
from apps.organizations.models import Organization

ELIGIBLE_BUSINESS_TYPES = (
    Organization.BusinessType.RETAIL_ECOMMERCE,
    Organization.BusinessType.RESTAURANTS_FOOD,
)

# Same people who run the business day to day; plain members never see it.
ALLOWED_ROLES = (User.Role.OWNER, User.Role.ADMIN, User.Role.MANAGER)


def org_is_eligible(org) -> bool:
    return bool(org) and org.business_type in ELIGIBLE_BUSINESS_TYPES


def user_has_access(user) -> bool:
    return (
        user is not None
        and user.is_authenticated
        and bool(getattr(user, "organization_id", None))
        and user.role in ALLOWED_ROLES
        and org_is_eligible(user.organization)
        and org_allows(user.organization, CUSTOMER_REQUESTS_PAGE)
    )
