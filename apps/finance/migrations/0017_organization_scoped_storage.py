import django.db.models.deletion
from django.db import migrations, models


def populate_organization(apps, schema_editor):
    """Populate organization_id for existing records."""
    Organization = apps.get_model('organizations', 'Organization')

    # Get or create the first organization
    org, _ = Organization.objects.get_or_create(
        name='Default',
        defaults={'slug': 'default'}
    )
    org_id = org.id

    # Bulk update all models with a single org_id to avoid long locks
    models_to_update = [
        'FinanceSettings', 'SalesEntry', 'ExpenseEntry', 'PurchaseEntry',
        'Receivable', 'Payable', 'CashTransfer', 'PartnerTransaction',
        'Category', 'SubCategory', 'Customer', 'Vendor', 'BankAccount', 'Partner'
    ]

    for model_name in models_to_update:
        try:
            model = apps.get_model('finance', model_name)
            model.objects.all().update(organization_id=org_id)
        except LookupError:
            pass


def reverse_populate(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('organizations', '0016_organization_storage_mode'),
        ('finance', '0015_alter_bankaccount_options_alter_cashtransfer_options_and_more'),
    ]

    operations = [
        # Add organization FK to all models (nullable for now)
        migrations.AddField(
            model_name='salesentry',
            name='organization',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, to='organizations.organization'),
        ),
        migrations.AddField(
            model_name='expenseentry',
            name='organization',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, to='organizations.organization'),
        ),
        migrations.AddField(
            model_name='purchaseentry',
            name='organization',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, to='organizations.organization'),
        ),
        migrations.AddField(
            model_name='receivable',
            name='organization',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, to='organizations.organization'),
        ),
        migrations.AddField(
            model_name='payable',
            name='organization',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, to='organizations.organization'),
        ),
        migrations.AddField(
            model_name='cashtransfer',
            name='organization',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, to='organizations.organization'),
        ),
        migrations.AddField(
            model_name='partnertransaction',
            name='organization',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, to='organizations.organization'),
        ),
        migrations.AddField(
            model_name='financesettings',
            name='organization',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, to='organizations.organization'),
        ),
        migrations.AddField(
            model_name='category',
            name='organization',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, to='organizations.organization'),
        ),
        migrations.AddField(
            model_name='subcategory',
            name='organization',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, to='organizations.organization'),
        ),
        migrations.AddField(
            model_name='customer',
            name='organization',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, to='organizations.organization'),
        ),
        migrations.AddField(
            model_name='vendor',
            name='organization',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, to='organizations.organization'),
        ),
        migrations.AddField(
            model_name='bankaccount',
            name='organization',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, to='organizations.organization'),
        ),
        migrations.AddField(
            model_name='partner',
            name='organization',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, to='organizations.organization'),
        ),

        # Populate organization_id
        migrations.RunPython(populate_organization, reverse_populate),

        # Make organization NOT NULL
        migrations.AlterField(
            model_name='salesentry',
            name='organization',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='organizations.organization'),
        ),
        migrations.AlterField(
            model_name='expenseentry',
            name='organization',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='organizations.organization'),
        ),
        migrations.AlterField(
            model_name='purchaseentry',
            name='organization',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='organizations.organization'),
        ),
        migrations.AlterField(
            model_name='receivable',
            name='organization',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='organizations.organization'),
        ),
        migrations.AlterField(
            model_name='payable',
            name='organization',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='organizations.organization'),
        ),
        migrations.AlterField(
            model_name='cashtransfer',
            name='organization',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='organizations.organization'),
        ),
        migrations.AlterField(
            model_name='partnertransaction',
            name='organization',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='organizations.organization'),
        ),
        migrations.AlterField(
            model_name='financesettings',
            name='organization',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='organizations.organization', unique=True),
        ),
        migrations.AlterField(
            model_name='category',
            name='organization',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='organizations.organization'),
        ),
        migrations.AlterField(
            model_name='subcategory',
            name='organization',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='organizations.organization'),
        ),
        migrations.AlterField(
            model_name='customer',
            name='organization',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='organizations.organization'),
        ),
        migrations.AlterField(
            model_name='vendor',
            name='organization',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='organizations.organization'),
        ),
        migrations.AlterField(
            model_name='bankaccount',
            name='organization',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='organizations.organization'),
        ),
        migrations.AlterField(
            model_name='partner',
            name='organization',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='organizations.organization'),
        ),

        # Update constraints (separate from field additions to avoid locks)
        migrations.RemoveConstraint(
            model_name='category',
            name='finance_category_kind_name_uniq',
        ),
        migrations.AddConstraint(
            model_name='category',
            constraint=models.UniqueConstraint(fields=('organization', 'kind', 'name'), name='finance_category_org_kind_name_uniq'),
        ),

        migrations.RemoveConstraint(
            model_name='subcategory',
            name='finance_subcategory_category_parent_name_uniq',
        ),
        migrations.AddConstraint(
            model_name='subcategory',
            constraint=models.UniqueConstraint(
                fields=('organization', 'category', 'parent', 'name'), name='finance_subcategory_org_category_parent_name_uniq'
            ),
        ),

        migrations.RemoveConstraint(
            model_name='customer',
            name='finance_customer_name_uniq',
        ),
        migrations.AddConstraint(
            model_name='customer',
            constraint=models.UniqueConstraint(fields=('organization', 'name'), name='finance_customer_org_name_uniq'),
        ),

        migrations.RemoveConstraint(
            model_name='vendor',
            name='finance_vendor_name_uniq',
        ),
        migrations.AddConstraint(
            model_name='vendor',
            constraint=models.UniqueConstraint(fields=('organization', 'name'), name='finance_vendor_org_name_uniq'),
        ),

        migrations.RemoveConstraint(
            model_name='bankaccount',
            name='finance_bankaccount_name_uniq',
        ),
        migrations.AddConstraint(
            model_name='bankaccount',
            constraint=models.UniqueConstraint(fields=('organization', 'name'), name='finance_bankaccount_org_name_uniq'),
        ),

        migrations.RemoveConstraint(
            model_name='partner',
            name='finance_partner_name_uniq',
        ),
        migrations.AddConstraint(
            model_name='partner',
            constraint=models.UniqueConstraint(fields=('organization', 'name'), name='finance_partner_org_name_uniq'),
        ),
    ]
