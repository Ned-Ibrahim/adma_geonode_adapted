"""How file sizes and file types read on the pages."""
from django.test import SimpleTestCase

from filemanager.models import File
from filemanager.templatetags.file_display import file_type_label


class FileDisplayTests(SimpleTestCase):
    def test_bytes_are_whole_and_larger_units_keep_one_decimal(self):
        for size, shown in ((0, '0 B'), (5, '5 B'), (236, '236 B'), (1023, '1023 B'),
                            (1024, '1.0 KB'), (98304, '96.0 KB'), (int(2.5 * 1024 ** 2), '2.5 MB'),
                            (3 * 1024 ** 3, '3.0 GB')):
            self.assertEqual(File.describe_size(size), shown)

    def test_file_types_read_as_initialisms_or_words(self):
        for file_type, shown in (('gis', 'GIS'), ('csv', 'CSV'), ('spreadsheet', 'Spreadsheet'),
                                 ('image', 'Image'), ('other', 'Other'), ('', '')):
            self.assertEqual(file_type_label(file_type), shown)
