import re

from django import template

register = template.Library()


@register.filter
def initials(value, count=2):
    """'Nakshatra Mobiles' -> 'NM'; 'Apollo Hospitals' -> 'AH'; a single word
    ('Nakshatra') falls back to its first letters ('NA')."""
    try:
        count = int(count)
    except (TypeError, ValueError):
        count = 2
    words = re.findall(r"[A-Za-z0-9]+", str(value or ""))
    if len(words) >= count:
        return "".join(w[0] for w in words[:count]).upper()
    return "".join(words)[:count].upper()
