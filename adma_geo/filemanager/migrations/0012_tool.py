# Generated migration for Tool model

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('filemanager', '0011_remove_embedding_fields'),
    ]

    operations = [
        migrations.CreateModel(
            name='Tool',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('name', models.CharField(help_text='Display name of the tool', max_length=255)),
                ('slug', models.SlugField(help_text="URL-friendly identifier (e.g., 'seeding-tool')", max_length=100, unique=True)),
                ('description', models.TextField(blank=True, help_text='Detailed description of what the tool does')),
                ('short_description', models.CharField(blank=True, help_text='Brief description for list views', max_length=500)),
                ('is_system_tool', models.BooleanField(default=False, help_text='System tools are built-in and cannot be deleted by users')),
                ('is_public', models.BooleanField(default=False, help_text='Public tools are visible to all users')),
                ('is_active', models.BooleanField(default=True, help_text='Inactive tools are hidden from the tools list')),
                ('category', models.CharField(choices=[('gis_processing', 'GIS Processing'), ('format_conversion', 'Format Conversion'), ('analysis', 'Analysis'), ('visualization', 'Visualization'), ('data_management', 'Data Management'), ('other', 'Other')], default='other', help_text='Category for organizing tools', max_length=50)),
                ('icon', models.CharField(default='fa-tools', help_text="FontAwesome icon class (e.g., 'fa-seedling')", max_length=100)),
                ('icon_color', models.CharField(choices=[('primary', 'Primary (Blue)'), ('secondary', 'Secondary (Gray)'), ('success', 'Success (Green)'), ('danger', 'Danger (Red)'), ('warning', 'Warning (Yellow)'), ('info', 'Info (Cyan)'), ('dark', 'Dark')], default='primary', help_text='Bootstrap color class for the icon', max_length=20)),
                ('url_name', models.CharField(blank=True, help_text="Django URL name for the tool page (e.g., 'filemanager:seeding_tool')", max_length=255)),
                ('celery_task_name', models.CharField(blank=True, help_text="Celery task name for async execution (e.g., 'filemanager.tasks.run_seeding_tool_task')", max_length=255)),
                ('input_config', models.JSONField(blank=True, default=dict, help_text='JSON configuration for tool inputs (file types, required fields, etc.)')),
                ('output_config', models.JSONField(blank=True, default=dict, help_text='JSON configuration for tool outputs (file types, naming conventions, etc.)')),
                ('status', models.CharField(choices=[('available', 'Available'), ('coming_soon', 'Coming Soon'), ('beta', 'Beta'), ('deprecated', 'Deprecated'), ('maintenance', 'Under Maintenance')], default='available', help_text='Current status of the tool', max_length=20)),
                ('version', models.CharField(default='1.0.0', help_text='Tool version number', max_length=50)),
                ('usage_count', models.PositiveIntegerField(default=0, help_text='Number of times this tool has been executed')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('owner', models.ForeignKey(blank=True, help_text='Owner of the tool (null for system tools)', null=True, on_delete=django.db.models.deletion.CASCADE, related_name='tools', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Tool',
                'verbose_name_plural': 'Tools',
                'ordering': ['category', 'name'],
            },
        ),
    ]
