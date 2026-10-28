from django.contrib import admin
from django.contrib.sitemaps.views import sitemap
from django.urls import include, path

from apps.core.sitemaps import StaticViewSitemap
from apps.core.views import robots_txt

sitemaps = {"static": StaticViewSitemap}

urlpatterns = [
    path("admin/", admin.site.urls),
    path("sitemap.xml", sitemap, {"sitemaps": sitemaps}, name="sitemap"),
    path("robots.txt", robots_txt, name="robots_txt"),
    path("api/mobile/", include("apps.mobile_api.urls")),
    path("api/mobile/", include("apps.quote_requests.mobile_urls")),
    path("accounts/", include("apps.accounts.urls")),
    path("app/", include("apps.finance.urls")),
    path("billing/", include("apps.billing.urls")),
    path("superadmin/", include("apps.superadmin.urls")),
    path("organizations/", include("apps.organizations.urls")),
    path("", include("apps.quote_requests.urls")),
    path("", include("apps.core.urls")),
]
