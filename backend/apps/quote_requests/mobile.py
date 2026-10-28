"""The mobile app's side of Customer Requests: the list, one request, quoting, payment, and
registering a phone for push notifications. JSON, behind the app's bearer-token login."""

import functools
import json
from decimal import Decimal

from django.db import transaction
from django.http import JsonResponse
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import date_format
from django.views.decorators.csrf import csrf_exempt

from apps.mobile_api.auth import api_login_required

from .access import user_has_access
from .inbox import build_inbox_query
from .models import DeviceToken, QuoteRequest
from .services import RequestError, list_version, quoted_amounts, save_quote, set_payment
from .views import _absolute_url, _format_qty, _with_amounts


def _error(message: str, status: int, code: str):
    return JsonResponse({"error": message, "code": code}, status=status)


def _body(request) -> dict:
    try:
        data = json.loads(request.body or b"{}")
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def requests_api(view):
    """Signed in (JSON 401/403 otherwise), and the organization is allowed Customer Requests."""

    @functools.wraps(view)
    def wrapper(request, *args, **kwargs):
        if not user_has_access(request.user):
            return _error("Customer requests are not available for this account.", 403, "unavailable")
        return view(request, *args, **kwargs)

    return csrf_exempt(api_login_required(wrapper))


def _money(value) -> str:
    return str((value or Decimal("0")).quantize(Decimal("0.01")))


def _summary_json(r) -> dict:
    return {
        "number": r.number,
        "customer_name": r.customer_name,
        "customer_phone": r.customer_phone,
        "customer_address": r.customer_address,
        "status": r.status,
        "payment_status": r.payment_status,
        "item_count": r.item_count,
        "amount": _money(r.amount),
        "created_at": timezone.localtime(r.created_at).isoformat(),
        "edited": r.edited_at is not None,
    }


def _day_label(key, day) -> str:
    if day is None:
        return "All days"
    pretty = date_format(day, "D, d M Y")
    return {"today": f"Today, {pretty}", "yesterday": f"Yesterday, {pretty}"}.get(key, pretty)


@requests_api
def list_requests(request):
    if request.method != "GET":
        return _error("Method not allowed.", 405, "method_not_allowed")
    org = request.user.organization
    inbox = build_inbox_query(org, request.GET)
    scoped, quoted = inbox.scoped, inbox.scoped.filter(status=QuoteRequest.Status.QUOTED)
    amounts = quoted_amounts(org, inbox.day)
    rows = _with_amounts(list(inbox.requests.order_by("-number")[:200])[::-1])   # oldest first, like the web list
    return JsonResponse({
        "day": {
            "key": inbox.day_key, "label": _day_label(inbox.day_key, inbox.day),
            "date": inbox.day.isoformat() if inbox.day else None,
        },
        "filters": {"status": inbox.status, "payment": inbox.payment, "q": inbox.q},
        "currency": org.currency,
        "counts": {
            "total": scoped.count(),
            "pending": scoped.filter(status=QuoteRequest.Status.PENDING).count(),
            "quoted": quoted.count(),
            "paid": quoted.filter(payment_status=QuoteRequest.PaymentStatus.PAID).count(),
            "unpaid": quoted.filter(payment_status=QuoteRequest.PaymentStatus.PENDING).count(),
        },
        "amounts": {key: _money(value) for key, value in amounts.items()},
        # Every day: what the tab's badge shows, so a request from yesterday is never forgotten.
        "waiting_total": org.quote_requests.filter(status=QuoteRequest.Status.PENDING).count(),
        "requests": [_summary_json(r) for r in rows],
    })


