# Storage Change Request Feature Implementation Guide

## Overview
This feature allows clients to request changing their data storage from Google Drive to Finday's database (or vice versa). Superadmins must approve requests, and clients can see billing impact before submitting.

## What's Been Created

### 1. **Database Model** (`apps/organizations/models.py`)
- **DataStorageChangeRequest** - Tracks all storage change requests with:
  - Organization FK
  - Current and requested storage types
  - Billing impact calculation
  - Request status (PENDING, APPROVED, REJECTED, COMPLETED)
  - Admin notes and approval tracking
  - Timestamps for request, review, and completion

### 2. **Forms** (`apps/organizations/forms.py`)
- **RequestStorageChangeForm** - Client form to request storage change
  - Validates only one pending/approved request per org
  - Calculates billing impact
  - Requires reason (optional)
  
- **ApproveStorageChangeForm** - Admin form to approve/reject
  - Simple approve/reject radio buttons
  - Admin notes field

### 3. **Views** (`apps/organizations/views.py`)
- **Client Views:**
  - `request_storage_change` - Form to submit new request
  - `storage_change_requests` - View pending and past requests
  
- **Superadmin Views:**
  - `superadmin_storage_change_requests` - Dashboard of all requests
  - `superadmin_storage_change_detail` - Review and approve/reject

### 4. **URL Routing** (`apps/organizations/urls.py`)
```
/organizations/storage/change/request/           - Client request form
/organizations/storage/change/requests/          - Client's requests
/organizations/superadmin/storage-change-requests/    - Admin dashboard
/organizations/superadmin/storage-change-requests/<id>/ - Admin review
```

---

## Still Need to Do

### 1. **Add to Main URLs** (`config/urls.py`)
Add this line to your main urls.py:
```python
path('organizations/', include('apps.organizations.urls')),
```

### 2. **Create Templates**
You need to create 4 HTML templates:

#### a) `templates/organizations/request_storage_change.html`
Shows the form to request storage change with billing impact preview.

#### b) `templates/organizations/storage_change_requests.html`
Shows pending request, approved request, and request history.

#### c) `templates/superadmin/storage_change_requests.html`
Dashboard showing all pending/approved/completed requests.

#### d) `templates/superadmin/storage_change_detail.html`
Detailed view for admin to review and approve/reject with notes.

### 3. **Create Migration**
```bash
python manage.py makemigrations organizations
python manage.py migrate
```

### 4. **Admin Integration** (Optional but Recommended)
Add to `apps/organizations/admin.py`:
```python
from django.contrib import admin
from .models import DataStorageChangeRequest

@admin.register(DataStorageChangeRequest)
class DataStorageChangeRequestAdmin(admin.ModelAdmin):
    list_display = ('organization', 'current_storage', 'requested_storage', 'status', 'requested_at')
    list_filter = ('status', 'requested_at')
    search_fields = ('organization__name',)
    readonly_fields = ('requested_at', 'reviewed_at', 'completed_at')
```

### 5. **Approval/Completion Logic**
Add a service function to actually change storage when approved:

```python
# apps/organizations/services.py
from django.utils import timezone
from .models import DataStorageChangeRequest

def complete_storage_change_request(request_obj):
    """Actually perform the storage migration after approval."""
    organization = request_obj.organization
    
    try:
        # Update organization's storage mode
        organization.storage_mode = request_obj.requested_storage
        organization.save()
        
        # Mark request as completed
        request_obj.status = DataStorageChangeRequest.Status.COMPLETED
        request_obj.completed_at = timezone.now()
        request_obj.save()
        
        # TODO: Implement actual data migration logic here
        # This would involve moving data from Google Sheets to DB or vice versa
        
        return True
    except Exception as e:
        print(f"Error completing storage change: {e}")
        return False
```

### 6. **Hook into Billing** (Important!)
Update billing calculation to account for storage changes:

```python
# In RequestStorageChangeForm.save():
# Currently assumes both storage types cost the same
# Update this if you have different pricing:

from apps.billing.models import Subscription

def calculate_storage_impact(organization, new_storage_type):
    """Calculate billing impact of storage change."""
    subscription = organization.subscriptions.filter(is_current=True).first()
    if not subscription:
        return Decimal('0')
    
    # Define storage pricing (update based on your plans)
    storage_pricing = {
        Organization.StorageMode.GOOGLE_SHEETS: Decimal('0'),    # Free
        Organization.StorageMode.OUR_DATABASE: Decimal('500'),   # ₹500/month
    }
    
    current_cost = storage_pricing.get(organization.storage_mode, Decimal('0'))
    new_cost = storage_pricing.get(new_storage_type, Decimal('0'))
    
    return new_cost - current_cost
```

---

## Workflow

### Client Flow:
1. Client navigates to `/organizations/storage/change/request/`
2. Sees current storage and billing details
3. Selects new storage type
4. Views billing impact (increase/decrease/no change)
5. Submits request with optional reason
6. Sees "Pending Approval" in `/organizations/storage/change/requests/`
7. Receives notification when approved/rejected

### Admin Flow:
1. Navigate to `/organizations/superadmin/storage-change-requests/`
2. See all pending requests
3. Click on request to review
4. Review client details, reason, and billing impact
5. Add notes (optional)
6. Click "Approve" or "Reject"
7. Request status updates and client is notified

### Approval → Completion:
1. When approved, request enters APPROVED status
2. Admin/system calls `complete_storage_change_request()`
3. Organization's `storage_mode` is updated
4. Data migration happens (implement custom logic)
5. Request moves to COMPLETED
6. Client's next login uses new storage

---

## Key Features Implemented

✅ One active request per organization (UNIQUE constraint)
✅ Billing impact calculation
✅ Admin approval workflow
✅ Request history tracking
✅ Human-readable status display
✅ Audit trail (who, when, notes)
✅ Prevents concurrent requests

---

## Next Steps

1. Run migrations: `python manage.py makemigrations && python manage.py migrate`
2. Create the 4 HTML templates
3. Add to config/urls.py
4. (Optional) Add to Django admin
5. Implement `complete_storage_change_request()` service
6. Add notifications/emails for approval/rejection
7. Update billing calculations if storage has different prices

---

## Billing Customization

Currently, the form assumes both storage types cost the same. To implement different pricing:

**Option 1: Update the Plan model to include storage pricing**
```python
class Plan(models.Model):
    # ... existing fields ...
    google_sheets_surcharge = models.DecimalField(...)  # Additional cost
    database_surcharge = models.DecimalField(...)       # Additional cost
```

**Option 2: Create a separate StoragePlan model**
```python
class StoragePrice(models.Model):
    storage_type = models.CharField(choices=Organization.StorageMode.choices)
    monthly_price = models.DecimalField()
    plan = models.ForeignKey(Plan)  # Optional: plan-specific pricing
```

Then update `RequestStorageChangeForm.save()` to use this pricing.

---

## Database Constraints

The model enforces:
- Only ONE pending or approved request per organization at a time
- Prevents duplicate active requests
- Complete audit trail maintained
- No deletion of historical requests

---

## Questions?

This feature is now ready for template creation and integration. The forms, views, models, and URL routing are all in place!
