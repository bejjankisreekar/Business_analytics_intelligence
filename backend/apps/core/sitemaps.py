from django.contrib.sitemaps import Sitemap
from django.urls import reverse


class StaticViewSitemap(Sitemap):
    """The handful of public, crawlable pages — everything else under
    /app, /accounts, /billing, /organizations and /superadmin requires
    login and is kept out of the sitemap (and disallowed in robots.txt)."""

    protocol = "https"

    def items(self):
        return [
            ("core:landing", "weekly", 1.0),
            ("core:privacy_policy", "yearly", 0.3),
            ("core:terms", "yearly", 0.3),
        ]

    def location(self, item):
        return reverse(item[0])

    def changefreq(self, item):
        return item[1]

    def priority(self, item):
        return item[2]
