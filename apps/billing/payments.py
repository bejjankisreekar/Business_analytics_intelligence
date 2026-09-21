from django.db import transaction
from django.utils import timezone

from . import invoicing, services
from .models import Invoice, Payment, PaymentWebhookEvent


@transaction.atomic
def record_payment(
    organization,
    *,
    using: str = "default",
    subscription=None,
    invoice=None,
    amount,
    currency="INR",
    payment_method: str = Payment.Method.OTHER,
    gateway: str = Payment.Gateway.MANUAL,
    transaction_id: str = "",
    status: str = Payment.Status.SUCCESS,
    payment_date=None,
    failure_reason: str = "",
    raw_response=None,
):
    """Record a payment. If `transaction_id` is set, this is idempotent at
    the database level: calling it twice with the same (gateway,
    transaction_id) returns the SAME row rather than creating a duplicate
    (`created=False` on the second call) — this is what makes retried
    webhook deliveries safe.
    """
    payment_date = payment_date or timezone.now()
    defaults = dict(
        organization=organization,
        subscription=subscription,
        invoice=invoice,
        amount=amount,
        currency=currency,
        payment_method=payment_method,
        payment_date=payment_date,
        status=status,
        failure_reason=failure_reason,
        raw_gateway_response=raw_response or {},
    )

    if transaction_id:
        payment, created = Payment.objects.using(using).get_or_create(
            gateway=gateway, transaction_id=transaction_id, defaults=defaults
        )
    else:
        payment = Payment(gateway=gateway, transaction_id=transaction_id, **defaults)
        payment.save(using=using)
        created = True

    if created and invoice is not None and status == Payment.Status.SUCCESS:
        apply_payment_to_invoice(invoice, amount, using=using)

    return payment, created


def apply_payment_to_invoice(invoice, amount, *, using: str = "default"):
    invoice.amount_paid = (invoice.amount_paid or 0) + amount
    invoice = invoicing.refresh_invoice_status(invoice, using=using)
    if invoice.status == Invoice.Status.PAID:
        services.renew_subscription_from_invoice(invoice, using=using)
        _resume_if_payment_hold_cleared(invoice, using=using)
    return invoice


def _resume_if_payment_hold_cleared(invoice, *, using: str) -> None:
    """A client whose service was stopped/suspended for a pending payment gets it
    back as soon as their last open invoice is paid - no superadmin action needed."""
    from apps.organizations import service_control

    org = invoice.organization
    if not org.is_payment_hold:
        return
    still_open = Invoice.objects.using(using).filter(
        organization_id=org.id,
        status__in=[Invoice.Status.ISSUED, Invoice.Status.PARTIALLY_PAID, Invoice.Status.OVERDUE],
    ).exists()
    if not still_open:
        service_control.resume_service(
            org, using=using, admin_email="system (payment received)",
            notes=f"Service resumed automatically: invoice {invoice.invoice_number} was paid.",
        )


def refund_payment(payment: Payment, *, using: str = "default", amount=None, reason: str = "") -> Payment:
    amount = payment.amount if amount is None else amount
    payment.refunded_amount = (payment.refunded_amount or 0) + amount
    payment.refund_reason = reason
    payment.refunded_at = timezone.now()
    payment.status = (
        Payment.Status.REFUNDED if payment.refunded_amount >= payment.amount else Payment.Status.PARTIALLY_REFUNDED
    )
    payment.save(using=using)

    if payment.invoice_id:
        invoice = payment.invoice
        invoice.amount_paid = max((invoice.amount_paid or 0) - amount, 0)
        invoicing.refresh_invoice_status(invoice, using=using)
    return payment


@transaction.atomic
def process_webhook_event(
    gateway: str,
    event_id: str,
    payload: dict,
    *,
    using: str = "default",
    event_type: str = "",
    organization=None,
    subscription=None,
    invoice=None,
    amount=None,
    currency="INR",
    transaction_id: str = "",
    status: str = Payment.Status.SUCCESS,
    payment_date=None,
    failure_reason: str = "",
):
    """The single entrypoint a real gateway webhook (or a manual replay of
    one) calls. Idempotent at the EVENT level via (gateway, event_id): a
    duplicate delivery of the exact same event is recognized here and never
    reaches `record_payment` a second time, on top of `record_payment`'s own
    (gateway, transaction_id) idempotency. Returns (payment, processed) —
    `processed=False` means this event was a no-op replay.
    """
    event, created = PaymentWebhookEvent.objects.using(using).get_or_create(
        gateway=gateway,
        event_id=event_id,
        defaults={"event_type": event_type, "payload": payload},
    )
    if not created and event.status == PaymentWebhookEvent.Status.PROCESSED:
        return event.payment, False

    if organization is None:
        event.status = PaymentWebhookEvent.Status.FAILED
        event.error_message = "No organization resolved for this webhook event."
        event.save(using=using, update_fields=["status", "error_message"])
        return None, False

    payment, _payment_created = record_payment(
        organization,
        using=using,
        subscription=subscription,
        invoice=invoice,
        amount=amount,
        currency=currency,
        gateway=gateway,
        transaction_id=transaction_id,
        status=status,
        payment_date=payment_date,
        failure_reason=failure_reason,
        raw_response=payload,
    )
    event.payment = payment
    event.status = PaymentWebhookEvent.Status.PROCESSED
    event.processed_at = timezone.now()
    event.save(using=using, update_fields=["payment", "status", "processed_at"])
    return payment, True
