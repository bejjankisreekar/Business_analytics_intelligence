import uuid

from django.db import models


class Organization(models.Model):
    """A tenant. Lives in the shared `public` schema; its own operational
    data (once built) lives in the isolated schema named by `schema_name`.
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

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=220, unique=True)

    # Tenant identity — organization_code is the human-facing id,
    # schema_name is the random, derived PostgreSQL schema for this tenant.
    organization_code = models.CharField(max_length=12, unique=True)
    schema_name = models.CharField(max_length=63, unique=True)

    business_type = models.CharField(
        max_length=30, choices=BusinessType.choices, default=BusinessType.OTHER
    )
    size = models.CharField(
        max_length=20, choices=OrganizationSize.choices, default=OrganizationSize.SOLO
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
