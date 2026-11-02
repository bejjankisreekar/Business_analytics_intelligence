from django.db import migrations

# code, name, scanning on?, inventory type
STARTER_CODES = [
    ("HOSP", "Hospitals", False, "OTHER"),
    ("DIAG", "Diagnostic centres", False, "OTHER"),
    ("REST", "Restaurants", False, "OTHER"),
    ("MOBS", "Mobile shops", True, "MOBILES"),
    ("CLTH", "Clothing stores", True, "CLOTHES"),
    ("GENR", "General stores", True, "OTHER"),
]

# Existing organizations are filed under the code that matches their business type. Only the
# code is set; their scanning switch and inventory type are left exactly as they are.
BUSINESS_TYPE_TO_CODE = {
    "HEALTHCARE": "HOSP",
    "DIAGNOSTIC": "DIAG",
    "RESTAURANTS_FOOD": "REST",
}


def seed(apps, schema_editor):
    db = schema_editor.connection.alias
    Category = apps.get_model("organizations", "OrganizationCategory")
    Organization = apps.get_model("organizations", "Organization")
    by_code = {}
    for code, name, scanning, inventory_type in STARTER_CODES:
        category, _ = Category.objects.using(db).get_or_create(
            code=code,
            defaults={"name": name, "barcode_scanning_enabled": scanning, "inventory_type": inventory_type},
        )
        by_code[code] = category
    for business_type, code in BUSINESS_TYPE_TO_CODE.items():
        Organization.objects.using(db).filter(business_type=business_type, category__isnull=True).update(
            category=by_code[code]
        )


def unseed(apps, schema_editor):
    db = schema_editor.connection.alias
    Category = apps.get_model("organizations", "OrganizationCategory")
    Category.objects.using(db).filter(code__in=[c[0] for c in STARTER_CODES]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("organizations", "0028_organization_categories"),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
