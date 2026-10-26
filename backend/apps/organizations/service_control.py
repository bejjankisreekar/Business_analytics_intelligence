"""Client service lifecycle: start/stop/suspend/resume, each producing a
permanent ServiceStatusChange audit entry. This is deliberately separate
from account status (`Organization.is_active`) and from billing/subscription
state (`apps.billing`) — suspending service never touches either.
"""
from django.db import transaction

from .models import Organization, ServiceStatusChange


def _transition(
    org: Organization, *, using: str, action: str, new_status: str, admin_email: str, reason: str = "", notes: str = ""
) -> ServiceStatusChange:
    with transaction.atomic(using=using):
        previous_status = org.service_status
        org.service_status = new_status
        org.save(using=using, update_fields=["service_status", "updated_at"])
        return ServiceStatusChange.objects.using(using).create(
            organization=org,
            performed_by_email=admin_email,
            action=action,
            previous_status=previous_status,
            new_status=new_status,
            reason=reason,
            notes=notes,
        )


def start_service(org: Organization, *, using: str = "default", admin_email: str, notes: str = "") -> ServiceStatusChange:
    """First-time activation — e.g. a client whose service was never
    started. Functionally identical to resume; kept as a distinct action
    for a clearer audit trail."""
    return _transition(
        org, using=using, action=ServiceStatusChange.Action.START,
        new_status=Organization.ServiceStatus.ACTIVE, admin_email=admin_email, notes=notes,
    )


def stop_service(
    org: Organization, *, using: str = "default", admin_email: str, reason: str, notes: str = ""
) -> ServiceStatusChange:
    return _transition(
        org, using=using, action=ServiceStatusChange.Action.STOP,
        new_status=Organization.ServiceStatus.SUSPENDED, admin_email=admin_email, reason=reason, notes=notes,
    )


def suspend_service(
    org: Organization, *, using: str = "default", admin_email: str, reason: str, notes: str = ""
) -> ServiceStatusChange:
    return _transition(
        org, using=using, action=ServiceStatusChange.Action.SUSPEND,
        new_status=Organization.ServiceStatus.SUSPENDED, admin_email=admin_email, reason=reason, notes=notes,
    )


def resume_service(org: Organization, *, using: str = "default", admin_email: str, notes: str = "") -> ServiceStatusChange:
    return _transition(
        org, using=using, action=ServiceStatusChange.Action.RESUME,
        new_status=Organization.ServiceStatus.ACTIVE, admin_email=admin_email, notes=notes,
    )
