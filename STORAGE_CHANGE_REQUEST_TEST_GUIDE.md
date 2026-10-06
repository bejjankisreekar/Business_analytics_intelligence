# Storage Change Request Feature - Test Guide

## ✅ What's Been Implemented

1. **Database Model** - `DataStorageChangeRequest` with full audit trail
2. **Client Forms** - Request form with billing impact calculation
3. **Views** (6 total):
   - Client: Request form + View requests
   - Admin: Dashboard + Detail review page
4. **Templates** (4 total):
   - Client: request_storage_change.html
   - Client: storage_change_requests.html
   - Admin: storage_change_requests.html
   - Admin: storage_change_detail.html
5. **URL Routing** - All endpoints configured
6. **Database** - Migrations created and applied ✓

---

## 🧪 Manual Testing Workflow

### **Test 1: Client Requests Storage Change**

#### Step 1: Login as Client
```
1. Login to the application
2. Navigate to: /organizations/storage/change/request/
3. You should see the request form
```

#### Step 2: Submit Request
```
1. Current storage is displayed at top
2. Select new storage type (radio button):
   - If currently on GOOGLE_SHEETS → select OUR_DATABASE
   - If currently on OUR_DATABASE → select GOOGLE_SHEETS
3. Enter reason (optional): "Need better integration with our ERP system"
4. Click "Submit Request"
```

#### Step 3: Check Success
```
Expected Results:
✓ Success message: "Your storage change request has been submitted"
✓ Billing impact shown: "₹X increase/decrease per month" or "No change"
✓ Redirected to /organizations/storage/change/requests/
✓ Request shows as "⏳ Pending Approval"
```

#### Step 4: View Pending Request
```
1. Navigate to: /organizations/storage/change/requests/
2. Should see:
   ✓ Pending request card showing:
     - Current storage and requested storage
     - Billing impact
     - Requested date
     - Client reason (if provided)
   ✓ "New Request" button disabled or shows message
```

---

### **Test 2: Admin Reviews and Approves Request**

#### Step 1: Admin Dashboard
```
1. Login as superadmin
2. Navigate to: /superadmin/storage-change-requests/
3. You should see:
   ✓ List of all pending requests
   ✓ Filter buttons: Pending | Approved | All
   ✓ Each request shows:
     - Organization name
     - Storage change: FROM → TO
     - Billing impact
     - Client reason (truncated)
     - Status badge (⏳ Pending)
```

#### Step 2: Review Individual Request
```
1. Click on a pending request
2. Navigated to: /superadmin/storage-change-requests/<id>/
3. You should see:
   ✓ Left side: Organization details
   ✓ Storage change details
   ✓ Billing impact breakdown:
     - Current monthly price
     - Requested monthly price
     - Monthly difference (color-coded)
   ✓ Request info: Date requested, Current status
   ✓ Right side: Client's reason in full
```

#### Step 3: Approve Request
```
1. Select "Approve" radio button
2. (Optional) Add admin notes: "Approved - good strategic move"
3. Click "Submit Review"
4. You should see:
   ✓ Success message: "Request approved!"
   ✓ Redirected to dashboard
   ✓ Request status updated to "✓ Approved"
```

#### Step 4: Reject Request (Alternative Test)
```
1. On a different request, select "Reject" radio button
2. Add notes: "Storage type not available for enterprise plan"
3. Click "Submit Review"
4. You should see:
   ✓ Message: "Request rejected"
   ✓ Request status updated to "✗ Rejected"
```

---

### **Test 3: Verify Data Integrity**

#### Test Constraint (No Duplicate Active Requests)
```
1. Client requests storage change (Request #1 created)
2. Before approval, client tries to request again
3. Expected: Form shows error:
   ✓ "You already have a pending storage change request"
4. After approval/rejection, client can request again
5. Expected: New form submission works ✓
```

#### Test Billing Calculation
```
1. Make request with plan pricing:
   - Current: ₹500/month
   - Requested: ₹500/month (same)
2. Check billing impact: "No change to your monthly billing" ✓

3. Make request with plan pricing:
   - Current: ₹500/month
   - Requested: ₹1000/month (higher)
4. Check billing impact: "₹500.00/month increase" ✓
```

---

## 🔧 SQL Queries for Testing

### Check All Requests
```sql
SELECT 
  id, 
  organization_id, 
  current_storage, 
  requested_storage, 
  status, 
  requested_at 
FROM organizations_datastoragechangerequest 
ORDER BY requested_at DESC;
```

