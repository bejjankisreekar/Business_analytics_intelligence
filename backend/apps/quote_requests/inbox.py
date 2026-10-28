"""How the owner's list of requests is filtered: by day, search text, status and payment.

One routine used by both the web page and the mobile app's API, so the two always agree.
"""

from dataclasses import dataclass
from datetime import date, timedelta

from django.db.models import Q, QuerySet, Value
from django.db.models.functions import Replace
from django.utils import timezone

from .models import QuoteRequest


def search(queryset, text):
    """Finds requests by customer name, address, phone (spaces and dashes ignored), order number
    or any item the customer asked for."""
    number = text.lstrip("#").strip()
    if number.isdigit() and len(number) < 4:
        # "2" or "#2" means order number 2, not "any address or item containing a 2"
        return queryset.filter(number=int(number))

    query = (
        Q(customer_name__icontains=text) | Q(customer_address__icontains=text)
        | Q(items__product_name__icontains=text)
    )
    digits = "".join(ch for ch in text if ch.isdigit())
    if len(digits) >= 4:        # shorter digit runs would match almost every phone number
        queryset = queryset.annotate(phone_digits=Replace(
            Replace(Replace("customer_phone", Value(" "), Value("")), Value("-"), Value("")), Value("+"), Value("")))
        query |= Q(phone_digits__icontains=digits)
    if number.isdigit():
        query |= Q(number=int(number))
    return queryset.filter(query).distinct()


def day_scope(params, has_search):
    """Which day the list shows: today unless told otherwise (yesterday, a picked date, or all days).

    A search made without choosing a day looks across all days, so an older customer is still found.
    Returns (key, day, explicit): key is today / yesterday / date / all, day is a date or None for
    all days, and explicit says whether the request named a day (so links keep it)."""
    raw = (params.get("day") or "").strip().lower()
    today = timezone.localdate()
    yesterday = today - timedelta(days=1)
    if raw == "all":
        return "all", None, True
    if raw == "today":
        return "today", today, True
    if raw == "yesterday":
        return "yesterday", yesterday, True
    try:
        picked = date.fromisoformat(raw)
    except ValueError:
        return ("all", None, False) if has_search else ("today", today, False)
    key = "today" if picked == today else "yesterday" if picked == yesterday else "date"
    return key, picked, True


@dataclass
class InboxQuery:
    day_key: str
    day: date | None
    explicit_day: bool
    status: str
    payment: str
    q: str
    scoped: QuerySet        # the chosen day only: what the cards and totals describe
    requests: QuerySet      # scoped, then narrowed by search, status and payment


def build_inbox_query(org, params) -> InboxQuery:
    status = params.get("status", "")
    payment = params.get("payment", "")
    q = (params.get("q") or "").strip()[:100]
    day_key, day, explicit_day = day_scope(params, bool(q))

    scoped = org.quote_requests.all() if day is None else org.quote_requests.filter(created_at__date=day)
    requests = scoped.prefetch_related("items")
    if q:
        requests = search(requests, q)
    if status in QuoteRequest.Status.values:
        requests = requests.filter(status=status)
    elif payment == "PAID":
        requests = requests.filter(status=QuoteRequest.Status.QUOTED, payment_status=QuoteRequest.PaymentStatus.PAID)
    elif payment == "UNPAID":
        requests = requests.filter(status=QuoteRequest.Status.QUOTED, payment_status=QuoteRequest.PaymentStatus.PENDING)
    else:
        payment = ""
    if status not in QuoteRequest.Status.values:
        status = ""
    return InboxQuery(day_key, day, explicit_day, status, payment, q, scoped, requests)
