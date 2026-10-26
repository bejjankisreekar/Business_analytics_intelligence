from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('finance', '0018_alter_bankaccount_organization_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='financesettings',
            name='bulk_column_labels',
            field=models.TextField(blank=True, default=''),
        ),
    ]
