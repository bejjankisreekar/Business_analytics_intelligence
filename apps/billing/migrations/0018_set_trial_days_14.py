from django.db import migrations


def set_trial_days(apps, schema_editor):
    """Every plan gets a 14-day trial."""
    Plan = apps.get_model("billing", "Plan")
    Plan.objects.using(schema_editor.connection.alias).update(trial_days=14)


class Migration(migrations.Migration):

    dependencies = [
        ("billing", "0017_sync_plan_catalog"),
    ]

    operations = [
        migrations.RunPython(set_trial_days, migrations.RunPython.noop),
    ]
