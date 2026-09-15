from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('Accounts', '0002_remove_store_sendgrid_api_key_remove_store_smtp_host_and_more'),
    ]

    operations = [
        migrations.AlterUniqueTogether(
            name='store',
            unique_together=set(),
        ),
    ]
