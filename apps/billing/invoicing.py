import datetime

from django.utils import timezone

from .models import Invoice


def create_invoice(
    organization,
    *,
    using: str = "default",
    subscription=None,
    subtotal,
    discount=0,
    tax=0,
    currency="INR",
    invoice_date=None,
    due_date=None,
    status: str = Invoice.Status.ISSUED,
) -> Invoice:
    invoice_date = invoice_date or timezone.localdate()
    due_date = due_date or (invoice_date + datetime.timedelta(days=7))
    invoice = Invoice(
        organization=organization,
        subscription=subscription,
        invoice_date=invoice_date,
        due_date=due_date,
        currency=currency,
        subtotal=subtotal,
        discount=discount,
        tax=tax,
        status=status,
    )
    invoice.save(using=using)
    return invoice


def refresh_invoice_status(invoice: Invoice, *, using: str = "default") -> Invoice:
    """Recompute an invoice's status from its amounts/due date. Called after
    any payment or refund is applied. Never overrides a terminal CANCELLED
    status, and leaves a DRAFT invoice alone until it's explicitly issued.
    """
    if invoice.status == Invoice.Status.CANCELLED:
        return invoice
    if invoice.status == Invoice.Status.DRAFT:
        invoice.save(using=using)
        return invoice

    # amount_due is only recomputed inside Invoice.save() (from total and
    # amount_paid), which hasn't run yet at this point — use the live
    # values directly rather than the stale `invoice.amount_due`.
    total = (invoice.subtotal or 0) - (invoice.discount or 0) + (invoice.tax or 0)
    amount_due = total - (invoice.amount_paid or 0)

    if amount_due <= 0:
        invoice.status = Invoice.Status.PAID
    elif invoice.amount_paid > 0:
        invoice.status = Invoice.Status.PARTIALLY_PAID
    elif invoice.due_date < timezone.localdate():
        invoice.status = Invoice.Status.OVERDUE
    else:
        invoice.status = Invoice.Status.ISSUED
    invoice.save(using=using)
    return invoice


def cancel_invoice(invoice: Invoice, *, using: str = "default") -> Invoice:
    invoice.status = Invoice.Status.CANCELLED
    invoice.save(using=using)
    return invoice
