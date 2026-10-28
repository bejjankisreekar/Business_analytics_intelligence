from django import template

from apps.quote_requests.access import user_has_access

register = template.Library()


@register.simple_tag(takes_context=True)
def quote_requests_pending(context):
    """For the menu badge: requests still waiting for the owner's quote (0 when not allowed)."""
    from apps.quote_requests.models import QuoteRequest

    user = getattr(context.get("request"), "user", None)
    if not user_has_access(user):
        return 0
    return QuoteRequest.objects.filter(organization_id=user.organization_id, status=QuoteRequest.Status.PENDING).count()


@register.simple_tag(takes_context=True)
def quote_requests_enabled(context):
    """For the menu: True only for eligible (shop/restaurant) orgs."""
    request = context.get("request")
    return user_has_access(getattr(request, "user", None))
