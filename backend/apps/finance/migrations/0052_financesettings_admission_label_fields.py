from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('finance', '0051_common_options_and_values'),
    ]

    operations = [
        migrations.AddField(
            model_name='financesettings',
            name='admission_label_fields',
            field=models.TextField(blank=True, default=''),
        ),
    ]
