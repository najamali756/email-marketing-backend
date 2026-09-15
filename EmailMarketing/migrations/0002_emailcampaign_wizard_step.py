from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('EmailMarketing', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='emailcampaign',
            name='wizard_step',
            field=models.IntegerField(default=1),
        ),
    ]
