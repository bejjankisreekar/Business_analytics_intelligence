from django.db import migrations


def set_trial_days(apps, schema_editor):
    """Every plan gets a 30-day trial."""
    Plan = apps.get_model("billing", "Plan")
    Plan.objects.using(schema_editor.connection.alias).update(trial_days=30)


class Migration(migrations.Migration):

    dependencies = [
        ("billing", "0018_set_trial_days_14"),
    ]

    operations = [
        migrations.RunPython(set_trial_days, migrations.RunPython.noop),
    ]
