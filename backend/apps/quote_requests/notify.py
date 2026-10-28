"""Telling the shop owner that a customer sent (or changed) a request."""

import logging
import threading

from django.conf import settings
from django.core.mail import EmailMessage
from django.utils import timezone
from django.utils.formats import date_format

from apps.accounts.models import User

from .models import QuoteRequestSettings

logger = logging.getLogger(__name__)


def owner_login_email(org) -> str:
    """The organization owner's own login email."""
    owner = User.objects.filter(organization=org, role=User.Role.OWNER).order_by("email").first()
    return owner.email if owner else ""


def recipient_for(org, cfg) -> str:
    """Where owner emails go: the address the owner chose, else their login email."""
    return (cfg.notify_email if cfg else "") or owner_login_email(org) or org.contact_email


def _qty(qty) -> str:
    return format(qty.normalize(), "f")


def build_owner_email(req, owner_url: str, *, updated: bool):
    """(subject, body, recipient) for the owner, or None when emails are off or no address is known."""
    org = req.organization
    cfg = QuoteRequestSettings.objects.filter(organization=org).first()
    if cfg is not None and not cfg.notify_email_enabled:
        return None
    to = recipient_for(org, cfg)
    if not to:
        return None

    items = list(req.items.all())
    received = date_format(timezone.localtime(req.created_at), "d M Y, H:i")
    lines = [
        f"{org.name}: {'a customer changed their list' if updated else 'new customer request'} (#{req.number})",
        "",
        f"Customer: {req.customer_name}",
        f"Phone: {req.customer_phone}",
    ]
    if req.customer_address:
        lines.append(f"Address: {req.customer_address}")
    lines += [f"Received: {received}", "", f"Items ({len(items)}):"]
    lines += [f"  {i.serial}. {i.product_name} - {_qty(i.qty)} {i.get_unit_display()}" for i in items]
    lines += [
        "",
        "Open it, check what you have and send your quote:",
        owner_url,
        "",
        f"You get this because customer requests are switched on for {org.name}. "
        "You can change or turn off these emails on the Customer Requests page.",
    ]
    subject = f"[{org.name}] {'Updated' if updated else 'New'} customer request #{req.number} from {req.customer_name}"
    return subject, "\n".join(lines), to


def _run_in_background(job) -> None:
    """Runs `job` without making the customer wait (inline while testing). A job here only talks to
    mail or Google, never to our own database, so the thread needs no connection of its own."""
    def run():
        try:
            job()
        except Exception:
            logger.exception("A notification to the owner failed")

    if settings.TESTING:
        run()
    else:
        threading.Thread(target=run, daemon=True).start()


def _deliver(message: EmailMessage) -> None:
    _run_in_background(message.send)


def notify_owner(req, owner_url: str, *, updated: bool = False) -> None:
    """Tells the owner by email and by push notification to their phone. Never raises: a mail or
    push problem must not break the customer's submit."""
    try:
        built = build_owner_email(req, owner_url, updated=updated)
        if built is not None:
            subject, body, to = built
            _deliver(EmailMessage(subject=subject, body=body, from_email=settings.DEFAULT_FROM_EMAIL, to=[to]))
    except Exception:
        logger.exception("Could not prepare the owner email for request %s", req.pk)
    try:
        from . import push

        job = push.prepare_for_request(req, updated=updated)
        if job is not None:
            _run_in_background(job)
    except Exception:
        logger.exception("Could not prepare the push notification for request %s", req.pk)
