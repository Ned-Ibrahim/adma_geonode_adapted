from django import forms
from django.contrib import admin
from .models import Folder, File, Map, MapLayer, Tool, FolderGrant


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


def adapt_folder_paths():
    """{folder id: full path} for every ADAPT folder, built in one query instead of one per folder."""
    rows = Folder.objects.filter(third_party_source='adapt', deletion_in_progress=False).values_list('id', 'name', 'parent_id')
    parents = {pk: (name, parent_id) for pk, name, parent_id in rows}
    paths = {}

    def path(pk):
        if pk not in paths:
            name, parent_id = parents[pk]
            paths[pk] = f"{path(parent_id)}/{name}" if parent_id in parents else name
        return paths[pk]

    for pk in parents:
        path(pk)
    return paths


class AdaptFolderChoiceField(forms.ModelChoiceField):
    """Lists ADAPT folders by full path, so "Soils/2024" is told apart from "Grazing Systems/2024"."""

    def __init__(self, *args, **kwargs):
        self.paths = adapt_folder_paths()
        super().__init__(*args, **kwargs)
        self.queryset = Folder.objects.filter(pk__in=self.paths.keys())

    def _get_choices(self):
        ordered = sorted(self.paths.items(), key=lambda item: item[1].lower())
        return [('', self.empty_label)] + ordered

    choices = property(_get_choices, forms.ChoiceField._set_choices)


@admin.register(FolderGrant)
class FolderGrantAdmin(admin.ModelAdmin):
    list_display = ['folder_path', 'user', 'group', 'can_write', 'granted_by', 'created_at']
    list_filter = ['can_write', 'group']
    search_fields = ['folder__name', 'user__username', 'group__name']
    list_select_related = ['folder', 'user', 'group', 'granted_by']
    fields = ['folder', 'user', 'group', 'can_write']

    @admin.display(description='Folder', ordering='folder__name')
    def folder_path(self, obj):
        return obj.folder.get_full_path()

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == 'folder':
            kwargs['form_class'] = AdaptFolderChoiceField
            kwargs['help_text'] = 'The grant covers this folder, its files and every folder beneath it.'
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def save_model(self, request, obj, form, change):
        if not change:
            obj.granted_by = request.user
        super().save_model(request, obj, form, change)
