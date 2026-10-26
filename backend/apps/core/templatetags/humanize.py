"""Shadows Django's `humanize` template library so `intcomma` groups digits
the Indian way (12,34,567) instead of in thousands (1,234,567).

`apps.core` is listed after `django.contrib.humanize` in INSTALLED_APPS, so
this module wins for `{% load humanize %}` and every existing template picks
up the new grouping without any edits. Everything else in the library
(`naturaltime`, `ordinal`, ...) is re-exported unchanged.
"""
import re

from django import template
from django.contrib.humanize.templatetags.humanize import register  # noqa: F401 — re-exported library

_NUMBER_RE = re.compile(r"^(-?)(\d+)(\.\d+)?$")


def indian_grouping(value) -> str:
    text = str(value).strip()
    match = _NUMBER_RE.match(text)
    if not match:
        return str(value)
    sign, digits, fraction = match.group(1), match.group(2), match.group(3) or ""
    if len(digits) > 3:
        head, tail = digits[:-3], digits[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        digits = ",".join(groups + [tail])
    return f"{sign}{digits}{fraction}"


@register.filter(is_safe=True)
def intcomma(value, use_l10n=True):
    """1234567 -> '12,34,567'; 1234567.5 -> '12,34,567.5'."""
    return indian_grouping(value)
