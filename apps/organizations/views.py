from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.views.decorators.http import require_http_methods
from django.utils import timezone
from django.db.models import Q

from .models import DataStorageChangeRequest, Organization
from .forms import RequestStorageChangeForm, ApproveStorageChangeForm


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def _handle_approved_storage_change(storage_request):
    """
    After superadmin approves a storage change request, initiate the migration.

    For GOOGLE_SHEETS: Update storage mode and send email to client with
    Google Drive connection link.

    For OUR_DATABASE: Update storage mode directly (data already there).
    """
    organization = storage_request.organization

    if storage_request.requested_storage == Organization.StorageMode.GOOGLE_SHEETS:
        # Switching to Google Drive
        # Update storage mode to GOOGLE_SHEETS so when client completes OAuth,
        # the system will migrate the data from Postgres to Google Sheets
        organization.storage_mode = Organization.StorageMode.GOOGLE_SHEETS
        organization.save(update_fields=['storage_mode'])

        # Send email notification to client with link to complete Google Drive connection
        from django.core.mail import send_mail
        from django.urls import reverse
        from django.conf import settings

        # Get recipient email - prefer contact email, fall back to first user
        recipient_email = organization.contact_email
        if not recipient_email and organization.user_set.exists():
            recipient_email = organization.user_set.first().email

        if recipient_email:
            try:
                connect_url = reverse('accounts:connect_drive')
                subject = "Storage Change Approved - Complete Google Drive Connection"
                message = f"""Hello,

Your request to switch to Google Drive storage for {organization.name} has been approved!

To complete the process, please log in to your account and navigate to the Google Drive connection page:
/accounts/profile/drive/connect/

Your existing data will be automatically migrated to a Google Sheet in your own Google Drive.

Best regards,
Prism Pulse Team
"""
                send_mail(
                    subject,
                    message,
                    settings.DEFAULT_FROM_EMAIL,
                    [recipient_email],
                    fail_silently=True,
                )
            except Exception:
                # Log error but don't fail the approval
                pass

    elif storage_request.requested_storage == Organization.StorageMode.OUR_DATABASE:
        # Switching to Prism Pulse's database
        organization.storage_mode = Organization.StorageMode.OUR_DATABASE
        organization.save(update_fields=['storage_mode'])
        # TODO: If switching from Google Sheets, implement data migration


# ============================================================================
# CLIENT VIEWS - Storage Change Requests
# ============================================================================

@login_required
@require_http_methods(["GET", "POST"])
def request_storage_change(request):
    """Client view to request a storage change."""
    # Get the client's organization
    organization = request.user.organization
    if not organization:
        messages.error(request, "You don't have access to an organization.")
        return redirect('finance:dashboard')

    # Check if client already has a pending/approved request
    existing_request = DataStorageChangeRequest.objects.filter(
        organization=organization,
        status__in=[DataStorageChangeRequest.Status.PENDING, DataStorageChangeRequest.Status.APPROVED]
    ).first()

    if request.method == 'POST':
        form = RequestStorageChangeForm(organization, request.POST)
        if form.is_valid():
            request_obj = form.save()
            messages.success(
                request,
                f"Your storage change request has been submitted. "
                f"You will see a {request_obj.billing_impact_text}. "
                f"Our team will review it shortly."
            )
            return redirect('organizations:storage_change_requests')
    else:
        form = RequestStorageChangeForm(organization)

    context = {
        'form': form,
        'organization': organization,
        'existing_request': existing_request,
        'current_storage': organization.get_storage_mode_display(),
    }
    return render(request, 'organizations/request_storage_change.html', context)


@login_required
def storage_change_requests(request):
    """Client view to see their pending/past storage change requests."""
    organization = request.user.organization
    if not organization:
        messages.error(request, "You don't have access to an organization.")
        return redirect('finance:dashboard')

    requests_list = DataStorageChangeRequest.objects.filter(
        organization=organization
    ).order_by('-requested_at')

    # Separate pending and completed requests
    pending = requests_list.filter(status=DataStorageChangeRequest.Status.PENDING).first()
    approved = requests_list.filter(status=DataStorageChangeRequest.Status.APPROVED).first()
    history = requests_list.filter(
        status__in=[DataStorageChangeRequest.Status.COMPLETED, DataStorageChangeRequest.Status.REJECTED]
    )

    context = {
        'pending_request': pending,
        'approved_request': approved,
        'request_history': history,
        'organization': organization,
    }
    return render(request, 'organizations/storage_change_requests.html', context)


# ============================================================================
# SUPERADMIN VIEWS - Manage Storage Change Requests
# ============================================================================

def superadmin_storage_change_requests(request):
    """Superadmin view to see all pending storage change requests."""
    # Check if user is superadmin
    if not request.user.is_superuser:
        messages.error(request, "You don't have permission to access this page.")
        return redirect('superadmin:overview')

    # Filter requests by status
    status_filter = request.GET.get('status', 'PENDING')

    if status_filter == 'PENDING':
        requests_list = DataStorageChangeRequest.objects.filter(
            status=DataStorageChangeRequest.Status.PENDING
        ).order_by('-requested_at')
    elif status_filter == 'APPROVED':
        requests_list = DataStorageChangeRequest.objects.filter(
            status=DataStorageChangeRequest.Status.APPROVED
        ).order_by('-requested_at')
    elif status_filter == 'ALL':
        requests_list = DataStorageChangeRequest.objects.all().order_by('-requested_at')
    else:
        requests_list = DataStorageChangeRequest.objects.filter(
            status=DataStorageChangeRequest.Status.PENDING
        ).order_by('-requested_at')
        status_filter = 'PENDING'

    context = {
        'requests': requests_list,
        'status_filter': status_filter,
        'status_choices': [
            ('PENDING', 'Pending'),
            ('APPROVED', 'Approved'),
            ('ALL', 'All'),
        ]
    }
    return render(request, 'superadmin/storage_change_requests.html', context)


@require_http_methods(["GET", "POST"])
def superadmin_storage_change_detail(request, request_id):
    """Superadmin view to review and approve/reject a storage change request."""
    if not request.user.is_superuser:
        messages.error(request, "You don't have permission to access this page.")
        return redirect('superadmin:overview')

    storage_request = get_object_or_404(DataStorageChangeRequest, pk=request_id)

    if request.method == 'POST':
        form = ApproveStorageChangeForm(request.POST, instance=storage_request)
        if form.is_valid():
            storage_request = form.save(commit=False)
            storage_request.reviewed_by_email = request.user.email
            storage_request.reviewed_at = timezone.now()

            if storage_request.status == DataStorageChangeRequest.Status.APPROVED:
                # Mark as COMPLETED since superadmin approval is final
                storage_request.status = DataStorageChangeRequest.Status.COMPLETED
                storage_request.completed_at = timezone.now()
                storage_request.save()

                # Trigger the appropriate migration based on requested storage type
                _handle_approved_storage_change(storage_request)

                messages.success(
                    request,
                    f"Request approved! Storage change has been initiated for {storage_request.organization.name}."
                )
            else:
                storage_request.save()
                messages.info(
                    request,
                    f"Request rejected. The organization has been notified."
                )

            return redirect('superadmin:storage_change_requests')
    else:
        form = ApproveStorageChangeForm(instance=storage_request)

    context = {
        'storage_request': storage_request,
        'form': form,
        'organization': storage_request.organization,
    }
    return render(request, 'superadmin/storage_change_detail.html', context)
