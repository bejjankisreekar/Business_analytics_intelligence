from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('organizations', '0015_remove_legacy_tenant_db_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='organization',
            name='storage_mode',
            field=models.CharField(
                choices=[('GOOGLE_SHEETS', "Your own Google Drive"), ('OUR_DATABASE', "Prism Pulse's own database")],
                default='GOOGLE_SHEETS',
                max_length=20,
            ),
        ),
    ]
