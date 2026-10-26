import os

from django import template
from django.conf import settings
from django.templatetags.static import static

register = template.Library()


@register.simple_tag
def static_v(path):
    """Like {% static %}, but appends the file's mtime as a cache-busting
    query string so edits to it (tailwind.css after a rebuild, etc.) show up
    on the next page load instead of being served from the browser's cache
    under the same unchanging URL."""
    url = static(path)
    for static_dir in settings.STATICFILES_DIRS:
        candidate = os.path.join(static_dir, path)
        if os.path.exists(candidate):
            return f"{url}?v={int(os.path.getmtime(candidate))}"
    return url
