from django.urls import path

from . import views

app_name = "billing"

urlpatterns = [
    path("webhooks/<str:gateway>/", views.PaymentWebhookView.as_view(), name="payment_webhook"),
]
