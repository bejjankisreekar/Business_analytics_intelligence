from django.db import migrations

ALL_FEATURE_KEYS = [
    "business_dashboard", "sales_analytics", "expense_analytics", "purchase_analytics",
    "profit_loss", "cash_flow", "reports", "export", "ai_insights",
]


def consolidate_plans(apps, schema_editor):
    """This project only needs two tiers: Free (trial-ish, no paid features)
    and Enterprise (everything, one paid plan for all clients) — not the
    four placeholder tiers seeded in 0002. Reassign any subscription still
    pointing at Basic/Pro to Enterprise, then remove those two plans."""
    Plan = apps.get_model("billing", "Plan")
    Subscription = apps.get_model("billing", "Subscription")
    db_alias = schema_editor.connection.alias

    enterprise = Plan.objects.using(db_alias).filter(slug="enterprise").first()
    if enterprise is None:
        return  # nothing to consolidate onto; leave existing data alone

    enterprise.features = ALL_FEATURE_KEYS
    enterprise.is_active = True
    enterprise.save(using=db_alias)

    for slug in ("basic", "pro"):
        plan = Plan.objects.using(db_alias).filter(slug=slug).first()
        if plan is None:
            continue
        Subscription.objects.using(db_alias).filter(plan_id=plan.id).update(plan_id=enterprise.id)
        plan.delete()


class Migration(migrations.Migration):

    dependencies = [
        ("billing", "0002_plans_and_subscription_history"),
    ]

    operations = [
        migrations.RunPython(consolidate_plans, migrations.RunPython.noop),
    ]
