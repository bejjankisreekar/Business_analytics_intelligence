import logging
import secrets
import socket
from datetime import date, timedelta
from decimal import Decimal
from urllib.parse import quote, urlencode

from decouple import config
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db.models import Q, Value
from django.db.models.functions import Replace
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from io import BytesIO

from django.http import Http404, HttpResponse
from django.template.loader import render_to_string
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.safestring import mark_safe
from django.views import View

from apps.organizations.models import Organization

from .access import org_is_eligible, user_has_access
from .models import ProductSuggestion, QuoteRequest, QuoteRequestSettings, Unit
from .inbox import build_inbox_query
from .notify import notify_owner, owner_login_email
from .qr import qr_svg
from .services import (
    MAX_ROWS, RequestError, book_sale, create_request, set_payment, quoted_amounts, rate_limited, release_sale, save_quote,
    sync_product_suggestions, list_version, update_request,
)

logger = logging.getLogger(__name__)


class OwnerAccessMixin(LoginRequiredMixin):
    """Login + shop/restaurant org + owner/admin/manager role."""

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and not user_has_access(request.user):
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)


def _settings_for(org) -> QuoteRequestSettings:
    return QuoteRequestSettings.objects.get_or_create(organization=org)[0]


def _refresh_suggestions(org) -> None:
    """Best effort: a Sheets hiccup must never break the owner's page."""
    try:
        sync_product_suggestions(org)
    except Exception:
        logger.exception("Could not refresh product suggestions for %s", org.pk)


LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "[::1]")


