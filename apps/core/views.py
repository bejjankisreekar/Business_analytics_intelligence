from django.views.generic import TemplateView

from apps.billing.models import Plan


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
        return context
