from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('Accounts', '0003_alter_store_unique_together'),
    ]

    operations = [
        migrations.AddField(
            model_name='contact',
            name='raw_data',
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
