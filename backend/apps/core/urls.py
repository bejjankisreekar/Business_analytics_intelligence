from django.urls import path

from .views import LandingPageView, PrivacyPolicyView, TermsView

app_name = "core"

urlpatterns = [
    path("", LandingPageView.as_view(), name="landing"),
    path("privacy-policy/", PrivacyPolicyView.as_view(), name="privacy_policy"),
    path("terms/", TermsView.as_view(), name="terms"),
]
