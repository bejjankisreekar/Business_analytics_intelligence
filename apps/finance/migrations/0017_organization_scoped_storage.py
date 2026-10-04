from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('organizations', '0016_organization_storage_mode'),
        ('finance', '0015_alter_bankaccount_options_alter_cashtransfer_options_and_more'),
    ]

    operations = [
        # Simple approach: just add the columns without constraints, populate, make NOT NULL

        # Add organization columns (nullable, no constraints yet)
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

        # Populate with default org (use raw SQL to avoid locks)
        migrations.RunSQL(
            "UPDATE finance_salesentry SET organization_id = (SELECT id FROM organizations_organization LIMIT 1) WHERE organization_id IS NULL;",
            "SELECT 1;"
        ),
        migrations.RunSQL(
            "UPDATE finance_expenseentry SET organization_id = (SELECT id FROM organizations_organization LIMIT 1) WHERE organization_id IS NULL;",
            "SELECT 1;"
        ),
        migrations.RunSQL(
            "UPDATE finance_purchaseentry SET organization_id = (SELECT id FROM organizations_organization LIMIT 1) WHERE organization_id IS NULL;",
            "SELECT 1;"
        ),
        migrations.RunSQL(
            "UPDATE finance_receivable SET organization_id = (SELECT id FROM organizations_organization LIMIT 1) WHERE organization_id IS NULL;",
            "SELECT 1;"
        ),
        migrations.RunSQL(
            "UPDATE finance_payable SET organization_id = (SELECT id FROM organizations_organization LIMIT 1) WHERE organization_id IS NULL;",
            "SELECT 1;"
        ),
        migrations.RunSQL(
            "UPDATE finance_cashtransfer SET organization_id = (SELECT id FROM organizations_organization LIMIT 1) WHERE organization_id IS NULL;",
            "SELECT 1;"
        ),
        migrations.RunSQL(
            "UPDATE finance_partnertransaction SET organization_id = (SELECT id FROM organizations_organization LIMIT 1) WHERE organization_id IS NULL;",
            "SELECT 1;"
        ),
        migrations.RunSQL(
            "UPDATE finance_financesettings SET organization_id = (SELECT id FROM organizations_organization LIMIT 1) WHERE organization_id IS NULL;",
            "SELECT 1;"
        ),
        migrations.RunSQL(
            "UPDATE finance_category SET organization_id = (SELECT id FROM organizations_organization LIMIT 1) WHERE organization_id IS NULL;",
            "SELECT 1;"
        ),
        migrations.RunSQL(
            "UPDATE finance_subcategory SET organization_id = (SELECT id FROM organizations_organization LIMIT 1) WHERE organization_id IS NULL;",
            "SELECT 1;"
        ),
        migrations.RunSQL(
            "UPDATE finance_customer SET organization_id = (SELECT id FROM organizations_organization LIMIT 1) WHERE organization_id IS NULL;",
            "SELECT 1;"
        ),
        migrations.RunSQL(
            "UPDATE finance_vendor SET organization_id = (SELECT id FROM organizations_organization LIMIT 1) WHERE organization_id IS NULL;",
            "SELECT 1;"
        ),
        migrations.RunSQL(
            "UPDATE finance_bankaccount SET organization_id = (SELECT id FROM organizations_organization LIMIT 1) WHERE organization_id IS NULL;",
            "SELECT 1;"
        ),
        migrations.RunSQL(
            "UPDATE finance_partner SET organization_id = (SELECT id FROM organizations_organization LIMIT 1) WHERE organization_id IS NULL;",
            "SELECT 1;"
        ),

        # Make NOT NULL
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

        # Add constraints in a separate pass (avoid heavy locking)
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
