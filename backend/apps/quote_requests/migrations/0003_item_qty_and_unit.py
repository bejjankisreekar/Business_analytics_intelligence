from decimal import Decimal

from django.db import migrations, models


def kg_pc_to_qty_unit(apps, schema_editor):
    """Old rows had separate kg and pc boxes. Keep the kg amount when both were filled."""
    # Always the database being migrated: this project migrates more than one (dev and prod aliases).
    alias = schema_editor.connection.alias
    Item = apps.get_model("quote_requests", "QuoteRequestItem")
    for item in Item.objects.using(alias).all():
        if item.qty_kg is not None:
            item.qty, item.unit = item.qty_kg, "kg"
        elif item.qty_pc is not None:
            item.qty, item.unit = item.qty_pc, "pc"
        else:
            item.qty, item.unit = Decimal("1"), "pc"
        item.save(using=alias, update_fields=["qty", "unit"])


class Migration(migrations.Migration):

    dependencies = [
        ("quote_requests", "0002_productsuggestion"),
    ]

    operations = [
        migrations.AddField(
            model_name="quoterequestitem",
            name="qty",
            field=models.DecimalField(decimal_places=3, max_digits=10, null=True),
        ),
        migrations.AddField(
            model_name="quoterequestitem",
            name="unit",
            field=models.CharField(default="kg", max_length=10),
        ),
        migrations.RunPython(kg_pc_to_qty_unit, migrations.RunPython.noop),
        migrations.RemoveField(model_name="quoterequestitem", name="qty_kg"),
        migrations.RemoveField(model_name="quoterequestitem", name="qty_pc"),
        migrations.AlterField(
            model_name="quoterequestitem",
            name="qty",
            field=models.DecimalField(decimal_places=3, max_digits=10),
        ),
        migrations.AlterField(
            model_name="quoterequestitem",
            name="unit",
            field=models.CharField(
                choices=[
                    ("kg", "kg"), ("g", "g"), ("pc", "pc"), ("dozen", "dozen"), ("litre", "litre"),
                    ("ml", "ml"), ("packet", "packet"), ("box", "box"), ("bottle", "bottle"),
                    ("bag", "bag"), ("plate", "plate"), ("portion", "portion"), ("metre", "metre"),
                ],
                default="kg",
                max_length=10,
            ),
        ),
    ]
