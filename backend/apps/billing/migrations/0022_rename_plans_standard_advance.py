from django.db import migrations

# Plan.slug -> new name. Slugs stay the same, so subscriptions, the Kickbacks
# gate and the plan-change ladders keep pointing at the same rows.
RENAMES = {
    "starter": "Standard",
    "pro": "Standard Drive",
    "smart-drive": "Advance",
    "enterprise": "Advance Drive",
}


def rename_plans(apps, schema_editor):
    Plan = apps.get_model("billing", "Plan")
    db = schema_editor.connection.alias
    for slug, name in RENAMES.items():
        Plan.objects.using(db).filter(slug=slug).update(name=name)


class Migration(migrations.Migration):

    dependencies = [
        ("billing", "0021_plan_allowed_pages_lite"),
    ]

    operations = [
        migrations.RunPython(rename_plans, migrations.RunPython.noop),
    ]
