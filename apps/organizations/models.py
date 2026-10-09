import uuid

from django.db import models


class Organization(models.Model):
    """A tenant. Its own record (this row) lives in our shared Postgres
    database, alongside auth/billing/superadmin data. Its actual business
    (finance) data lives entirely in its own Google Sheet, connected via
    Google OAuth and read/written live through apps.sheets_store — never
    in our own database. See CloudBackupConnection for that connection.
    """

    class ServiceStatus(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        SUSPENDED = "SUSPENDED", "Suspended"

    class BusinessType(models.TextChoices):
        RETAIL_ECOMMERCE = "RETAIL_ECOMMERCE", "Retail & E-commerce"
        MANUFACTURING = "MANUFACTURING", "Manufacturing"
        HEALTHCARE = "HEALTHCARE", "Healthcare & Medical"
        TECHNOLOGY = "TECHNOLOGY", "Technology & Software"
        PROFESSIONAL_SERVICES = "PROFESSIONAL_SERVICES", "Professional Services"
        FINANCIAL_SERVICES = "FINANCIAL_SERVICES", "Financial Services"
        REAL_ESTATE = "REAL_ESTATE", "Real Estate"
        CONSTRUCTION = "CONSTRUCTION", "Construction & Engineering"
        EDUCATION = "EDUCATION", "Education & Training"
        RESTAURANTS_FOOD = "RESTAURANTS_FOOD", "Restaurants & Food Services"
        HOSPITALITY_TRAVEL = "HOSPITALITY_TRAVEL", "Hospitality & Travel"
        LOGISTICS_TRANSPORT = "LOGISTICS_TRANSPORT", "Logistics & Transportation"
        WHOLESALE_DISTRIBUTION = "WHOLESALE_DISTRIBUTION", "Wholesale & Distribution"
        AUTOMOTIVE = "AUTOMOTIVE", "Automotive"
        MEDIA_ENTERTAINMENT = "MEDIA_ENTERTAINMENT", "Media & Entertainment"
        MARKETING_ADVERTISING = "MARKETING_ADVERTISING", "Marketing & Advertising"
        AGRICULTURE = "AGRICULTURE", "Agriculture & Farming"
        PHARMA_LIFE_SCIENCES = "PHARMA_LIFE_SCIENCES", "Pharmaceuticals & Life Sciences"
        ENERGY_UTILITIES = "ENERGY_UTILITIES", "Energy & Utilities"
        OTHER = "OTHER", "Other"

    class OrganizationSize(models.TextChoices):
        SOLO = "SOLO", "Just me"
        SMALL = "SMALL", "2-10 employees"
        MEDIUM = "MEDIUM", "11-50 employees"
        LARGE = "LARGE", "51-200 employees"
        ENTERPRISE = "ENTERPRISE", "200+ employees"

    class ManagerLogins(models.IntegerChoices):
        ONE = 1, "1 Admin account"
        TWO = 2, "1 Admin + 1 manager account"

    class StorageMode(models.TextChoices):
        # Finance data lives in our shared Postgres database, isolated
        # by the `organization` column every apps.finance model carries
        # (see SheetAwareManager/SheetAwareModelMixin, which auto-scope
        # every query/save to the current request's organization when
        # no Sheets session is active). No Google account needed.
        OUR_DATABASE = "OUR_DATABASE", "Finday's secure managed database"
        # Finance data lives entirely in the org's own Google Sheet,
        # read/written live over the Sheets API via its own OAuth grant
        # (apps.sheets_store) — never written to our database. Gated
        # behind connecting Google Drive first (TenantSchemaMiddleware).
        GOOGLE_SHEETS = "GOOGLE_SHEETS", "Your own Google Drive"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=220, unique=True)

    # The human-facing tenant id — also used to name the org's Google
    # Sheet ("<organization_code> Data") and its Drive backup folder,
    # when storage_mode is GOOGLE_SHEETS.
    organization_code = models.CharField(max_length=12, unique=True)

    # Where this org's finance data lives — chosen once at signup
    # (apps.accounts.forms.OrganizationSignupForm) and never changed
    # after provisioning; there's no built-in way to migrate an org
    # between the two after the fact.
    storage_mode = models.CharField(max_length=20, choices=StorageMode.choices, default=StorageMode.GOOGLE_SHEETS)

    business_type = models.CharField(
        max_length=30, choices=BusinessType.choices, default=BusinessType.OTHER
    )
    size = models.CharField(
        max_length=20, choices=OrganizationSize.choices, default=OrganizationSize.SOLO
    )
    manager_logins = models.PositiveSmallIntegerField(
        choices=ManagerLogins.choices, default=1, help_text="Number of manager accounts for this organization"
    )
    currency = models.CharField(max_length=8, default="INR")
    timezone = models.CharField(max_length=64, default="Asia/Kolkata")

    # Client profile — set/edited by superadmin, distinct from the tenant's
    # own operational data. All optional since self-serve signup doesn't
    # collect any of this today.
    industry = models.CharField(max_length=100, blank=True)
    contact_person = models.CharField(max_length=150, blank=True)
    contact_email = models.EmailField(blank=True)
    contact_phone = models.CharField(max_length=20, blank=True)
    address = models.CharField(max_length=255, blank=True)
    city = models.CharField(max_length=100, blank=True)
    state = models.CharField(max_length=100, blank=True)
    country = models.CharField(max_length=100, blank=True, default="India")
    tax_id = models.CharField("GST / tax number", max_length=32, blank=True)
    website = models.URLField(blank=True)
    pan_number = models.CharField("PAN number", max_length=20, blank=True)
    registered_address = models.CharField(
        max_length=255, blank=True,
        help_text="Leave blank if the same as the operating address above.",
    )
    employee_count = models.PositiveIntegerField(
        null=True, blank=True, help_text="Exact headcount, if known — separate from the team-size range above.",
    )

    # Bank details — for invoicing/payout reference only, not used to move
    # money anywhere in this app.
    bank_account_holder = models.CharField(max_length=150, blank=True)
    bank_account_number = models.CharField(max_length=34, blank=True)
    bank_ifsc = models.CharField("IFSC code", max_length=11, blank=True)
    bank_name = models.CharField(max_length=150, blank=True)

    # Per-org override of the earliest date this org's users may create or
    # backdate a finance entry (sales/purchase/expense — see
    # apps.billing.services.historical_window_start). A fixed calendar date,
    # not a rolling window — it does not move as time passes. Blank = inherit
    # the current plan's rolling `Plan.historical_months_limit` default (3
    # months back from today for a new org on the default free plan), unless
    # historical_entry_no_limit is set. Set only by a superadmin, from the
    # client edit form — never by the org itself.
    historical_entry_cutoff_date = models.DateField(
        null=True, blank=True,
        help_text="Fixed date before which this org may not backdate a sales/purchase/expense "
                   "entry. Blank = use the plan's rolling default. Ignored when "
                   "'No backdating limit' is checked. Superadmin-only.",
    )
    historical_entry_no_limit = models.BooleanField(
        default=False,
        help_text="Lets this org backdate an entry to any date, overriding both the plan "
                   "default and the cutoff date above. Superadmin-only.",
    )

    # Account status: whether this is a legitimate, non-deleted account.
    # Deliberately separate from `service_status` below — a client is never
    # deleted or deactivated as an account just because their payment is
    # overdue; only their SERVICE (login/app access) is suspended.
    is_active = models.BooleanField(default=True)

    # Service status: whether this org's users can actually log in and use
    # the app right now. Controlled exclusively through the superadmin
    # service-control actions (start/stop/suspend/resume) — see
    # apps.organizations.service_control — never edited directly.
    service_status = models.CharField(
        max_length=10, choices=ServiceStatus.choices, default=ServiceStatus.ACTIVE
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return self.name

    @property
    def is_service_active(self) -> bool:
        return self.service_status == self.ServiceStatus.ACTIVE

    @property
    def is_payment_hold(self) -> bool:
        """Service is stopped/suspended *because a payment is pending*. Such a client
        can still sign in - but only reaches the Billing page until they pay. Any
        other suspension reason keeps the hard block (no sign-in at all)."""
        if self.is_service_active:
            return False
        last = (
            self.service_status_changes
            .filter(action__in=[ServiceStatusChange.Action.STOP, ServiceStatusChange.Action.SUSPEND])
            .order_by("-created_at")
            .first()
        )
        return bool(last and last.reason == ServiceStatusChange.Reason.PAYMENT_OVERDUE)


class ServiceStatusChange(models.Model):
    """Audit log entry for a superadmin start/stop/suspend/resume action.
    Never edited or deleted — this is the permanent record of who changed a
    client's service status, when, and why.
    """

    class Action(models.TextChoices):
        START = "START", "Start Service"
        STOP = "STOP", "Stop Service"
        SUSPEND = "SUSPEND", "Suspend Service"
        RESUME = "RESUME", "Resume Service"

    class Reason(models.TextChoices):
        PAYMENT_OVERDUE = "PAYMENT_OVERDUE", "Payment pending / overdue"
        CUSTOMER_REQUESTED = "CUSTOMER_REQUESTED", "Customer requested"
        ADMINISTRATIVE = "ADMINISTRATIVE", "Administrative action"
        MAINTENANCE = "MAINTENANCE", "Maintenance"
        SECURITY = "SECURITY", "Security"
        OTHER = "OTHER", "Other"

    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="service_status_changes"
    )
    # Snapshot, not a FK: the superadmin performing this action is always
    # authenticated on the `default` alias, but this log is written to
    # whichever alias (`dev`/`prod`) the organization lives in — which may
    # not be `default`, so the admin's User row may not even exist there.
    performed_by_email = models.EmailField()

    action = models.CharField(max_length=10, choices=Action.choices)
    previous_status = models.CharField(max_length=10, choices=Organization.ServiceStatus.choices)
    new_status = models.CharField(max_length=10, choices=Organization.ServiceStatus.choices)
    reason = models.CharField(max_length=32, choices=Reason.choices, blank=True)
    notes = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["organization", "-created_at"])]

    def __str__(self) -> str:
        return f"{self.organization_id}: {self.action} ({self.previous_status} -> {self.new_status})"


