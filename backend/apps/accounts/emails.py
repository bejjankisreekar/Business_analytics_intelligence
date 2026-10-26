"""Transactional emails to client users: the one-time onboarding (welcome)
email sent right after signup, and the password-reset code."""

import logging

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils import timezone

from apps.organizations.models import Organization

from .models import OTP_VALIDITY

logger = logging.getLogger(__name__)


def _send(subject, *, to, template, context):
    """Render `<template>.txt` + `<template>.html` and send them as one
    multipart email. Replies go to the support inbox, since the sender is a
    no-reply address. Raises on failure — callers decide how to degrade."""
    message = EmailMultiAlternatives(
        subject=subject,
        body=render_to_string(f"{template}.txt", context),
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=to,
        reply_to=[settings.CONTACT_EMAIL] if settings.CONTACT_EMAIL else None,
    )
    message.attach_alternative(render_to_string(f"{template}.html", context), "text/html")
    message.send()


def send_password_reset_email(user, code):
    """Raises on failure, so the forgot-password form can tell the user."""
    _send(
        f"Your {settings.SITE_NAME} password reset code",
        to=[user.email],
        template="emails/password_reset",
        context={
            "site_name": settings.SITE_NAME,
            "first_name": user.first_name,
            "code": code,
            "validity_minutes": int(OTP_VALIDITY.total_seconds() // 60),
            "email": user.email,
            "support_email": settings.CONTACT_EMAIL,
            "year": timezone.localdate().year,
        },
    )


def _trial_days(subscription):
    """Trial length actually granted — the subscription's own dates, falling
    back to the plan's trial_days (what start_trial() copied them from)."""
    if subscription is None:
        return 0
    if subscription.trial_start_date and subscription.trial_end_date:
        return (subscription.trial_end_date - subscription.trial_start_date).days
    return subscription.plan.trial_days or 0


def build_welcome_context(organization, user, subscription, *, dashboard_url, manager_logins=None):
    plan = subscription.plan if subscription else None
    trial_days = _trial_days(subscription)
    logins = manager_logins or organization.manager_logins
    is_sheets = organization.storage_mode == Organization.StorageMode.GOOGLE_SHEETS
    return {
        "site_name": settings.SITE_NAME,
        "first_name": user.first_name or user.username,
        "organization": organization,
        "user": user,
        "plan": plan,
        "plan_features": plan.feature_labels() if plan else [],
        "monthly_price": plan.effective_monthly_price if plan else None,
        "trial_days": trial_days,
        "trial_end_date": subscription.trial_end_date if subscription else None,
        "storage_label": organization.get_storage_mode_display(),
        "is_sheets": is_sheets,
        "logins_label": Organization.ManagerLogins(int(logins)).label if logins else "",
        "business_type": organization.get_business_type_display(),
        "size": organization.get_size_display(),
        "dashboard_url": dashboard_url,
        "support_email": settings.CONTACT_EMAIL,
        "year": timezone.localdate().year,
    }


def send_welcome_email(organization, user, subscription, *, dashboard_url, manager_logins=None) -> bool:
    """Send the onboarding email to the owner's signup address — at most once
    per organization. The welcome_email_sent_at claim is a single conditional
    UPDATE, so a double-submitted signup or a retry can't send it twice. A
    failed send releases the claim (so it can be retried) and never raises:
    signup must not fail just because email is down."""
    claimed = Organization.objects.filter(
        pk=organization.pk, welcome_email_sent_at__isnull=True
    ).update(welcome_email_sent_at=timezone.now())
    if not claimed:
        return False

    context = build_welcome_context(
        organization, user, subscription, dashboard_url=dashboard_url, manager_logins=manager_logins
    )
    if context["trial_days"]:
        subject = f"Welcome to {settings.SITE_NAME} — your {context['trial_days']}-day free trial has started"
    else:
        subject = f"Welcome to {settings.SITE_NAME}, {organization.name}"
    try:
        _send(subject, to=[user.email], template="emails/welcome", context=context)
    except Exception:
        logger.exception("Failed to send welcome email to %s (org %s)", user.email, organization.pk)
        Organization.objects.filter(pk=organization.pk).update(welcome_email_sent_at=None)
        return False
    return True
