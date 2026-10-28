import re
from decimal import Decimal, InvalidOperation

from django.core.cache import cache
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from apps.organizations.models import Organization

from .models import ProductSuggestion, QuoteRequest, QuoteRequestItem, Unit

MAX_ROWS = 100
SUBMITS_PER_HOUR = 10  # per IP, on the open link


class RequestError(Exception):
    """Bad input from the customer or owner; the message is safe to show."""


def parse_qty(raw):
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        value = Decimal(raw)
    except InvalidOperation:
        raise RequestError(f"'{raw}' is not a valid quantity.")
    if not value.is_finite() or value <= 0 or value >= Decimal("1000000"):
        raise RequestError("Quantities must be greater than zero.")
    return value


def parse_price(raw):
    raw = (raw or "").strip().replace(",", "")
    if not raw:
        return None
    try:
        value = Decimal(raw)
    except InvalidOperation:
        raise RequestError(f"'{raw}' is not a valid price.")
    if not value.is_finite() or value < 0 or value >= Decimal("100000000"):
        raise RequestError("Price must be zero or more.")
    return value


def client_ip(request) -> str:
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    return (forwarded.split(",")[0].strip() or request.META.get("REMOTE_ADDR", "")) or "unknown"


def rate_limited(request) -> bool:
    """Counts a submit attempt against this IP; True once over the hourly cap."""
    key = f"quote-req-submit:{client_ip(request)}"
    cache.add(key, 0, timeout=3600)
    try:
        return cache.incr(key) > SUBMITS_PER_HOUR
    except ValueError:
        return False


def clean_address(raw) -> str:
    """Optional. Line breaks become spaces so it always reads as one tidy line."""
    return " ".join((raw or "").split())[:255]


def clean_phone(raw) -> str:
    phone = (raw or "").strip()
    digits = re.sub(r"\D", "", phone)
    if not 7 <= len(digits) <= 15:
        raise RequestError("Please enter a valid phone number.")
    return phone[:20]


def _clean_rows(products, qtys, units):
    """Validates a submitted list and returns (product, qty, unit) rows, blanks dropped."""
    rows = []
    for product, qty_raw, unit in zip(products, qtys, units):
        product = (product or "").strip()
        qty = parse_qty(qty_raw)
        if not product and qty is None:
            continue  # blank row
        if not product:
            raise RequestError("Every row with a quantity needs a product name.")
        if qty is None:
            raise RequestError(f"Enter a quantity for '{product}'.")
        if unit not in Unit.values:
            raise RequestError(f"Pick a unit for '{product}'.")
        rows.append((product[:200], qty, unit))

    if not rows:
        raise RequestError("Add at least one product to your list.")
    if len(rows) > MAX_ROWS:
        raise RequestError(f"A list can have at most {MAX_ROWS} products.")
    return rows


def list_version(req: QuoteRequest) -> str:
    """Changes whenever the customer edits their list; the owner's quote form carries it
    so a quote can never be saved against a list the customer has since changed."""
    return (req.edited_at or req.created_at).isoformat()


@transaction.atomic
def update_request(req: QuoteRequest, name, phone, products, qtys, units, address="") -> None:
    """The customer's changes to their own list, allowed only until the quote is sent."""
    locked = QuoteRequest.objects.select_for_update().get(pk=req.pk)
    if locked.status != QuoteRequest.Status.PENDING:
        raise RequestError("Your quote is ready, so this list can't be changed any more. Please contact the shop.")
    name = (name or "").strip()
    if not name:
        raise RequestError("Please enter your name.")
    phone = clean_phone(phone)
    rows = _clean_rows(products, qtys, units)

    locked.items.all().delete()
    QuoteRequestItem.objects.bulk_create(
        QuoteRequestItem(request=locked, serial=i, product_name=p, qty=q, unit=u)
        for i, (p, q, u) in enumerate(rows, start=1)
    )
    locked.customer_name, locked.customer_phone = name[:150], phone
    locked.customer_address = clean_address(address)
    locked.edited_at = timezone.now()
    locked.save(update_fields=["customer_name", "customer_phone", "customer_address", "edited_at"])


