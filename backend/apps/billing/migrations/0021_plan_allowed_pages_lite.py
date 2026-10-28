from decimal import Decimal

from django.db import migrations, models

LITE_PAGES = ["customer_requests", "daily_report", "dashboard"]


def add_lite(apps, schema_editor):
    """Lite pack: Customer Requests, Daily Book and Dashboard only. Runs on every
    database `migrate` is run against, so dev and prod each get their own row."""
    Plan = apps.get_model("billing", "Plan")
    Plan.objects.using(schema_editor.connection.alias).update_or_create(
        slug="lite",
        defaults=dict(
            name="Lite", is_active=True, show_on_landing_page=False,
            monthly_price=Decimal("499"), yearly_price=Decimal("4990"),
            trial_days=14, historical_months_limit=3, user_limit=1,
            features=["business_dashboard"], allowed_pages=LITE_PAGES,
        ),
    )


def remove_lite(apps, schema_editor):
    apps.get_model("billing", "Plan").objects.using(schema_editor.connection.alias).filter(slug="lite").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("billing", "0020_invoice_upgrade_plan"),
    ]

    operations = [
        migrations.AddField(
            model_name="plan",
            name="allowed_pages",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.RunPython(add_lite, remove_lite),
    ]