def _detail_json(request, req) -> dict:
    items = list(req.items.all())
    return {
        "currency": req.organization.currency,
        "request": {
            "number": req.number,
            "customer_name": req.customer_name,
            "customer_phone": req.customer_phone,
            "customer_address": req.customer_address,
            "status": req.status,
            "payment_status": req.payment_status,
            "payment_mode": req.payment_mode,
            "paid_at": timezone.localtime(req.paid_at).isoformat() if req.paid_at else None,
            "created_at": timezone.localtime(req.created_at).isoformat(),
            "edited_at": timezone.localtime(req.edited_at).isoformat() if req.edited_at else None,
            "version": list_version(req),
            "total": _money(req.total),
            "customer_link": _absolute_url(request, reverse("quote_requests:public_track", args=[req.tracking_token])),
            "pdf_link": _absolute_url(request, reverse("quote_requests:public_pdf", args=[req.tracking_token]))
            if req.status == QuoteRequest.Status.QUOTED else None,
        },
        "items": [
            {
                "id": i.pk, "serial": i.serial, "product_name": i.product_name,
                "qty": _format_qty(i.qty), "unit": i.unit, "unit_label": i.get_unit_display(),
                "available": i.available,
                "price": None if i.price is None else _money(i.price),
                "remark": i.remark,
                "line_total": None if i.line_total is None else _money(i.line_total),
            }
            for i in items
        ],
    }


def _get_request(request, number):
    return (
        QuoteRequest.objects.select_related("organization")
        .filter(organization=request.user.organization, number=number).first()
    )


@requests_api
def request_detail(request, number):
    if request.method != "GET":
        return _error("Method not allowed.", 405, "method_not_allowed")
    req = _get_request(request, number)
    if req is None:
        return _error("Request not found.", 404, "not_found")
    return JsonResponse(_detail_json(request, req))


@requests_api
def quote(request, number):
    """Saves the owner's availability, rates and remarks; `send: true` also sends the quote."""
    if request.method != "POST":
        return _error("Method not allowed.", 405, "method_not_allowed")
    req = _get_request(request, number)
    if req is None:
        return _error("Request not found.", 404, "not_found")
    data = _body(request)
    rows = data.get("items")
    if not isinstance(rows, list):
        return _error("items must be a list.", 400, "invalid")

    known = {i.pk for i in req.items.all()}
    posted = {}
    for row in rows:
        if not isinstance(row, dict) or row.get("id") not in known:
            return _error("Unknown item in the list.", 400, "invalid")
        pk = row["id"]
        price = row.get("price")
        posted[f"available_{pk}"] = "on" if row.get("available") else ""
        posted[f"price_{pk}"] = "" if price in (None, "") else str(price)
        posted[f"remark_{pk}"] = str(row.get("remark") or "")
    if {row["id"] for row in rows} != known:
        return _error("Send every item of the request.", 400, "invalid")
    if data.get("version") is not None:
        posted["version"] = str(data["version"])

    try:
        with transaction.atomic():
            save_quote(req, posted, send=bool(data.get("send")))
    except RequestError as exc:
        stale = str(exc).startswith("The customer just changed")
        return _error(str(exc), 409 if stale else 400, "stale_list" if stale else "invalid")
    req.refresh_from_db()
    return JsonResponse(_detail_json(request, req))


@requests_api
def payment(request, number):
    """Marks a quoted request Paid (Cash or Online) or back to pending, booking or removing the sale."""
    if request.method != "POST":
        return _error("Method not allowed.", 405, "method_not_allowed")
    req = _get_request(request, number)
    if req is None:
        return _error("Request not found.", 404, "not_found")
    data = _body(request)
    try:
        set_payment(req, paid=data.get("payment") == "paid", mode=str(data.get("mode") or ""), email=request.user.email)
    except RequestError as exc:
        return _error(str(exc), 400, "invalid")
    req.refresh_from_db()
    return JsonResponse(_detail_json(request, req))


@requests_api
def devices(request):
    """POST registers this phone for push notifications; DELETE forgets it (on sign-out)."""
    data = _body(request)
    token = str(data.get("token") or request.GET.get("token") or "").strip()
    if request.method == "POST":
        if not token or len(token) > 255:
            return _error("A device token is required.", 400, "invalid")
        # A phone can be handed to someone else: the token always belongs to whoever signed in last.
        DeviceToken.objects.update_or_create(
            token=token, defaults={"user": request.user, "platform": str(data.get("platform") or "")[:20]})
        return JsonResponse({"ok": True})
    if request.method == "DELETE":
        DeviceToken.objects.filter(token=token, user=request.user).delete()
        return JsonResponse({"ok": True})
    return _error("Method not allowed.", 405, "method_not_allowed")
