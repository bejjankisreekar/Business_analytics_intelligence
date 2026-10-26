"""Thin wrapper around the Razorpay Orders API and its signature
verification helpers — the only place the razorpay SDK is touched, so a
future second gateway (Stripe) doesn't have to route through this shape."""
import hashlib
import hmac

import razorpay
from django.conf import settings


def is_configured() -> bool:
    return bool(settings.RAZORPAY_KEY_ID and settings.RAZORPAY_KEY_SECRET)


def _client() -> razorpay.Client:
    return razorpay.Client(auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET))


def create_order(invoice) -> dict:
    """A Razorpay Order for this invoice's outstanding amount (in paise),
    tagged with the invoice/org in `notes` so the webhook can find its way
    back to the right Invoice/Organization without any other lookup."""
    amount_paise = int(round(invoice.amount_due * 100))
    return _client().order.create({
        "amount": amount_paise,
        "currency": invoice.currency,
        "receipt": invoice.invoice_number,
        "notes": {
            "invoice_id": str(invoice.pk),
            "organization_code": invoice.organization.organization_code,
        },
    })


def verify_payment_signature(*, order_id: str, payment_id: str, signature: str) -> bool:
    """Confirms the Checkout popup's success callback actually came from
    Razorpay for this exact order — the client-side handler alone can't be
    trusted, since it runs in the payer's browser."""
    try:
        _client().utility.verify_payment_signature({
            "razorpay_order_id": order_id,
            "razorpay_payment_id": payment_id,
            "razorpay_signature": signature,
        })
        return True
    except razorpay.errors.SignatureVerificationError:
        return False


def create_plan(*, name: str, amount, currency: str, period: str, interval: int = 1) -> dict:
    """A Razorpay Plan — the price+cadence template a Subscription is
    created against. `period` is Razorpay's own vocabulary ("monthly" /
    "yearly"). Razorpay has no upsert, so the caller (services.
    get_or_create_razorpay_plan) is what makes this idempotent by caching
    the returned id on our own Plan row."""
    amount_paise = int(round(amount * 100))
    return _client().plan.create({
        "period": period,
        "interval": interval,
        "item": {"name": name, "amount": amount_paise, "currency": currency},
    })


def create_subscription(*, razorpay_plan_id: str, total_count: int, notes: dict) -> dict:
    """A Razorpay Subscription against an existing Plan — this is what
    Checkout (in subscription mode) authorizes a recurring mandate for.
    `total_count` is Razorpay's required cap on billing cycles; a large
    number (see services.AUTOPAY_TOTAL_CYCLES) stands in for "until
    cancelled", since the API has no literal infinite option."""
    return _client().subscription.create({
        "plan_id": razorpay_plan_id,
        "total_count": total_count,
        "customer_notify": 1,
        "notes": notes,
    })


def cancel_subscription(razorpay_subscription_id: str, *, cancel_at_cycle_end: bool = False) -> dict:
    return _client().subscription.cancel(razorpay_subscription_id, {"cancel_at_cycle_end": int(cancel_at_cycle_end)})


def verify_subscription_signature(*, subscription_id: str, payment_id: str, signature: str) -> bool:
    """Confirms a subscription-mode Checkout's success callback — same
    idea as verify_payment_signature, different message format
    (payment_id|subscription_id instead of order_id|payment_id)."""
    try:
        _client().utility.verify_subscription_payment_signature({
            "razorpay_subscription_id": subscription_id,
            "razorpay_payment_id": payment_id,
            "razorpay_signature": signature,
        })
        return True
    except razorpay.errors.SignatureVerificationError:
        return False


def verify_webhook_signature(*, raw_body: bytes, signature: str) -> bool:
    if not settings.RAZORPAY_WEBHOOK_SECRET or not signature:
        return False
    expected = hmac.new(
        settings.RAZORPAY_WEBHOOK_SECRET.encode("utf-8"), raw_body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)
