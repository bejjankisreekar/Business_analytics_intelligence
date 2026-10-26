"""The superadmin alert digest: the overview's "Needs attention" items for
one environment, sent by email (and to Slack when a webhook is set).

Run daily from a scheduler: `python manage.py superadmin_digest --env prod`.
"""
import json
import logging
import urllib.request

from django.conf import settings
from django.core.mail import send_mail
from django.db import DatabaseError
from django.template.loader import render_to_string
from django.utils import timezone

from apps.accounts.models import User

from .alerts import attention_items

logger = logging.getLogger(__name__)
ENV_LABELS = {"dev": "Development", "prod": "Production"}


def _recipients(env):
    if settings.SUPERADMIN_DIGEST_EMAILS:
        return list(settings.SUPERADMIN_DIGEST_EMAILS)
    try:
        return list(User.objects.using(env).filter(role=User.Role.SUPER_ADMIN, is_active=True)
                    .values_list("email", flat=True))
    except DatabaseError:
        return []


def _text(env, items, base_url):
    lines = [f"Finday Ops — {ENV_LABELS[env]} alerts, {timezone.localdate():%d %b %Y}", ""]
    if not items:
        lines.append("Nothing needs attention. 🎉")
    for i in items:
        amount = f" (₹{i['amount']:,.0f})" if i.get("amount") else ""
        lines.append(f"• [{i['severity'].upper()}] {i['title']}: {i['count']}{amount} — {i['detail']}")
        if base_url:
            lines.append(f"  {base_url.rstrip('/')}{i['href']}")
    return "\n".join(lines)


def send_digest(env: str, *, recipients=None, base_url: str = "", slack: bool = True) -> dict:
    result = {"sent_email": False, "sent_slack": False, "item_count": 0, "error": ""}
    try:
        items = attention_items(env, include_activity=False)
    except DatabaseError as exc:
        result["error"] = f"{ENV_LABELS[env]} database unavailable ({exc.__class__.__name__})"
        return result
    result["item_count"] = len(items)
    text = _text(env, items, base_url)
    subject = (f"[Finday Ops] {ENV_LABELS[env]}: {len(items)} item{'s' if len(items) != 1 else ''} need attention"
               if items else f"[Finday Ops] {ENV_LABELS[env]}: all clear")

    to = recipients or _recipients(env)
    if to:
        html = render_to_string("superadmin/email/digest.html", {
            "env_label": ENV_LABELS[env], "items": items, "base_url": base_url.rstrip("/"),
            "today": timezone.localdate(),
        })
        try:
            send_mail(subject, text, settings.DEFAULT_FROM_EMAIL, to, html_message=html)
            result["sent_email"] = True
        except Exception as exc:  # noqa: BLE001 — report, don't crash the caller
            logger.exception("Superadmin digest email failed")
            result["error"] = f"email failed ({exc.__class__.__name__})"

    if slack and settings.SUPERADMIN_SLACK_WEBHOOK_URL:
        try:
            req = urllib.request.Request(
                settings.SUPERADMIN_SLACK_WEBHOOK_URL, data=json.dumps({"text": text}).encode(),
                headers={"Content-Type": "application/json"},
            )
            urllib.request.urlopen(req, timeout=10).read()
            result["sent_slack"] = True
        except Exception as exc:  # noqa: BLE001
            logger.exception("Superadmin digest Slack post failed")
            result["error"] = (result["error"] + "; " if result["error"] else "") + f"Slack failed ({exc.__class__.__name__})"
    return result
