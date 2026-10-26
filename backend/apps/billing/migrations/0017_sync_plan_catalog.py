from decimal import Decimal

from django.db import migrations

ALL = [
    "business_dashboard", "sales_analytics", "expense_analytics", "purchase_analytics",
    "profit_loss", "cash_flow", "reports", "export", "ai_insights",
]

# (slug, name, active, landing, monthly, yearly, m_disc, y_disc, trial, hist, users)
PLANS = [
    ("free", "Complementary", True, False, "0", "0", 0, 0, 14, 3, None),
    ("pro-old", "Pro Legacy", False, False, "1798", "21570", 50, 52, 14, 3, None),
    ("starter", "Professional", True, True, "1999", "23988", 35, 35, 7, 3, 1),
    ("pro", "Professional Drive", True, True, "2499", "29988", 40, 40, 7, 3, 1),
    ("smart-drive", "Business", True, True, "2499", "29988", 36, 36, 7, 3, 2),
    ("enterprise", "Business Drive", True, True, "2999", "35988", 40, 40, 7, 3, 2),
]


def sync_plans(apps, schema_editor):
    """Make every environment's plan catalog match dev. Upserts by slug so
    existing subscriptions keep pointing at the same rows. Plan names are
    unique, so rows are first parked on temporary names to avoid collisions
    while names are swapped around."""
    Plan = apps.get_model("billing", "Plan")
    db = schema_editor.connection.alias
    qs = Plan.objects.using(db)

    for slug, *_ in PLANS:
        qs.filter(slug=slug).update(name=f"__sync_{slug}")

    for slug, name, active, landing, monthly, yearly, md, yd, trial, hist, users in PLANS:
        qs.update_or_create(slug=slug, defaults=dict(
            name=name, is_active=active, show_on_landing_page=landing,
            monthly_price=Decimal(monthly), yearly_price=Decimal(yearly),
            monthly_discount_percent=md, yearly_discount_percent=yd,
            trial_days=trial, historical_months_limit=hist,
            user_limit=users, features=ALL,
        ))


class Migration(migrations.Migration):

    dependencies = [
        ("billing", "0016_invoice_service_billing_end_date_and_more"),
    ]

    operations = [
        migrations.RunPython(sync_plans, migrations.RunPython.noop),
    ]
