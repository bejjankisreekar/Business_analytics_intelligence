import datetime

from django.db import IntegrityError, transaction
from django.utils.text import slugify

from apps.accounts.models import User

from .models import Organization
from .utils import generate_organization_code


class OrganizationSignupError(RuntimeError):
    pass


def _unique_slug(name: str, using: str = "default") -> str:
    base = slugify(name) or "organization"
    slug = base
    n = 1
    while Organization.objects.using(using).filter(slug=slug).exists():
        n += 1
        slug = f"{base}-{n}"
    return slug


def _provision_our_database_tenant(org: Organization) -> None:
    """Seeds default categories + finance settings for a StorageMode.
    OUR_DATABASE org, directly into our shared Postgres tables — the
    equivalent of apps.sheets_store.provisioning.provision_sheet_tenant
    for a GOOGLE_SHEETS org, just immediate (no Drive connection to
    wait for)."""
    from apps.finance.models import Category, FinanceSettings
    from apps.sheets_store.provisioning import (
        DEFAULT_EXPENSE_CATEGORIES,
        DEFAULT_PURCHASE_CATEGORIES,
        DEFAULT_SALES_CHANNELS,
    )

    FinanceSettings.objects.create(
        organization=org, fy_start_month=4, opening_balance=0, opening_date=datetime.date.today(),
    )
    Category.objects.bulk_create(
        [Category(organization=org, kind=Category.Kind.EXPENSE, name=name) for name in DEFAULT_EXPENSE_CATEGORIES]
        + [Category(organization=org, kind=Category.Kind.PURCHASE, name=name) for name in DEFAULT_PURCHASE_CATEGORIES]
        + [Category(organization=org, kind=Category.Kind.SALES, name=name) for name in DEFAULT_SALES_CHANNELS]
    )


def create_organization_with_tenant_schema_and_admin(*, org_data: dict, admin_data: dict, using: str = "default"):
    """Create the Organization row and its first (owner) user, on the
    `using` database alias. Where its finance data actually lives
    depends on org_data["storage_mode"]:

    - GOOGLE_SHEETS: lives entirely in its own Google Sheet.
      Provisioning (creating that Sheet) is deferred until the owner
      connects Google Drive, see GoogleDriveOAuthCallbackView and
      TenantSchemaMiddleware's connect-database gate — so signing up
      this way requires Google OAuth to be configured at all.
    - OUR_DATABASE: lives in our own shared Postgres tables, scoped to
      this org. Provisioned immediately below — no Google account
      needed.

    Rolls back everything if the admin user can't be created.
    """
    storage_mode = org_data.get("storage_mode", Organization.StorageMode.GOOGLE_SHEETS)

    if storage_mode == Organization.StorageMode.GOOGLE_SHEETS:
        from . import google_drive_client as drive

        if not drive.is_configured():
            raise OrganizationSignupError(
                "Signups are temporarily unavailable — Google Drive isn't configured on this "
                "deployment yet. Contact the site administrator."
            )

    with transaction.atomic(using=using):
        for _ in range(50):
            code = generate_organization_code()
            if not Organization.objects.using(using).filter(organization_code=code).exists():
                break
        else:
            raise OrganizationSignupError("Unable to allocate a unique organization code.")

        org = Organization(
            name=org_data["name"],
            slug=_unique_slug(org_data["name"], using=using),
            organization_code=code,
            storage_mode=storage_mode,
            business_type=org_data.get("business_type", Organization.BusinessType.OTHER),
            size=org_data.get("size", Organization.OrganizationSize.SOLO),
            industry=org_data.get("industry", ""),
            contact_person=org_data.get("contact_person", ""),
            contact_email=org_data.get("contact_email", ""),
            contact_phone=org_data.get("contact_phone", ""),
            address=org_data.get("address", ""),
            city=org_data.get("city", ""),
            state=org_data.get("state", ""),
            country=org_data.get("country") or "India",
            tax_id=org_data.get("tax_id", ""),
            website=org_data.get("website", ""),
        )
        org.save(using=using)

        if storage_mode == Organization.StorageMode.OUR_DATABASE:
            try:
                _provision_our_database_tenant(org)
            except Exception as exc:
                raise OrganizationSignupError(f"Failed to provision your database: {exc}") from exc
        # else GOOGLE_SHEETS: provisioning deferred to the Drive connect step.

        admin = User(
            email=User.objects.normalize_email(admin_data["email"]),
            username=admin_data.get("username") or None,
            first_name=admin_data.get("first_name", ""),
            last_name=admin_data.get("last_name", ""),
            role=User.Role.OWNER,
            organization=org,
            is_staff=False,
        )
        admin.set_password(admin_data["password"])
        try:
            admin.save(using=using)
        except IntegrityError as exc:
            # The signup form already checked email/username uniqueness, but
            # that's a TOCTOU gap — someone else could take either between
            # that check and this save (e.g. a near-simultaneous signup).
            # Catch it here instead of letting a raw IntegrityError 500.
            raise OrganizationSignupError(
                "That email or username was just taken by someone else. Please go back and try again."
            ) from exc

        return org, admin


def delete_organization_and_tenant(org: Organization, using: str = "default") -> None:
    with transaction.atomic(using=using):
        User.objects.using(using).filter(organization=org).update(is_active=False, organization=None)
        # GOOGLE_SHEETS: the org's actual data is its own Google Sheet,
        # in its own Drive — never ours to delete. OUR_DATABASE: its
        # finance rows cascade away with the Organization row below —
        # that data genuinely is ours to manage.
        org.delete(using=using)