### Check Pending Requests
```sql
SELECT * FROM organizations_datastoragechangerequest 
WHERE status = 'PENDING' 
ORDER BY requested_at DESC;
```

### Check Organization's Request History
```sql
SELECT * FROM organizations_datastoragechangerequest 
WHERE organization_id = '<org_id>' 
ORDER BY requested_at DESC;
```

---

## 🎯 Test Coverage Checklist

### Client Views
- [ ] Client can navigate to request form
- [ ] Form shows current storage type
- [ ] Can select different storage type
- [ ] Billing impact calculated and displayed
- [ ] Can submit with reason (optional)
- [ ] Can submit without reason
- [ ] Error message when trying to submit duplicate request
- [ ] Can view pending requests
- [ ] Can view request history
- [ ] Pending request shows correct details
- [ ] Approved request shows correct status
- [ ] Rejected request shows correct status

### Admin Views
- [ ] Admin can access dashboard
- [ ] Dashboard shows all pending requests
- [ ] Filter buttons work (Pending | Approved | All)
- [ ] Can click request to view details
- [ ] Detail page shows organization info
- [ ] Detail page shows storage change
- [ ] Detail page shows billing impact
- [ ] Detail page shows client reason
- [ ] Can approve with notes
- [ ] Can approve without notes
- [ ] Can reject with notes
- [ ] Can reject without notes
- [ ] Success message on approval
- [ ] Request status updates in database
- [ ] Admin name recorded in reviewed_by_email
- [ ] Review timestamp recorded

### Data Integrity
- [ ] No duplicate pending requests per organization
- [ ] Billing impact correctly calculated
- [ ] Billing impact matches plan pricing
- [ ] Approved requests show correct status
- [ ] Rejected requests show correct status
- [ ] Request history preserved
- [ ] Audit trail complete (who, what, when)

---

## 🐛 Edge Cases to Test

1. **Organization with no current subscription**
   - Billing impact should default to ₹0

2. **Same storage type selected**
   - Form should prevent submission or show error

3. **Multiple admins approving**
   - Last approval should win
   - All approvals recorded

4. **Client accessing other org's request**
   - Should get 404 or permission error

5. **Admin accessing as non-superuser**
   - Should get permission denied error

---

## 📝 Test Data Setup

### Create Test Data (Management Command)
```python
# Run in shell: python manage.py shell
from apps.organizations.models import Organization, DataStorageChangeRequest
from apps.billing.models import Subscription, Plan

# Get a test organization
org = Organization.objects.first()

# Create a test request
request = DataStorageChangeRequest.objects.create(
    organization=org,
    current_storage=Organization.StorageMode.GOOGLE_SHEETS,
    requested_storage=Organization.StorageMode.OUR_DATABASE,
    current_monthly_price=500,
    requested_monthly_price=500,
    price_difference=0,
    reason="Testing the feature",
    status=DataStorageChangeRequest.Status.PENDING
)

print(f"Created request {request.id}")
```

---

## 🔗 URL Reference

### Client URLs
```
GET  /organizations/storage/change/request/    - Show form
POST /organizations/storage/change/request/    - Submit request
GET  /organizations/storage/change/requests/   - View requests
```

### Admin URLs
```
GET  /organizations/superadmin/storage-change-requests/       - Dashboard
GET  /organizations/superadmin/storage-change-requests/<id>/  - Detail
POST /organizations/superadmin/storage-change-requests/<id>/  - Review
```

---

## ✅ Complete Feature Checklist

- [x] Model created with all fields
- [x] Forms created (request + approval)
- [x] Views created (client + admin)
- [x] Templates created (4 files)
- [x] URLs configured
- [x] Migrations created & applied
- [ ] Tested manually (start here ⬆️)
- [ ] Unit tests written
- [ ] Integration tests written
- [ ] Admin interface (Django admin)
- [ ] Email notifications (on approval/rejection)
- [ ] Actual storage migration logic
- [ ] Migration rollback capability

---

## 📊 Feature Status

**Current:** Feature fully functional for request/approval workflow

**Still To Do:**
1. Implement `complete_storage_change_request()` service
2. Add email notifications
3. Add Django admin integration
4. Create unit/integration tests
5. Implement actual data migration logic

---

## Questions During Testing?

If you find any issues:
1. Check the error message in the browser or console
2. Look at Django error logs
3. Verify database entries with SQL queries above
4. Check if templates are in correct directories

Happy testing! 🚀
