from django.urls import path

from . import views

app_name = "superadmin"

urlpatterns = [
    path("", views.EnvironmentOverviewView.as_view(), name="overview"),
    path("<str:env>/", views.OrganizationListView.as_view(), name="org_list"),
    path("<str:env>/create/", views.OrganizationCreateView.as_view(), name="org_create"),
    path("<str:env>/plans/", views.PlanListView.as_view(), name="plan_list"),
    path("<str:env>/plans/create/", views.PlanCreateView.as_view(), name="plan_create"),
    path("<str:env>/plans/<int:pk>/edit/", views.PlanEditView.as_view(), name="plan_edit"),
    path("<str:env>/plans/<int:pk>/toggle/", views.PlanToggleActiveView.as_view(), name="plan_toggle"),
    path("<str:env>/coupons/", views.CouponListView.as_view(), name="coupon_list"),
    path("<str:env>/coupons/create/", views.CouponCreateView.as_view(), name="coupon_create"),
    path("<str:env>/coupons/<int:pk>/edit/", views.CouponEditView.as_view(), name="coupon_edit"),
    path("<str:env>/coupons/<int:pk>/toggle/", views.CouponToggleActiveView.as_view(), name="coupon_toggle"),
    path("<str:env>/payments/", views.PaymentListView.as_view(), name="payment_list"),
    path("<str:env>/payments/<int:pk>/edit/", views.PaymentEditView.as_view(), name="payment_edit"),
    path("<str:env>/invoices/", views.InvoiceListView.as_view(), name="invoice_list"),
    path("<str:env>/invoices/new/", views.InvoiceGenerateView.as_view(), name="invoice_new"),
    path("<str:env>/invoices/<int:pk>/", views.InvoiceDetailView.as_view(), name="invoice_detail"),
    path("<str:env>/invoices/<int:pk>/edit/", views.InvoiceEditView.as_view(), name="invoice_edit"),
    path("<str:env>/invoices/<int:pk>/payment/", views.InvoiceRecordPaymentView.as_view(), name="invoice_record_payment"),
    path("<str:env>/invoices/<int:pk>/coupon/", views.ApplyInvoiceCouponView.as_view(), name="invoice_apply_coupon"),
    path("<str:env>/invoices/<int:pk>/download/", views.InvoiceDownloadView.as_view(), name="invoice_download"),
    path("<str:env>/<uuid:pk>/", views.OrganizationDetailView.as_view(), name="org_detail"),
    path("<str:env>/<uuid:pk>/schema/", views.OrganizationSchemaRenameView.as_view(), name="org_schema_rename"),
    path("<str:env>/<uuid:pk>/schema/backup/", views.OrganizationSchemaBackupView.as_view(), name="org_schema_backup"),
    path("<str:env>/<uuid:pk>/edit/", views.OrganizationEditView.as_view(), name="org_edit"),
    path(
        "<str:env>/<uuid:pk>/team/<uuid:user_id>/edit/",
        views.ClientUserEditView.as_view(),
        name="edit_client_user",
    ),
    path(
        "<str:env>/<uuid:pk>/team/<uuid:user_id>/reset-password/",
        views.ClientPasswordResetView.as_view(),
        name="reset_client_password",
    ),
    path("<str:env>/<uuid:pk>/invoices/new/", views.InvoiceGenerateView.as_view(), name="client_invoice_new"),
    path("<str:env>/<uuid:pk>/subscription/", views.SubscriptionCreateView.as_view(), name="subscription_create"),
    path("<str:env>/<uuid:pk>/subscription/extend-trial/", views.ExtendTrialView.as_view(), name="extend_trial"),
    path("<str:env>/<uuid:pk>/dates/", views.EditKeyDatesView.as_view(), name="edit_key_dates"),
    path(
        "<str:env>/<uuid:pk>/subscription/complimentary/",
        views.GrantComplimentaryView.as_view(),
        name="grant_complimentary",
    ),
    path("<str:env>/<uuid:pk>/service/", views.ServiceControlView.as_view(), name="service_control"),
    path("<str:env>/<uuid:pk>/service/start/", views.StartServiceView.as_view(), name="service_start"),
    path("<str:env>/<uuid:pk>/service/stop/", views.StopServiceView.as_view(), name="service_stop"),
    path("<str:env>/<uuid:pk>/service/suspend/", views.SuspendServiceView.as_view(), name="service_suspend"),
    path("<str:env>/<uuid:pk>/service/resume/", views.ResumeServiceView.as_view(), name="service_resume"),
    path("<str:env>/<uuid:pk>/autopay/cancel/", views.CancelAutopayView.as_view(), name="cancel_autopay"),
    path(
        "<str:env>/<uuid:pk>/financial-history/",
        views.ClientFinancialHistoryView.as_view(),
        name="financial_history",
    ),
    path(
        "<str:env>/<uuid:pk>/payments/record/",
        views.RecordPaymentView.as_view(),
        name="record_payment",
    ),
    path(
        "<str:env>/<uuid:pk>/invoices/create/",
        views.CreateInvoiceView.as_view(),
        name="invoice_create",
    ),
]
