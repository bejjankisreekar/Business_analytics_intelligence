import datetime
import secrets

import django.db.models.deletion
from django.db import migrations, models

DEFAULT_PLANS = [
    # slug, name, monthly, yearly, trial_days
    ("free", "Free", "0.00", "0.00", 14),
    ("basic", "Basic", "499.00", "4990.00", 14),
    ("pro", "Pro", "999.00", "9990.00", 14),
    ("enterprise", "Enterprise", "2999.00", "29990.00", 14),
]

OLD_PLAN_TO_SLUG = {"FREE": "free", "BASIC": "basic", "PRO": "pro", "ENTERPRISE": "enterprise"}


def seed_default_plans(apps, schema_editor):
    Plan = apps.get_model("billing", "Plan")
    db_alias = schema_editor.connection.alias
    for slug, name, monthly, yearly, trial_days in DEFAULT_PLANS:
        Plan.objects.using(db_alias).get_or_create(
            slug=slug,
            defaults={
                "name": name,
                "monthly_price": monthly,
                "yearly_price": yearly,
                "trial_days": trial_days,
                "is_active": True,
                "features": [],
            },
        )


def populate_subscription_fields(apps, schema_editor):
    Subscription = apps.get_model("billing", "Subscription")
    Plan = apps.get_model("billing", "Plan")
    db_alias = schema_editor.connection.alias

    seen_ids = set()
    for sub in Subscription.objects.using(db_alias).all():
        slug = OLD_PLAN_TO_SLUG.get(sub.plan, "free")
        plan = Plan.objects.using(db_alias).filter(slug=slug).first()
        if plan is None:
            plan = Plan.objects.using(db_alias).filter(slug="free").first()
        sub.plan_new_id = plan.id if plan else None

        while True:
            candidate = f"SUB-{secrets.token_hex(4).upper()}"
            if candidate not in seen_ids:
                seen_ids.add(candidate)
                break
        sub.subscription_id = candidate

        sub.start_date = sub.created_at.date() if sub.created_at else datetime.date.today()
        sub.end_date = sub.renewal_date
        sub.is_current = True
        sub.save(
            using=db_alias,
            update_fields=["plan_new_id", "subscription_id", "start_date", "end_date", "is_current"],
        )


class Migration(migrations.Migration):

    dependencies = [
        ("organizations", "0002_organization_address_organization_city_and_more"),
        ("billing", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="Plan",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=100, unique=True)),
                ("slug", models.SlugField(max_length=110, unique=True)),
                ("is_active", models.BooleanField(default=True)),
                ("monthly_price", models.DecimalField(decimal_places=2, default=0, max_digits=10)),
                ("yearly_price", models.DecimalField(decimal_places=2, default=0, max_digits=10)),
                ("trial_days", models.PositiveIntegerField(default=0, help_text="0 = no trial period")),
                ("user_limit", models.PositiveIntegerField(blank=True, help_text="Blank = unlimited", null=True)),
                ("business_limit", models.PositiveIntegerField(blank=True, help_text="Blank = unlimited", null=True)),
                ("features", models.JSONField(blank=True, default=list)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"ordering": ["monthly_price", "name"]},
        ),
        migrations.RunPython(seed_default_plans, migrations.RunPython.noop),
        migrations.AddField(
            model_name="subscription",
            name="subscription_id",
            field=models.CharField(max_length=20, null=True, editable=False),
        ),
        migrations.AddField(
            model_name="subscription",
            name="plan_new",
            field=models.ForeignKey(
                null=True, on_delete=django.db.models.deletion.PROTECT,
                related_name="subscriptions_tmp", to="billing.plan",
            ),
        ),
        migrations.AddField(
            model_name="subscription", name="start_date", field=models.DateField(blank=True, null=True)
        ),
        migrations.AddField(
            model_name="subscription", name="end_date", field=models.DateField(blank=True, null=True)
        ),
        migrations.AddField(
            model_name="subscription", name="trial_start_date", field=models.DateField(blank=True, null=True)
        ),
        migrations.AddField(
            model_name="subscription", name="trial_end_date", field=models.DateField(blank=True, null=True)
        ),
        migrations.AddField(
            model_name="subscription", name="price", field=models.DecimalField(decimal_places=2, default=0, max_digits=10)
        ),
        migrations.AddField(
            model_name="subscription", name="discount", field=models.DecimalField(decimal_places=2, default=0, max_digits=10)
        ),
        migrations.AddField(
            model_name="subscription", name="tax", field=models.DecimalField(decimal_places=2, default=0, max_digits=10)
        ),
        migrations.AddField(
            model_name="subscription", name="final_amount", field=models.DecimalField(decimal_places=2, default=0, max_digits=10)
        ),
        migrations.AddField(
            model_name="subscription",
            name="billing_cycle",
            field=models.CharField(
                choices=[("MONTHLY", "Monthly"), ("YEARLY", "Yearly"), ("CUSTOM", "Custom")],
                default="MONTHLY", max_length=10,
            ),
        ),
        migrations.AddField(
            model_name="subscription", name="auto_renewal", field=models.BooleanField(default=True)
        ),
        migrations.AddField(
            model_name="subscription", name="cancellation_date", field=models.DateField(blank=True, null=True)
        ),
        migrations.AddField(
            model_name="subscription", name="is_complimentary", field=models.BooleanField(default=False)
        ),
        migrations.AddField(
            model_name="subscription", name="is_current", field=models.BooleanField(default=True)
        ),
        migrations.AddField(
            model_name="subscription", name="notes", field=models.TextField(blank=True)
        ),
        migrations.AlterField(
            model_name="subscription",
            name="status",
            field=models.CharField(
                choices=[
                    ("TRIAL", "Trial"), ("ACTIVE", "Active"), ("PAYMENT_DUE", "Payment Due"),
                    ("PAST_DUE", "Past Due"), ("SUSPENDED", "Suspended"), ("EXPIRED", "Expired"),
                    ("CANCELLED", "Cancelled"),
                ],
                default="TRIAL", max_length=12,
            ),
        ),
        migrations.AlterField(
            model_name="subscription",
            name="organization",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE, related_name="subscriptions",
                to="organizations.organization",
            ),
        ),
        migrations.RunPython(populate_subscription_fields, migrations.RunPython.noop),
        migrations.RemoveField(model_name="subscription", name="plan"),
        migrations.RemoveField(model_name="subscription", name="renewal_date"),
        migrations.RenameField(model_name="subscription", old_name="plan_new", new_name="plan"),
        migrations.AlterField(
            model_name="subscription",
            name="plan",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT, related_name="subscriptions", to="billing.plan",
            ),
        ),
        migrations.AlterField(
            model_name="subscription",
            name="subscription_id",
            field=models.CharField(editable=False, max_length=20, unique=True),
        ),
        migrations.AlterModelOptions(
            name="subscription", options={"ordering": ["-created_at"]}
        ),
        migrations.AddIndex(
            model_name="subscription",
            index=models.Index(fields=["organization", "is_current"], name="billing_sub_organiz_39725d_idx"),
        ),
    ]
