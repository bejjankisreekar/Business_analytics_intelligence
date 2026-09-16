from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("apps.accounts.urls")),
    path("app/", include("apps.finance.urls")),
    path("billing/", include("apps.billing.urls")),
    path("superadmin/", include("apps.superadmin.urls")),
    path("", include("apps.core.urls")),
]
