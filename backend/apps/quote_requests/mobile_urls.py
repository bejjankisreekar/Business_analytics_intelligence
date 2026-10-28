from django.urls import path

from . import mobile

# Included under /api/mobile/ (see config/urls.py), next to the rest of the app's API.
urlpatterns = [
    path("customer-requests/", mobile.list_requests, name="mobile_requests"),
    path("customer-requests/<int:number>/", mobile.request_detail, name="mobile_request_detail"),
    path("customer-requests/<int:number>/quote/", mobile.quote, name="mobile_request_quote"),
    path("customer-requests/<int:number>/payment/", mobile.payment, name="mobile_request_payment"),
    path("devices/", mobile.devices, name="mobile_devices"),
]
