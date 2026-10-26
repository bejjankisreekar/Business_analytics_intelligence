from django.urls import path

from .views import (
    ChangePasswordView,
    ConnectDatabaseGateView,
    ConnectGoogleDriveView,
    CreateManagerAccountView,
    ManagerPageAccessView,
    DisconnectGoogleDriveView,
    EditManagerView,
    ExportFinanceDataExcelView,
    ForgotPasswordView,
    GoogleDriveOAuthCallbackView,
    LoginView,
    LogoutView,
    MobileAppDownloadView,
    SuspendedView,
    ProfileView,
    ResetManagerPasswordView,
    ResetPasswordView,
    SignupView,
    SwitchToOurDatabaseView,
)

app_name = "accounts"

urlpatterns = [
    path("login/", LoginView.as_view(), name="login"),
    path("signup/", SignupView.as_view(), name="signup"),
    path("logout/", LogoutView.as_view(), name="logout"),
    path("suspended/", SuspendedView.as_view(), name="suspended"),
    path("connect-database/", ConnectDatabaseGateView.as_view(), name="connect_database_gate"),
    path("profile/", ProfileView.as_view(), name="profile"),
    path("profile/export-excel/", ExportFinanceDataExcelView.as_view(), name="export_database_excel"),
    path("profile/drive/connect/", ConnectGoogleDriveView.as_view(), name="connect_drive"),
    path("profile/drive/callback/", GoogleDriveOAuthCallbackView.as_view(), name="drive_oauth_callback"),
    path("profile/drive/disconnect/", DisconnectGoogleDriveView.as_view(), name="disconnect_drive"),
    path("profile/switch-to-our-database/", SwitchToOurDatabaseView.as_view(), name="switch_to_our_database"),
    path("mobile-app/download/", MobileAppDownloadView.as_view(), name="mobile_app_download"),
    path("change-password/", ChangePasswordView.as_view(), name="change_password"),
    path("forgot-password/", ForgotPasswordView.as_view(), name="forgot_password"),
    path("reset-password/", ResetPasswordView.as_view(), name="reset_password"),
    path("manager/reset-password/", ResetManagerPasswordView.as_view(), name="reset_manager_password"),
    path("manager/create/", CreateManagerAccountView.as_view(), name="create_manager"),
    path("manager/edit/", EditManagerView.as_view(), name="edit_manager"),
    path("manager/pages/", ManagerPageAccessView.as_view(), name="manager_pages"),
]