@transaction.atomic
def create_request(org, name, phone, products, qtys, units, address="") -> QuoteRequest:
    name = (name or "").strip()
    if not name:
        raise RequestError("Please enter your name.")
    phone = clean_phone(phone)

    rows = _clean_rows(products, qtys, units)

    # Many customers can scan the same QR at once. Locking the organization's row
    # (it always exists, unlike the first request) makes simultaneous submits take
    # the next number one after another, so no two requests ever share a number.
    Organization.objects.select_for_update().get(pk=org.pk)
    last = QuoteRequest.objects.filter(organization=org).aggregate(m=Max("number"))["m"] or 0
    req = QuoteRequest.objects.create(
        organization=org, number=last + 1, customer_name=name[:150], customer_phone=phone,
        customer_address=clean_address(address),
    )
    QuoteRequestItem.objects.bulk_create(
        QuoteRequestItem(request=req, serial=i, product_name=p, qty=q, unit=u)
        for i, (p, q, u) in enumerate(rows, start=1)
    )
    return req


@transaction.atomic
def save_quote(req: QuoteRequest, post, send: bool) -> None:
    """Applies the owner's per-row inputs; `send` also marks the request Quoted."""
    current = QuoteRequest.objects.select_for_update().get(pk=req.pk)
    version = post.get("version")
    if version is not None and version != list_version(current):
        raise RequestError("The customer just changed their list. Please review the updated list and try again.")
    old_total = req.total
    updates = []
    for item in req.items.all():
        available = post.get(f"available_{item.pk}") == "on"
        price = parse_price(post.get(f"price_{item.pk}"))
        if available and send and price is None:
            raise RequestError(
                f"Row {item.serial} ({item.product_name}): enter a price, or untick Available."
            )
        item.available = available
        item.price = price if available else None
        item.remark = (post.get(f"remark_{item.pk}") or "").strip()[:255]
        updates.append(item)
    QuoteRequestItem.objects.bulk_update(updates, ["available", "price", "remark"])

    if send:
        req.status = QuoteRequest.Status.QUOTED
        req.quoted_at = timezone.now()
        fields = ["status", "quoted_at"]
        # A changed amount after payment was recorded needs a fresh payment check.
        if req.payment_status == QuoteRequest.PaymentStatus.PAID and req.total != old_total:
            release_sale(req)
            req.payment_status = QuoteRequest.PaymentStatus.PENDING
            req.paid_at = None
            fields += ["payment_status", "paid_at", "payment_mode", "sales_entry_id"]
        req.save(update_fields=fields)


MAX_SUGGESTIONS = 2000


def sync_product_suggestions(org) -> None:
    """Copies the shop's inventory names into ProductSuggestion.

    Must run inside a signed-in owner's request: only then is the shop's finance
    data (our database or their Google Sheet) readable. Both product categories
    and their subcategories are offered, since shops list items at either level.
    """
    from apps.finance.models import Category, Subcategory

    categories = {c.id: c for c in Category.objects.all() if c.kind == Category.Kind.PRODUCT and c.is_active}
    names = {}
    for c in categories.values():
        names.setdefault(c.name.strip().lower(), c.name.strip())
    for sub in Subcategory.objects.all():
        if sub.is_active and sub.category_id in categories:
            names.setdefault(sub.name.strip().lower(), sub.name.strip())
    wanted = {n[:200] for n in names.values() if n}
    wanted = set(sorted(wanted)[:MAX_SUGGESTIONS])

    with transaction.atomic():
        existing = {s.name: s.pk for s in ProductSuggestion.objects.filter(organization=org)}
        stale = [pk for name, pk in existing.items() if name not in wanted]
        if stale:
            ProductSuggestion.objects.filter(pk__in=stale).delete()
        ProductSuggestion.objects.bulk_create(
            [ProductSuggestion(organization=org, name=n) for n in wanted - existing.keys()],
            ignore_conflicts=True,
        )


