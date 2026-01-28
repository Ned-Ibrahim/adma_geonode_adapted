from django.contrib import admin
from .models import Folder, File, Map, MapLayer, Tool


@admin.register(Folder)
class FolderAdmin(admin.ModelAdmin):
    list_display = ['name', 'owner', 'parent', 'is_public', 'created_at']
    list_filter = ['is_public', 'created_at']
    search_fields = ['name']
    raw_id_fields = ['parent', 'owner']


@admin.register(File)
class FileAdmin(admin.ModelAdmin):
    list_display = ['name', 'owner', 'folder', 'file_type', 'file_size', 'is_public', 'created_at']
    list_filter = ['file_type', 'is_public', 'created_at']
    search_fields = ['name']
    raw_id_fields = ['folder', 'owner']
    readonly_fields = ['file_size', 'file_type', 'mime_type']


@admin.register(Map)
class MapAdmin(admin.ModelAdmin):
    list_display = ['name', 'owner', 'is_public', 'layer_count', 'created_at', 'updated_at']
    list_filter = ['is_public', 'created_at']
    search_fields = ['name', 'description']
    raw_id_fields = ['owner']
    readonly_fields = ['geoserver_layer_group_name', 'created_at', 'updated_at']


@admin.register(MapLayer)
class MapLayerAdmin(admin.ModelAdmin):
    list_display = ['file', 'map', 'layer_order', 'opacity', 'is_visible', 'added_at']
    list_filter = ['is_visible', 'added_at']
    search_fields = ['file__name', 'map__name']
    raw_id_fields = ['map', 'file']


@admin.register(Tool)
class ToolAdmin(admin.ModelAdmin):
    list_display = ['name', 'slug', 'category', 'status', 'is_system_tool', 'is_public', 'is_active', 'usage_count', 'version']
    list_filter = ['category', 'status', 'is_system_tool', 'is_public', 'is_active']
    search_fields = ['name', 'slug', 'description', 'short_description']
    raw_id_fields = ['owner']
    readonly_fields = ['id', 'usage_count', 'created_at', 'updated_at']
    prepopulated_fields = {'slug': ('name',)}
    
    fieldsets = (
        ('Basic Information', {
            'fields': ('name', 'slug', 'short_description', 'description')
        }),
        ('Ownership & Visibility', {
            'fields': ('owner', 'is_system_tool', 'is_public', 'is_active')
        }),
        ('Categorization & Display', {
            'fields': ('category', 'status', 'icon', 'icon_color', 'version')
        }),
        ('Execution Configuration', {
            'fields': ('url_name', 'celery_task_name', 'input_config', 'output_config'),
            'classes': ('collapse',)
        }),
        ('Statistics & Metadata', {
            'fields': ('usage_count', 'id', 'created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )
