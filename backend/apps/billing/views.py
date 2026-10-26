import json
from decimal import Decimal

from django.http import HttpResponse, HttpResponseBadRequest
from django.utils.dateparse import parse_datetime
from django.utils.decorators import method_decorator
from django.utils import timezone
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from apps.organizations.models import Organization

from . import payments as payment_services
from . import razorpay_client
from . import services as billing_services
from .models import Invoice, Payment, PaymentWebhookEvent


@method_decorator(csrf_exempt, name="dispatch")
class PaymentWebhookView(View):
    """Inbound webhook receiver for a payment gateway.

    Always writes to the `default` database alias — in a real deployment
    each environment (dev/prod) is its own running server with its own
    webhook URL configured on the gateway side, so "default" is exactly
    that server's own live database, same as every other request in the
    app. There's no env-in-the-URL here on purpose.

    Razorpay is wired up: `_verify_signature` checks the
    `X-Razorpay-Signature` header via `razorpay_client`, and
    `_handle_razorpay` normalizes its nested payload shape into the
    generic fields `payments.process_webhook_event` expects. Any other
    gateway falls through to the original flat-payload path (a no-op
    signature check) — the shape a real Stripe integration would need to
    add its own normalizer for later, same as Razorpay's here.
    """

    def post(self, request, gateway):
        gateway = gateway.upper()
        if gateway not in Payment.Gateway.values:
            return HttpResponseBadRequest("Unknown gateway")

        try:
            payload = json.loads(request.body or b"{}")
        except json.JSONDecodeError:
            return HttpResponseBadRequest("Invalid JSON")

        if not self._verify_signature(gateway, request, payload):
            return HttpResponse(status=400)

        if gateway == Payment.Gateway.RAZORPAY:
            return self._handle_razorpay(request, payload)

        event_id = payload.get("event_id") or payload.get("id")
        if not event_id:
            return HttpResponseBadRequest("Missing event_id")

        organization = None
        org_code = payload.get("organization_code")
        if org_code:
            organization = Organization.objects.filter(organization_code=org_code).first()

        payment_date = parse_datetime(payload.get("payment_date") or "") or timezone.now()

        payment_services.process_webhook_event(
            gateway,
            str(event_id),
            payload,
            event_type=payload.get("event_type", ""),
            organization=organization,
            transaction_id=payload.get("transaction_id", ""),
            amount=payload.get("amount"),
            currency=payload.get("currency", "INR"),
            status=payload.get("status", Payment.Status.SUCCESS),
            payment_date=payment_date,
            failure_reason=payload.get("failure_reason", ""),
        )
        # Always 200 once the event is durably recorded — including replays
        # of an already-processed event — so the gateway stops retrying.
        return HttpResponse(status=200)

    def _handle_razorpay(self, request, payload):
        """Razorpay's webhook body is nested (`payload.payment.entity...`)
        and carries no top-level event id — Razorpay sends that in the
        `X-Razorpay-Event-Id` header instead. The invoice/org this payment
        belongs to comes back via the `notes` we set on the Order in
        `razorpay_client.create_order`, so no separate lookup is needed."""
        event_type = payload.get("event", "")

        if event_type == "subscription.charged":
            return self._handle_subscription_charged(request, payload)
        if event_type in ("subscription.cancelled", "subscription.halted", "subscription.completed"):
            return self._handle_subscription_ended(payload)

        event_id = request.headers.get("X-Razorpay-Event-Id") or ""
        entity = ((payload.get("payload") or {}).get("payment") or {}).get("entity") or {}
        if not entity:
            # An event shape this app doesn't act on (e.g. refund/order
            # events) — acknowledge so Razorpay stops retrying it.
            return HttpResponse(status=200)
        if not event_id:
            event_id = entity.get("id", "")
        if not event_id:
            return HttpResponseBadRequest("Missing event id")

        notes = entity.get("notes") or {}
        organization = None
        org_code = notes.get("organization_code")
        if org_code:
            organization = Organization.objects.filter(organization_code=org_code).first()

        invoice = None
        invoice_id = notes.get("invoice_id")
        if invoice_id:
            invoice = Invoice.objects.filter(pk=invoice_id).first()
            if invoice and organization is None:
                organization = invoice.organization

        status_map = {"captured": Payment.Status.SUCCESS, "failed": Payment.Status.FAILED}
        status = status_map.get(entity.get("status"), Payment.Status.PENDING)
        amount = Decimal(entity["amount"]) / 100 if entity.get("amount") is not None else None

        payment_services.process_webhook_event(
            Payment.Gateway.RAZORPAY,
            str(event_id),
            payload,
            event_type=payload.get("event", ""),
            organization=organization,
            invoice=invoice,
            transaction_id=entity.get("id", ""),
            amount=amount,
            currency=entity.get("currency", "INR"),
            status=status,
            payment_date=timezone.now(),
            failure_reason=entity.get("error_description") or "",
        )
        return HttpResponse(status=200)

    def _handle_subscription_charged(self, request, payload):
        """Razorpay auto-charged a recurring mandate for its next billing
        cycle. Event-level idempotency (PaymentWebhookEvent, keyed on this
        event's own id) guards against a retried delivery creating a
        second invoice/payment for the same cycle — record_subscription_
        charge() itself always creates a fresh Invoice, so it must only
        ever run once per event."""
        event_id = request.headers.get("X-Razorpay-Event-Id") or payload.get("payload", {}).get(
            "payment", {}
        ).get("entity", {}).get("id", "")
        if not event_id:
            return HttpResponseBadRequest("Missing event id")

        event, created = PaymentWebhookEvent.objects.get_or_create(
            gateway=Payment.Gateway.RAZORPAY, event_id=str(event_id),
            defaults={"event_type": "subscription.charged", "payload": payload},
        )
        if not created and event.status == PaymentWebhookEvent.Status.PROCESSED:
            return HttpResponse(status=200)

        sub_entity = ((payload.get("payload") or {}).get("subscription") or {}).get("entity") or {}
        payment_entity = ((payload.get("payload") or {}).get("payment") or {}).get("entity") or {}
        razorpay_subscription_id = sub_entity.get("id", "")
        amount = Decimal(payment_entity["amount"]) / 100 if payment_entity.get("amount") is not None else None

        if razorpay_subscription_id and amount is not None:
            billing_services.record_subscription_charge(
                razorpay_subscription_id=razorpay_subscription_id,
                amount=amount,
                currency=payment_entity.get("currency", "INR"),
                transaction_id=payment_entity.get("id", ""),
            )
            event.status = PaymentWebhookEvent.Status.PROCESSED
        else:
            event.status = PaymentWebhookEvent.Status.IGNORED
            event.error_message = "Missing subscription id or payment amount in webhook payload."
        event.processed_at = timezone.now()
        event.save(update_fields=["status", "error_message", "processed_at"])
        return HttpResponse(status=200)

    def _handle_subscription_ended(self, payload):
        """The mandate stopped being active on Razorpay's side — cancelled,
        halted (repeated charge failures), or ran out its (effectively
        infinite) cycle count. Just clears the local autopay flag; never
        touches access/dates, since the org already paid for whatever
        period they're currently in."""
        sub_entity = ((payload.get("payload") or {}).get("subscription") or {}).get("entity") or {}
        razorpay_subscription_id = sub_entity.get("id", "")
        if razorpay_subscription_id:
            billing_services.disable_autopay_flag(razorpay_subscription_id)
        return HttpResponse(status=200)

    def _verify_signature(self, gateway, request, payload) -> bool:
        if gateway == Payment.Gateway.RAZORPAY:
            signature = request.headers.get("X-Razorpay-Signature", "")
            return razorpay_client.verify_webhook_signature(raw_body=request.body, signature=signature)
        return True
