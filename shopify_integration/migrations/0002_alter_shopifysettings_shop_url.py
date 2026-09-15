from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('shopify_integration', '0001_initial'),
    ]

    operations = [
        migrations.AlterField(
            model_name='shopifysettings',
            name='shop_url',
            field=models.CharField(blank=True, max_length=500, null=True),
        ),
    ]
