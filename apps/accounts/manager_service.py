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
    return plan_slug in ("business", "business-drive")


def get_max_manager_accounts(organization: Organization) -> int:
    """Get the maximum number of manager accounts allowed for this organization.

    Checks both the plan eligibility and the organization's manager_logins setting.
    """
    if not can_organization_have_manager_accounts(organization):
        return 0

    # manager_logins value indicates how many accounts are allowed
    # 1 = just admin, 2 = admin + 1 manager
    if organization.manager_logins >= 2:
        return 1
    return 0


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
