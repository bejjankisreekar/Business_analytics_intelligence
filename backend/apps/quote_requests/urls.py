from django.urls import path

from . import views

app_name = "quote_requests"

urlpatterns = [
    # Owner side (login required)
    path("customer-requests/", views.InboxView.as_view(), name="inbox"),
    path("customer-requests/toggle/", views.ToggleView.as_view(), name="toggle"),
    path("customer-requests/notifications/", views.NotifySettingsView.as_view(), name="notify_settings"),
    path("customer-requests/qr/", views.QrPageView.as_view(), name="qr"),
    path("customer-requests/qr.svg", views.QrDownloadView.as_view(), name="qr_download"),
    path("customer-requests/<int:number>/", views.DetailView.as_view(), name="detail"),
    path("customer-requests/<int:number>/pdf/", views.OwnerQuotePdfView.as_view(), name="owner_pdf"),
    path("customer-requests/<int:number>/payment/", views.PaymentView.as_view(), name="payment"),
    # Open side (no login)
    path("request/<slug:slug>/", views.PublicSubmitView.as_view(), name="public_submit"),
    path("r/<str:token>/", views.PublicTrackView.as_view(), name="public_track"),
    path("s/<str:token>/", views.PublicShareView.as_view(), name="public_share"),
    path("r/<str:token>/edit/", views.PublicEditView.as_view(), name="public_edit"),
    path("r/<str:token>/quote.pdf", views.PublicQuotePdfView.as_view(), name="public_pdf"),
]