def _detect_lan_ip():
    """This computer's Wi-Fi/LAN address (e.g. 10.155.196.76), or None if it can't be found.
    Connecting a UDP socket sends nothing; it only makes the OS pick the outgoing interface."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("10.255.255.255", 1))
            ip = sock.getsockname()[0]
    except OSError:
        return None
    return None if ip.startswith("127.") else ip


def _absolute_url(request, path: str) -> str:
    """A full link for QR codes and sharing.

    In production this is always the public site address (settings.SITE_URL, i.e.
    https://findayintelligence.com), so a printed QR never carries a proxy or internal host.

    In development it follows the address you opened the page with, except when that is
    127.0.0.1 or localhost: a phone can't reach those, so the computer's Wi-Fi address is
    used instead (or DEV_PUBLIC_HOST from .env, if you set one), keeping the same port.
    """
    if settings.IS_PRODUCTION:
        return settings.SITE_URL.rstrip("/") + path
    host = request.get_host()
    name, _, port = host.rpartition(":") if ":" in host and not host.endswith("]") else (host, "", "")
    if name in LOOPBACK_HOSTS:
        lan = config("DEV_PUBLIC_HOST", default="").strip() or _detect_lan_ip()
        if lan:
            return f"{request.scheme}://{lan}{':' + port if port else ''}{path}"
    return request.build_absolute_uri(path)


def _public_url(request, org) -> str:
    return _absolute_url(request, reverse("quote_requests:public_submit", args=[org.slug]))


def _with_amounts(requests):
    """Sets item_count and amount on each request from its prefetched items (no extra queries)."""
    for r in requests:
        items = list(r.items.all())
        r.item_count = len(items)
        r.amount = sum((i.line_total or Decimal("0") for i in items if i.available), Decimal("0"))
    return requests


class InboxView(OwnerAccessMixin, View):
    def get(self, request):
        org = request.user.organization
        _refresh_suggestions(org)
        inbox = build_inbox_query(org, request.GET)
        status, payment, q = inbox.status, inbox.payment, inbox.q
        day_key, day, explicit_day = inbox.day_key, inbox.day, inbox.explicit_day

        # The cards and the total describe the chosen day; the table below also honours the filters.
        scoped = inbox.scoped
        quoted = scoped.filter(status=QuoteRequest.Status.QUOTED)
        requests_qs = inbox.requests
        amounts = quoted_amounts(org, day)
        paid_pct = round(amounts["paid"] / amounts["total"] * 100) if amounts["total"] else 0

        day_param = (day.isoformat() if day_key == "date" else day_key) if explicit_day else ""
        base = reverse("quote_requests:inbox")

        def url(**override):
            params = {"day": day_param, "status": status, "payment": payment, "q": q}
            params.update(override)
            clean = {k: v for k, v in params.items() if v}
            return base + ("?" + urlencode(clean) if clean else "")

        if day is None:
            day_label = "All days"
        else:
            pretty = date_format(day, "D, d M Y")
            day_label = {"today": f"Today, {pretty}", "yesterday": f"Yesterday, {pretty}"}.get(day_key, pretty)
        today = timezone.localdate()

        return render(request, "quote_requests/inbox.html", {
            "active_nav": "customer_requests",
            "organization": org,
            "settings_obj": _settings_for(org),
            "owner_login_email": owner_login_email(org),
            "public_url": _public_url(request, org),
            # Oldest first (#1 on top, newest at the bottom); if there are more than 200,
            # keep the latest 200 rather than the first 200.
            "quote_requests": _with_amounts(list(requests_qs.order_by("-number")[:200])[::-1]),
            "amounts": amounts,
            "paid_pct": paid_pct,
            "status": status,
            "payment": payment,
            "q": q,
            "day": day_key,
            "day_label": day_label,
            "picked_date": day.isoformat() if day_key == "date" else "",
            "today_iso": today.isoformat(),
            "urls": {
                "today": url(day="today"), "yesterday": url(day="yesterday"), "all_days": url(day="all"),
                "all": url(status="", payment=""), "pending": url(status="PENDING", payment=""),
                "quoted": url(status="QUOTED", payment=""), "unpaid": url(payment="UNPAID", status=""),
                "paid": url(payment="PAID", status=""), "clear_search": url(q=""),
            },
            "keep_for_search": {"day": day_param, "status": status, "payment": payment},
            "keep_for_date": {"status": status, "payment": payment, "q": q},
            "pending_count": scoped.filter(status=QuoteRequest.Status.PENDING).count(),
            "quoted_count": quoted.count(),
            "paid_count": quoted.filter(payment_status=QuoteRequest.PaymentStatus.PAID).count(),
            "unpaid_count": quoted.filter(payment_status=QuoteRequest.PaymentStatus.PENDING).count(),
            "total_count": scoped.count(),
        })


class QrPageView(OwnerAccessMixin, View):
    """Print-ready poster: the shop sticks it up and customers scan it."""

    def get(self, request):
        org = request.user.organization
        _refresh_suggestions(org)
        url = _public_url(request, org)
        return render(request, "quote_requests/qr_poster.html", {
            "organization": org,
            "public_url": url,
            "qr": mark_safe(qr_svg(url)),
            "is_enabled": _settings_for(org).is_enabled,
        })


class QrDownloadView(OwnerAccessMixin, View):
    def get(self, request):
        org = request.user.organization
        response = HttpResponse(qr_svg(_public_url(request, org), size_attrs=True), content_type="image/svg+xml")
        response["Content-Disposition"] = f'attachment; filename="{org.slug}-request-qr.svg"'
        return response


class NotifySettingsView(OwnerAccessMixin, View):
    """How the shop hears about new requests: an email, and the WhatsApp number customers can message."""

    def post(self, request):
        cfg = _settings_for(request.user.organization)
        email = (request.POST.get("notify_email") or "").strip()
        whatsapp = (request.POST.get("notify_whatsapp") or "").strip()
        try:
            if email:
                validate_email(email)
        except ValidationError:
            messages.error(request, "That email address doesn't look right.")
            return redirect("quote_requests:inbox")
        digits = "".join(ch for ch in whatsapp if ch.isdigit())
        if whatsapp and not 7 <= len(digits) <= 15:
            messages.error(request, "Enter a valid WhatsApp number, or leave it blank.")
            return redirect("quote_requests:inbox")
        cfg.notify_email_enabled = request.POST.get("notify_email_enabled") == "on"
        cfg.notify_email = email
        cfg.notify_whatsapp = whatsapp[:20]
        cfg.save(update_fields=["notify_email_enabled", "notify_email", "notify_whatsapp"])
        messages.success(request, "Notification settings saved.")
        return redirect("quote_requests:inbox")


class ToggleView(OwnerAccessMixin, View):
    def post(self, request):
        obj = _settings_for(request.user.organization)
        obj.is_enabled = not obj.is_enabled
        obj.save(update_fields=["is_enabled"])
        messages.success(request, "Open link turned on." if obj.is_enabled else "Open link turned off.")
        return redirect("quote_requests:inbox")


class PaymentView(OwnerAccessMixin, View):
    """Owner marks a quoted request as paid or back to pending (entered by hand)."""

    def post(self, request, number):
        req = get_object_or_404(QuoteRequest, organization=request.user.organization, number=number)
        paid = request.POST.get("payment") == "paid"
        try:
            set_payment(req, paid=paid, mode=request.POST.get("mode", ""), email=request.user.email)
        except RequestError as exc:
            messages.error(request, str(exc))
            return redirect("quote_requests:detail", number=number)
        if paid:
            messages.success(request, "Marked as paid and added to your Daily Book as a sale. This order can be delivered.")
        else:
            messages.success(request, "Marked as payment pending. The sale was removed from your Daily Book.")
        return redirect("quote_requests:detail", number=number)


def _quote_pdf_response(req, items):
    """The quotation as a PDF: every item with quantity, rate and amount, plus the total."""
    from xhtml2pdf import pisa

    html = render_to_string("quote_requests/quote_pdf.html", {
        "req": req, "org": req.organization, "items": items,
        "item_count": len(items), "available_count": sum(1 for i in items if i.available),
    })
    buffer = BytesIO()
    pisa.CreatePDF(src=html, dest=buffer)
    response = HttpResponse(buffer.getvalue(), content_type="application/pdf")
    response["Content-Disposition"] = f'inline; filename="quote-{req.organization.slug}-{req.number}.pdf"'
    response["Cache-Control"] = "private, no-store"
    return response


def _wa_digits(phone: str, org) -> str:
    """Digits for wa.me; a bare 10-digit number of an Indian shop gets the +91 prefix WhatsApp needs."""
    digits = "".join(ch for ch in phone if ch.isdigit())
    if len(digits) == 10 and org.country.strip().lower() == "india":
        digits = "91" + digits
    return digits


def _whatsapp_phone(req) -> str:
    """Digits for wa.me; a bare 10-digit number of an Indian shop gets the +91 prefix WhatsApp needs."""
    digits = "".join(ch for ch in req.customer_phone if ch.isdigit())
    if len(digits) == 10 and req.organization.country.strip().lower() == "india":
        digits = "91" + digits
    return digits


class OwnerQuotePdfView(OwnerAccessMixin, View):
    def get(self, request, number):
        req = get_object_or_404(
            QuoteRequest.objects.select_related("organization"),
            organization=request.user.organization, number=number,
        )
        if req.status != QuoteRequest.Status.QUOTED:
            raise Http404
        return _quote_pdf_response(req, list(req.items.all()))


class PublicQuotePdfView(View):
    """The same PDF for the customer, behind their private link's token."""

    def get(self, request, token):
        req = get_object_or_404(QuoteRequest.objects.select_related("organization"), tracking_token=token)
        if req.status != QuoteRequest.Status.QUOTED:
            raise Http404
        response = _quote_pdf_response(req, list(req.items.all()))
        response["X-Robots-Tag"] = "noindex, nofollow"
        return response


class DetailView(OwnerAccessMixin, View):
    def _render(self, request, req):
        return render(request, "quote_requests/detail.html", {
            "active_nav": "customer_requests",
            "organization": request.user.organization,
            "req": req,
            "items": req.items.all(),
            "tracking_url": _absolute_url(
                request, reverse("quote_requests:public_track", args=[req.tracking_token])
            ),
            "version": list_version(req),
            "pdf_url": reverse("quote_requests:owner_pdf", args=[req.number]),
            "public_pdf_url": _absolute_url(
                request, reverse("quote_requests:public_pdf", args=[req.tracking_token])
            ),
            "whatsapp_phone": _whatsapp_phone(req),
        })

    def _get(self, request, number):
        return get_object_or_404(QuoteRequest, organization=request.user.organization, number=number)

    def get(self, request, number):
        return self._render(request, self._get(request, number))

    def post(self, request, number):
        req = self._get(request, number)
        send = "send" in request.POST
        try:
            save_quote(req, request.POST, send=send)
        except RequestError as exc:
            messages.error(request, str(exc))
            return redirect("quote_requests:detail", number=number)
        messages.success(request, "Quote sent — the customer can now see it." if send else "Draft saved.")
        return redirect("quote_requests:detail", number=number)


def _open_org_or_404(slug) -> Organization:
    """The org behind an open link — only if it is a shop/restaurant that switched the link on."""
    org = get_object_or_404(Organization, slug=slug, is_active=True)
    enabled = QuoteRequestSettings.objects.filter(organization=org, is_enabled=True).exists()
    if not (enabled and org.is_service_active and org_is_eligible(org)):
        raise Http404
    return org


class PublicSubmitView(View):
    """The open page a shop owner shares: no login, spreadsheet-style list."""

    def _render(self, request, org, rows, error="", form=None, status=200):
        return render(request, "quote_requests/public_submit.html", {
            "org": org, "rows": rows, "error": error, "form": form or {}, "max_rows": MAX_ROWS, "units": Unit.choices,
            "suggestions": ProductSuggestion.objects.filter(organization=org).values_list("name", flat=True),
        }, status=status)

    def get(self, request, slug):
        org = _open_org_or_404(slug)
        return self._render(request, org, rows=[{}])

    def post(self, request, slug):
        org = _open_org_or_404(slug)
        products = request.POST.getlist("product")[:MAX_ROWS + 20]
        qtys = request.POST.getlist("qty")[:MAX_ROWS + 20]
        units = request.POST.getlist("unit")[:MAX_ROWS + 20]
        form = {
            "name": request.POST.get("name", ""), "phone": request.POST.get("phone", ""),
            "address": request.POST.get("address", ""),
        }
        rows = [{"product": p, "qty": q, "unit": u} for p, q, u in zip(products, qtys, units)] or [{}]

        if rate_limited(request):
            return self._render(request, org, rows, "Too many requests from this device. Please try again later.", form, 429)
        try:
            req = create_request(org, form["name"], form["phone"], products, qtys, units, form["address"])
            notify_owner(req, _absolute_url(request, reverse("quote_requests:detail", args=[req.number])))
        except RequestError as exc:
            return self._render(request, org, rows, str(exc), form, 400)
        return redirect(reverse("quote_requests:public_track", args=[req.tracking_token]) + "?new=1")


def _format_qty(qty) -> str:
    """250.000 -> "250", 1.500 -> "1.5" (Decimal.normalize alone would give 2.5E+2)."""
    return format(qty.normalize(), "f")


class PublicEditView(View):
    """The customer changes their list, until the shop has sent the quote."""

    def _get_request(self, token):
        return get_object_or_404(QuoteRequest.objects.select_related("organization"), tracking_token=token)

    def _render(self, request, req, rows, error="", form=None, status=200):
        response = render(request, "quote_requests/public_submit.html", {
            "org": req.organization, "rows": rows, "error": error,
            "form": form or {"name": req.customer_name, "phone": req.customer_phone, "address": req.customer_address},
            "max_rows": MAX_ROWS, "units": Unit.choices, "editing": True, "req": req,
            "suggestions": ProductSuggestion.objects.filter(organization=req.organization).values_list("name", flat=True),
        }, status=status)
        response["Cache-Control"] = "private, no-store"
        response["X-Robots-Tag"] = "noindex, nofollow"
        return response

    def get(self, request, token):
        req = self._get_request(token)
        if req.status != QuoteRequest.Status.PENDING:
            return redirect("quote_requests:public_track", token=token)
        rows = [{"product": i.product_name, "qty": _format_qty(i.qty), "unit": i.unit} for i in req.items.all()]
        return self._render(request, req, rows)

    def post(self, request, token):
        req = self._get_request(token)
        products = request.POST.getlist("product")[:MAX_ROWS + 20]
        qtys = request.POST.getlist("qty")[:MAX_ROWS + 20]
        units = request.POST.getlist("unit")[:MAX_ROWS + 20]
        form = {
            "name": request.POST.get("name", ""), "phone": request.POST.get("phone", ""),
            "address": request.POST.get("address", ""),
        }
        rows = [{"product": p, "qty": q, "unit": u} for p, q, u in zip(products, qtys, units)] or [{}]

        if rate_limited(request):
            return self._render(request, req, rows, "Too many requests from this device. Please try again later.", form, 429)
        try:
            update_request(req, form["name"], form["phone"], products, qtys, units, form["address"])
        except RequestError as exc:
            # The shop may have sent the quote while the customer was typing.
            if QuoteRequest.objects.filter(pk=req.pk, status=QuoteRequest.Status.QUOTED).exists():
                return redirect("quote_requests:public_track", token=token)
            return self._render(request, req, rows, str(exc), form, 400)
        req.refresh_from_db()
        notify_owner(req, _absolute_url(request, reverse("quote_requests:detail", args=[req.number])), updated=True)
        return redirect(reverse("quote_requests:public_track", args=[token]) + "?updated=1")


def _shop_whatsapp_url(req, org, items) -> str:
    """A wa.me link that opens a ready-made message to the shop's WhatsApp, or "" if none is set up."""
    cfg = QuoteRequestSettings.objects.filter(organization=org).first()
    digits = _wa_digits(cfg.notify_whatsapp, org) if cfg and cfg.notify_whatsapp else ""
    if not digits:
        return ""
    lines = [f"Hi {org.name}, this is {req.customer_name}. I have sent my list (order #{req.number}):"]
    lines += [f"{i.serial}. {i.product_name} - {_format_qty(i.qty)} {i.get_unit_display()}" for i in items]
    lines.append("Please check and reply with prices.")
    return f"https://wa.me/{digits}?text={quote(chr(10).join(lines))}"


def _render_track(request, req, readonly):
    org = req.organization
    items = list(req.items.all())
    share_url = ""
    if not readonly:
        if not req.share_token:
            req.share_token = secrets.token_urlsafe(16)
            req.save(update_fields=["share_token"])
        share_url = request.build_absolute_uri(reverse("quote_requests:public_share", args=[req.share_token]))
    response = render(request, "quote_requests/public_track.html", {
        "req": req, "org": org, "items": items, "readonly": readonly,
        "item_count": len(items), "available_count": sum(1 for i in items if i.available),
        "share_url": share_url,
        "share_title": f"{org.name} - order #{req.number}",
        "shop_whatsapp_url": "" if readonly else _shop_whatsapp_url(req, org, items),
        "just_submitted": not readonly and request.GET.get("new") == "1",
        "just_updated": not readonly and request.GET.get("updated") == "1",
    })
    # A private link: keep it out of search engines and caches.
    response["X-Robots-Tag"] = "noindex, nofollow"
    response["Cache-Control"] = "private, no-store"
    return response


class PublicTrackView(View):
    """The customer's private link: their list, and the shop's prices once quoted."""

    def get(self, request, token):
        req = get_object_or_404(QuoteRequest.objects.select_related("organization"), tracking_token=token)
        return _render_track(request, req, readonly=False)


class PublicShareView(View):
    """The link a customer shares: the same page, view-only and without contact details."""

    def get(self, request, token):
        req = get_object_or_404(QuoteRequest.objects.select_related("organization"), share_token=token)
        return _render_track(request, req, readonly=True)
