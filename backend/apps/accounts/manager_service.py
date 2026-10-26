from apps.billing.models import Plan, Subscription
from apps.organizations.models import Organization


class ManagerAccountError(Exception):
    """Raised when a manager account operation violates business rules."""
    pass


def can_organization_have_manager_accounts(organization: Organization) -> bool:
    """Check if an organization's current plan allows manager accounts.

    Only Business and Business Drive plans allow manager account creation.
    """
    current_subscription = organization.subscriptions.filter(is_current=True).first()
    if not current_subscription:
        return False

    plan_slug = current_subscription.plan.slug
    # Accept paid/premium plans but exclude free and starter plans
    # Plans like: smart-drive, enterprise, business-drive, pro, etc. are allowed
    plan_lower = plan_slug.lower()
    is_free = plan_lower in ("free", "complementary")
    is_starter = plan_lower in ("starter", "basic")
    has_premium = "business" in plan_lower or "enterprise" in plan_lower or "drive" in plan_lower or "pro" in plan_lower
    return has_premium and not is_free and not is_starter


def get_max_manager_accounts(organization: Organization) -> int:
    """Get the maximum number of manager accounts allowed for this organization.

    Driven by the organization's current plan.
    """
    if not can_organization_have_manager_accounts(organization):
        return 0

    # An eligible plan grants one manager slot. This deliberately does not read
    # Organization.manager_logins: that is just the choice made at signup (default 1),
    # so an org upgraded to Business later would otherwise never get a slot.
    return 1


def validate_manager_account_creation(organization: Organization) -> None:
    """Validate that an organization can create a manager account.

    Raises ManagerAccountError if:
    - Organization doesn't have an eligible plan
    - Organization has reached its manager account limit
    """
    if not can_organization_have_manager_accounts(organization):
        raise ManagerAccountError(
            "Manager accounts are only available on Business and Business Drive plans. "
            "Please upgrade your plan to create manager accounts."
        )

    # Check if organization has reached manager account limit
    max_allowed = get_max_manager_accounts(organization)
    if max_allowed == 0:
        raise ManagerAccountError(
            "Your organization doesn't have manager account slots available. "
            "Please check your subscription or contact support."
        )

    # Count existing manager accounts
    from .models import User
    existing_managers = User.objects.filter(
        organization=organization,
        role=User.Role.MANAGER
    ).count()

    if existing_managers >= max_allowed:
        raise ManagerAccountError(
            f"You've reached the maximum number of manager accounts ({max_allowed}). "
            "Please remove an existing manager account or contact support."
        )
