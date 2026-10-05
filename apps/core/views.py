import logging

from django.conf import settings
from django.contrib import messages
from django.core.mail import EmailMultiAlternatives
from django.shortcuts import redirect
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.views.generic import TemplateView

from apps.billing.models import Plan

from .forms import ContactForm

logger = logging.getLogger(__name__)


class LandingPageView(TemplateView):
    template_name = "marketing/landing.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Public pricing always reflects the real production catalog, even
        # when this server is running locally against the dev database —
        # visitors should never see test/dev plan data.
        context["plans"] = (
            Plan.objects.using("prod").filter(is_active=True, show_on_landing_page=True).order_by("monthly_price")
        )
        context.setdefault("contact_form", ContactForm())
        return context

    def post(self, request, *args, **kwargs):
        form = ContactForm(request.POST)
        if not form.is_valid():
            messages.error(request, "Please fix the errors below and try again.")
            return self.render_to_response(self.get_context_data(contact_form=form))

        contact = form.save()
        if settings.CONTACT_EMAIL:
            try:
                text_body = (
                    f"New contact form submission — {contact.organization_name}\n\n"
                    f"Name: {contact.name}\n"
                    f"Organization: {contact.organization_name}\n"
                    f"Phone: {contact.phone}\n\n"
                    f"Message:\n{contact.message}"
                )
                html_body = render_to_string("emails/contact_submission.html", {
                    "site_name": settings.SITE_NAME,
                    "contact": contact,
                    "received_at": timezone.localtime(contact.created_at).strftime("%d %b %Y, %I:%M %p"),
                })
                email_message = EmailMultiAlternatives(
                    subject=f"New contact form submission — {contact.organization_name}",
                    body=text_body,
                    from_email=settings.DEFAULT_FROM_EMAIL,
                    to=[settings.CONTACT_EMAIL],
                )
                email_message.attach_alternative(html_body, "text/html")
                email_message.send()
            except Exception:
                logger.exception("Failed to send contact form notification email for submission %s", contact.pk)
        else:
            logger.warning("CONTACT_EMAIL is not set — skipped notification for submission %s", contact.pk)

        messages.success(request, "Thanks for reaching out — we'll get back to you shortly.")
        return redirect(reverse("core:landing") + "#contact")


class PrivacyPolicyView(TemplateView):
    template_name = "marketing/privacy_policy.html"


class TermsView(TemplateView):
    template_name = "marketing/terms.html"
