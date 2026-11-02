from django.db import migrations

# The starter codes that need more than the default three levels.
PRESETS = {
    "MOBS": [
        {"name": "Mobile", "common": False},
        {"name": "Brand", "common": False},
        {"name": "Model name", "common": False},
        {"name": "Colour", "common": True},
        {"name": "RAM", "common": True},
        {"name": "Storage", "common": True},
    ],
    "CLTH": [
        {"name": "Category", "common": False},
        {"name": "Brand", "common": False},
        {"name": "Item", "common": False},
        {"name": "Size", "common": True},
        {"name": "Colour", "common": True},
    ],
}


def apply_presets(apps, schema_editor):
    db = schema_editor.connection.alias
    Category = apps.get_model("organizations", "OrganizationCategory")
    Organization = apps.get_model("organizations", "Organization")
    for code, levels in PRESETS.items():
        category = Category.objects.using(db).filter(code=code).first()
        if category is None:
            continue
        category.levels = levels
        category.save(update_fields=["levels"])
        # Clients that already have the code follow it. Their other features are untouched.
        Organization.objects.using(db).filter(category=category).update(levels=levels)


class Migration(migrations.Migration):

    dependencies = [
        ("organizations", "0030_category_levels"),
    ]

    operations = [
        migrations.RunPython(apply_presets, migrations.RunPython.noop),
    ]
