from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('EmailMarketing', '0006_remove_emailtemplate_editor_json_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='emailtemplate',
            name='editor_json',
            field=models.TextField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='emailtemplate',
            name='is_system',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='emailtemplate',
            name='preview_text',
            field=models.CharField(blank=True, max_length=500, null=True),
        ),
        migrations.AddField(
            model_name='emailtemplate',
            name='subject',
            field=models.CharField(blank=True, max_length=500, null=True),
        ),
    ]
