# Generated migration for third-party integration fields

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('filemanager', '0012_tool'),
    ]

    operations = [
        # Add third-party fields to Folder model
        migrations.AddField(
            model_name='folder',
            name='is_third_party',
            field=models.BooleanField(default=False, help_text='True if this folder is from a third-party platform'),
        ),
        migrations.AddField(
            model_name='folder',
            name='third_party_source',
            field=models.CharField(blank=True, help_text="Name of the third-party platform (e.g., 'Google Drive', 'Dropbox', 'AWS S3')", max_length=100, null=True),
        ),
        migrations.AddField(
            model_name='folder',
            name='third_party_id',
            field=models.CharField(blank=True, help_text='Unique identifier from the third-party platform', max_length=255, null=True),
        ),
        
        # Add third-party fields to File model
        migrations.AddField(
            model_name='file',
            name='is_third_party',
            field=models.BooleanField(default=False, help_text='True if this file is from a third-party platform'),
        ),
        migrations.AddField(
            model_name='file',
            name='third_party_source',
            field=models.CharField(blank=True, help_text="Name of the third-party platform (e.g., 'Google Drive', 'Dropbox', 'AWS S3')", max_length=100, null=True),
        ),
        migrations.AddField(
            model_name='file',
            name='third_party_id',
            field=models.CharField(blank=True, help_text='Unique identifier from the third-party platform', max_length=255, null=True),
        ),
        migrations.AddField(
            model_name='file',
            name='third_party_url',
            field=models.URLField(blank=True, help_text='URL to the file on the third-party platform', max_length=500, null=True),
        ),
    ]