def quoted_amounts(org, day=None) -> dict:
    """Money across the shop's quoted requests (those received on `day`, if given): all of it,
    and the paid / unpaid split.

    Rows are rounded one by one (rate x quantity) exactly like QuoteRequestItem.line_total,
    so these figures always add up to what the individual request pages show.
    """
    rows = QuoteRequestItem.objects.filter(
        request__organization=org, request__status=QuoteRequest.Status.QUOTED,
        available=True, price__isnull=False,
    )
    if day is not None:
        rows = rows.filter(request__created_at__date=day)
    rows = rows.values_list("request__payment_status", "price", "qty")
    paid = unpaid = Decimal("0")
    for payment_status, price, qty in rows:
        line = (price * qty).quantize(Decimal("0.01"))
        if payment_status == QuoteRequest.PaymentStatus.PAID:
            paid += line
        else:
            unpaid += line
    return {"total": paid + unpaid, "paid": paid, "unpaid": unpaid}


SALES_CHANNEL_NAME = "Customer requests"


def _sales_channel(org):
    """The single revenue channel every paid request is booked under, created on first use."""
    from apps.finance.models import Category

    for category in Category.objects.all():
        if category.kind == Category.Kind.SALES and category.name == SALES_CHANNEL_NAME:
            return category
    return Category.objects.create(organization=org, kind=Category.Kind.SALES, name=SALES_CHANNEL_NAME)


def book_sale(req: QuoteRequest, mode: str, email: str) -> None:
    """Records the paid amount as one lump-sum sale in the Daily Book, dated the day it was paid.

    Every request goes under the one "Customer requests" revenue channel as a single
    lump-sum amount (not split per item or per product category).

    Only runs inside the signed-in owner's request (that is what makes the shop's
    finance data reachable, on our database or their Google Sheet). Safe to call twice:
    a request that already has its sale is left alone.
    """
    from apps.finance.models import PaymentMode, SalesEntry

    req.payment_mode = mode if mode in PaymentMode.values else PaymentMode.CASH
    if req.sales_entry_id is None and req.total > 0:
        entry = SalesEntry.objects.create(
            organization=req.organization,
            date=timezone.localdate(),
            amount=req.total,
            payment_mode=req.payment_mode,
            channel=_sales_channel(req.organization),
            note=f"Customer request #{req.number} · {req.customer_name}"[:255],
            created_by_email=email,
        )
        req.sales_entry_id = entry.pk


def release_sale(req: QuoteRequest) -> None:
    """Takes the request's sale back out of the Daily Book (payment reverted or amount changed)."""
    if req.sales_entry_id is not None:
        from apps.finance.models import SalesEntry

        entry = SalesEntry.objects.filter(pk=req.sales_entry_id).first()
        if entry is not None:
            entry.delete()
    req.sales_entry_id = None
    req.payment_mode = ""


def set_payment(req: QuoteRequest, *, paid: bool, mode: str, email: str) -> None:
    """Marks a quoted request Paid (booking the sale in the Daily Book) or back to Pending
    (removing it). Raises RequestError until the quote has been sent."""
    if req.status != QuoteRequest.Status.QUOTED:
        raise RequestError("Send the quote first, then record the payment.")
    if paid:
        book_sale(req, mode, email)
        req.payment_status = QuoteRequest.PaymentStatus.PAID
        req.paid_at = timezone.now()
    else:
        release_sale(req)
        req.payment_status = QuoteRequest.PaymentStatus.PENDING
        req.paid_at = None
    req.save(update_fields=["payment_status", "paid_at", "payment_mode", "sales_entry_id"])
