from django.db import transaction
from django.utils.text import slugify

from apps.accounts.models import User

from .models import Organization
from .tenant import provision_tenant_schema
from .utils import (
    drop_tenant_schema,
    generate_organization_code,
    normalize_schema_name,
    schema_exists,
)


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


def create_organization_with_tenant_schema_and_admin(*, org_data: dict, admin_data: dict, using: str = "default"):
    """Create the Organization row, its isolated Postgres schema, and the
    first (owner) user for it, all on the `using` database alias. Rolls
    back everything if the schema can't be created.
    """
    with transaction.atomic(using=using):
        for _ in range(50):
            code = generate_organization_code()
            if Organization.objects.using(using).filter(organization_code=code).exists():
                continue
            schema_name = normalize_schema_name(code)
            if schema_exists(schema_name, using=using):
                continue
            break
        else:
            raise OrganizationSignupError("Unable to allocate a unique organization code.")

        org = Organization(
            name=org_data["name"],
            slug=_unique_slug(org_data["name"], using=using),
            organization_code=code,
            schema_name=schema_name,
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

        try:
            provision_tenant_schema(
                org,
                using=using,
                fy_start_month=org_data.get("fy_start_month", 4),
                opening_balance=org_data.get("opening_balance", 0),
            )
        except Exception as exc:
            raise OrganizationSignupError(f"Failed to provision tenant schema: {exc}") from exc

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
        admin.save(using=using)

        return org, admin


def delete_organization_and_tenant(org: Organization, using: str = "default") -> None:
    with transaction.atomic(using=using):
        schema_name = org.schema_name
        User.objects.using(using).filter(organization=org).update(is_active=False, organization=None)
        org.delete(using=using)
        if schema_name and schema_exists(schema_name, using=using):
            try:
                drop_tenant_schema(schema_name, using=using)
            except Exception as exc:
                raise OrganizationSignupError(f"Failed to drop tenant schema: {exc}") from exc
