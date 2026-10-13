from django import template

register = template.Library()

# File.file_type values that are initialisms; the others read as words ('Spreadsheet').
TYPE_LABELS = {'gis': 'GIS', 'csv': 'CSV'}


@register.filter
def file_type_label(file_type):
    """A File.file_type as people read it: 'GIS', 'CSV', 'Image'.

    Usage: {{ file.file_type|file_type_label }}
    """
    if not file_type:
        return ''
    return TYPE_LABELS.get(file_type.lower(), file_type.title())

