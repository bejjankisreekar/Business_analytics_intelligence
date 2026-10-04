import django.db.models.deletion
from django.db import migrations, models


def populate_organization(apps, schema_editor):
    """Populate organization_id for existing FinanceSettings rows."""
    FinanceSettings = apps.get_model('finance', 'FinanceSettings')
    Organization = apps.get_model('organizations', 'Organization')

    # Get the first organization (or create one if none exist)
    org = Organization.objects.first()
    if org:
        FinanceSettings.objects.all().update(organization_id=org.id)


def reverse_populate(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('organizations', '0016_organization_storage_mode'),
        ('finance', '0015_alter_bankaccount_options_alter_cashtransfer_options_and_more'),
    ]

    operations = [
        # --- plain organization FK, no unique-name constraint involved ---
        migrations.AddField(
            model_name='salesentry',
            name='organization',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='+', to='organizations.organization'),
        ),
        migrations.AddField(
            model_name='expenseentry',
            name='organization',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='+', to='organizations.organization'),
        ),
        migrations.AddField(
            model_name='purchaseentry',
            name='organization',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='+', to='organizations.organization'),
        ),
        migrations.AddField(
            model_name='receivable',
            name='organization',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='+', to='organizations.organization'),
        ),
        migrations.AddField(
            model_name='payable',
            name='organization',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='+', to='organizations.organization'),
        ),
        migrations.AddField(
            model_name='cashtransfer',
            name='organization',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='+', to='organizations.organization'),
        ),
        migrations.AddField(
            model_name='partnertransaction',
            name='organization',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='+', to='organizations.organization'),
        ),
        migrations.AddField(
            model_name='financesettings',
            name='organization',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='organizations.organization', unique=True),
        ),
        migrations.RunPython(populate_organization, reverse_populate),
        migrations.AlterField(
            model_name='financesettings',
            name='organization',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='+', to='organizations.organization', unique=True),
        ),

        # --- organization FK + rename the old global-unique constraint to be org-scoped ---
        migrations.AddField(
            model_name='category',
            name='organization',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='+', to='organizations.organization'),
        ),
        migrations.RemoveConstraint(
            model_name='category',
            name='finance_category_kind_name_uniq',
        ),
        migrations.AddConstraint(
            model_name='category',
            constraint=models.UniqueConstraint(fields=('organization', 'kind', 'name'), name='finance_category_org_kind_name_uniq'),
        ),

        migrations.AddField(
            model_name='subcategory',
            name='organization',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='+', to='organizations.organization'),
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

        migrations.AddField(
            model_name='customer',
            name='organization',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='+', to='organizations.organization'),
        ),
        migrations.RemoveConstraint(
            model_name='customer',
            name='finance_customer_name_uniq',
        ),
        migrations.AddConstraint(
            model_name='customer',
            constraint=models.UniqueConstraint(fields=('organization', 'name'), name='finance_customer_org_name_uniq'),
        ),

        migrations.AddField(
            model_name='vendor',
            name='organization',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='+', to='organizations.organization'),
        ),
        migrations.RemoveConstraint(
            model_name='vendor',
            name='finance_vendor_name_uniq',
        ),
        migrations.AddConstraint(
            model_name='vendor',
            constraint=models.UniqueConstraint(fields=('organization', 'name'), name='finance_vendor_org_name_uniq'),
        ),

        migrations.AddField(
            model_name='bankaccount',
            name='organization',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='+', to='organizations.organization'),
        ),
        migrations.RemoveConstraint(
            model_name='bankaccount',
            name='finance_bankaccount_name_uniq',
        ),
        migrations.AddConstraint(
            model_name='bankaccount',
            constraint=models.UniqueConstraint(fields=('organization', 'name'), name='finance_bankaccount_org_name_uniq'),
        ),

        migrations.AddField(
            model_name='partner',
            name='organization',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='+', to='organizations.organization'),
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
