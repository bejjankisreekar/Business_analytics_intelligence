from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.views.decorators.http import require_http_methods
from django.utils import timezone
from django.db.models import Q

from .models import DataStorageChangeRequest, Organization
from .forms import RequestStorageChangeForm, ApproveStorageChangeForm


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
            storage_request.save()

            if storage_request.status == DataStorageChangeRequest.Status.APPROVED:
                messages.success(
                    request,
                    f"Request approved! The organization's storage will be changed shortly."
                )
            else:
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
