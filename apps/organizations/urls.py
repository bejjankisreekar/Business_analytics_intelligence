from django.urls import path
from . import views

app_name = 'organizations'

urlpatterns = [
    # Client URLs
    path('storage/change/request/', views.request_storage_change, name='request_storage_change'),
    path('storage/change/requests/', views.storage_change_requests, name='storage_change_requests'),

    path('storage/change/requests/cancel/', views.cancel_storage_change, name='cancel_storage_change'),

]
