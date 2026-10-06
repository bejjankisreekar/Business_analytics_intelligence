from django.urls import path
from . import views

app_name = 'organizations'

urlpatterns = [
    # Client URLs
    path('storage/change/request/', views.request_storage_change, name='request_storage_change'),
    path('storage/change/requests/', views.storage_change_requests, name='storage_change_requests'),

    # Superadmin URLs
    path('superadmin/storage-change-requests/', views.superadmin_storage_change_requests, name='storage_change_requests_admin'),
    path('superadmin/storage-change-requests/<int:request_id>/', views.superadmin_storage_change_detail, name='storage_change_detail'),
]