class CloudBackupConnection(models.Model):
    """An org's own connected Google account. The spreadsheet it points
    at (`external_file_id`) is this org's live database — every finance
    read/write goes straight to it over the Sheets API (see
    apps.sheets_store), never to our own database. Tokens are
    Fernet-encrypted at rest (apps.organizations.encryption) — never
    stored in plain text.
    """

    class Provider(models.TextChoices):
        GOOGLE_DRIVE = "GOOGLE_DRIVE", "Google Drive"

    organization = models.OneToOneField(
        Organization, on_delete=models.CASCADE, related_name="cloud_backup"
    )
    provider = models.CharField(max_length=20, choices=Provider.choices, default=Provider.GOOGLE_DRIVE)

    # The connected Google account's email, purely for display on
    # Profile ("which account is this?") — not used for auth or access
    # control. Blank for connections made before this field existed.
    connected_email = models.EmailField(blank=True)

    access_token_encrypted = models.BinaryField()
    refresh_token_encrypted = models.BinaryField()
    token_expires_at = models.DateTimeField()

    # The dedicated app-created folder in the client's Drive everything
    # gets uploaded into (e.g. "Finday Backups") — never an arbitrary
    # folder of theirs, since the drive.file OAuth scope only ever lets us
    # see files/folders this app itself created.
    external_folder_id = models.CharField(max_length=128)
    # The synced Google Sheet's own file id — lets Profile link straight
    # to it (docs.google.com/spreadsheets/d/<this>/edit) instead of just
    # the folder. Blank until the first successful sync.
    external_file_id = models.CharField(max_length=128, blank=True)

    connected_at = models.DateTimeField(auto_now_add=True)
    last_synced_at = models.DateTimeField(null=True, blank=True)
    last_sync_error = models.CharField(max_length=500, blank=True)

    def __str__(self) -> str:
        return f"{self.organization_id}: {self.get_provider_display()}"


