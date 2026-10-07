from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.views.decorators.http import require_http_methods
from django.utils import timezone
from django.db.models import Q

from .models import DataStorageChangeRequest, Organization
from .forms import RequestStorageChangeForm, compute_plan_impact, paired_plan_name


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def _switch_subscription_plan(storage_request):
    """Move the org's current subscription to the paired plan (e.g. Professional
    -> Professional Drive) and re-price it to that plan's price and discount,
    the same figures shown to the client when they raised the request."""
    organization = storage_request.organization
    db = storage_request._state.db
    impact = compute_plan_impact(organization, storage_request.requested_storage, using=db)
    if not impact:
        return
    subscription = organization.subscriptions.filter(is_current=True).first()
    if subscription.plan_id == impact['plan'].pk and subscription.price == impact['new_price']:
        return
    subscription.plan = impact['plan']
    subscription.price = impact['new_price']
    subscription.discount = impact['new_discount']
    subscription.save(update_fields=['plan', 'price', 'discount', 'final_amount'])


def _handle_approved_storage_change(storage_request):
    """
    After superadmin approves a storage change request, initiate the migration.

    For GOOGLE_SHEETS: email the client the Google Drive connection link; the
    switch itself completes when they connect (see GoogleDriveOAuthCallbackView).

    For OUR_DATABASE: Update storage mode directly (data already there).
    """
    organization = storage_request.organization
    _switch_subscription_plan(storage_request)

    if storage_request.requested_storage == Organization.StorageMode.GOOGLE_SHEETS:
        # Switching to Google Drive: the org stays on our database until the
        # client connects Drive (the middleware sends them to the connect page
        # on next login). The OAuth callback then migrates their data and flips
        # storage_mode, so nothing is left pointing at an empty Sheet.

        # Send email notification to client with link to complete Google Drive connection
        from django.core.mail import send_mail
        from django.urls import reverse
        from django.conf import settings

        # Get recipient email - prefer contact email, fall back to first user
        recipient_email = organization.contact_email
        if not recipient_email and organization.users.exists():
            recipient_email = organization.users.first().email

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

    target = (
        Organization.StorageMode.OUR_DATABASE
        if organization.storage_mode == Organization.StorageMode.GOOGLE_SHEETS
        else Organization.StorageMode.GOOGLE_SHEETS
    )
    impact = compute_plan_impact(organization, target)
    plan_preview = None
    if impact and impact['requested_plan'] != impact['current_plan']:
        plan_preview = (impact['current_plan'], impact['requested_plan'])

    context = {
        'form': form,
        'organization': organization,
        'existing_request': existing_request,
        'plan_preview': plan_preview,
        'impact': impact,
        'current_storage': organization.get_storage_mode_display(),
    }
    return render(request, 'organizations/request_storage_change.html', context)


@login_required
@require_http_methods(["POST"])
def cancel_storage_change(request):
    """Client view to cancel their own pending storage change request."""
    organization = request.user.organization
    if not organization:
        messages.error(request, "You don't have access to an organization.")
        return redirect('finance:dashboard')

    updated = DataStorageChangeRequest.objects.filter(
        organization=organization,
        status=DataStorageChangeRequest.Status.PENDING,
    ).update(status=DataStorageChangeRequest.Status.CANCELLED)
    if updated:
        messages.success(request, "Your storage change request has been cancelled.")
    else:
        messages.info(request, "There is no pending request to cancel.")
    return redirect('organizations:storage_change_requests')


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
        status__in=[DataStorageChangeRequest.Status.COMPLETED, DataStorageChangeRequest.Status.REJECTED, DataStorageChangeRequest.Status.CANCELLED]
    )

    context = {
        'pending_request': pending,
        'approved_request': approved,
        'request_history': history,
        'organization': organization,
    }
    return render(request, 'organizations/storage_change_requests.html', context)
