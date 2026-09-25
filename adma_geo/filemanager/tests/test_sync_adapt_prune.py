"""sync_adapt --prune removes rows for files that left the share, at any size.

The share on lagnr-adapt holds about 1.3 million files. The first prune sent every
seen id to Postgres in one query and crashed; these tests pin the replacement.
"""
import re
import shutil
import tempfile
from io import StringIO
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from filemanager.management.commands.sync_adapt import BATCH
from filemanager.models import File, Folder

User = get_user_model()

UUID = re.compile(r'[0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12}')


class PruneTest(TestCase):

    def setUp(self):
        self.mount = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.mount)
        owner = User.objects.create_user('sync')
        self.root = Folder.objects.create(
            name='ADAPT', owner=owner, is_third_party=True, third_party_source='adapt')

    def touch(self, rel):
        path = self.mount / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('x')

    def sync(self, *extra):
        call_command('sync_adapt', '--once', '--no-recompute', '--mount', str(self.mount),
                     *extra, stdout=StringIO())

    def paths(self):
        return sorted(File.objects.filter(third_party_source='adapt').values_list('third_party_id', flat=True))

    def test_prune_removes_deleted_files_folders_and_newly_excluded_paths(self):
        for rel in ('Soils/keep.csv', 'Soils/gone.csv', 'Soils/Old/inside.csv', 'Photos/cam/1.jpg'):
            self.touch(rel)
        self.sync()
        self.assertEqual(len(self.paths()), 4)

        (self.mount / 'Soils/gone.csv').unlink()
        shutil.rmtree(self.mount / 'Soils/Old')
        self.sync('--prune', '--exclude', 'Photos/cam')

        self.assertEqual(self.paths(), ['Soils/keep.csv'])
        names = set(Folder.objects.filter(third_party_source='adapt').values_list('name', flat=True))
        self.assertEqual(names, {'ADAPT', 'Soils', 'Photos'})

    def test_a_second_run_without_changes_prunes_nothing(self):
        self.touch('Soils/keep.csv')
        self.sync()
        out = StringIO()
        call_command('sync_adapt', '--once', '--prune', '--no-recompute', '--mount', str(self.mount), stdout=out)
        self.assertIn('Pruned files:     0', out.getvalue())
        self.assertEqual(self.paths(), ['Soils/keep.csv'])

    def test_a_large_prune_never_sends_more_than_one_batch_of_ids_per_query(self):
        stale = BATCH * 2 + 500
        for i in range(stale):
            self.touch(f'Bulk/f{i}.csv')
        self.touch('Soils/keep.csv')
        self.sync()
        shutil.rmtree(self.mount / 'Bulk')

        with CaptureQueriesContext(connection) as ctx:
            self.sync('--prune')

        self.assertEqual(self.paths(), ['Soils/keep.csv'])
        widest = max(len(UUID.findall(q['sql'])) for q in ctx.captured_queries)
        self.assertLessEqual(widest, BATCH + 1)