def org_needs_drive_connection(org) -> bool:
    """True while an org must connect Google Drive before using the app:
    a Drive org with no (usable) connection yet, or an own-database org whose
    switch to Drive was approved and is waiting for the client to connect."""
    if org.storage_mode == Organization.StorageMode.GOOGLE_SHEETS:
        connection = getattr(org, "cloud_backup", None)
        return connection is None or not connection.external_file_id
    return DataStorageChangeRequest.objects.filter(
        organization=org,
        status=DataStorageChangeRequest.Status.APPROVED,
        requested_storage=Organization.StorageMode.GOOGLE_SHEETS,
    ).exists()


class DataStorageChangeRequest(models.Model):
    """Request by a client to change their data storage from one provider to another.
    Once submitted, only a superadmin can approve or reject it. Approved requests
    update the organization's storage_mode and trigger necessary migrations.
    """

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending Approval"
        APPROVED = "APPROVED", "Approved"
        REJECTED = "REJECTED", "Rejected"
        COMPLETED = "COMPLETED", "Completed"
        CANCELLED = "CANCELLED", "Cancelled"

    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="storage_change_requests"
    )

    # Current and requested storage types
    current_storage = models.CharField(
        max_length=20, choices=Organization.StorageMode.choices
    )
    requested_storage = models.CharField(
        max_length=20, choices=Organization.StorageMode.choices
    )

    # Plan names before/after the switch (e.g. "Professional" -> "Professional Drive")
    current_plan_name = models.CharField(max_length=100, blank=True)
    requested_plan_name = models.CharField(max_length=100, blank=True)

    # Billing impact
    current_monthly_price = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    requested_monthly_price = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    price_difference = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    # Client details
    reason = models.TextField(blank=True, help_text="Why the client wants to change storage")

    # Admin approval details
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    admin_notes = models.TextField(blank=True, help_text="Superadmin notes on approval/rejection")
    reviewed_by_email = models.EmailField(blank=True)

    # Timestamps
    requested_at = models.DateTimeField(auto_now_add=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-requested_at"]
        indexes = [
            models.Index(fields=["organization", "-requested_at"]),
            models.Index(fields=["status", "-requested_at"]),
        ]
        constraints = [
            # Only one pending/approved request per organization at a time
            models.UniqueConstraint(
                fields=["organization", "status"],
                condition=models.Q(status__in=["PENDING", "APPROVED"]),
                name="unique_active_storage_request",
            )
        ]

    def __str__(self) -> str:
        return f"{self.organization.name}: {self.get_current_storage_display()} → {self.get_requested_storage_display()} ({self.get_status_display()})"

    @property
    def plan_change_text(self) -> str:
        """e.g. "Professional → Professional Drive", or "" if unknown."""
        if self.current_plan_name and self.requested_plan_name:
            return f"{self.current_plan_name} → {self.requested_plan_name}"
        return ""

    @property
    def billing_impact_text(self) -> str:
        """Human-readable billing impact."""
        if self.price_difference == 0:
            return "No change to your monthly billing"
        elif self.price_difference > 0:
            return f"₹{abs(self.price_difference):.2f} increase per month"
        else:
            return f"₹{abs(self.price_difference):.2f} decrease per month"
