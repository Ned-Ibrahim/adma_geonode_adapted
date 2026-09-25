"""Folder counts shown next to an ADAPT folder cover only what the viewer may see.

A user granted one branch of ADAPT opens the root to navigate down, but the
root's full size (thousands of folders, a million files) is not theirs to know.
"""
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from filemanager.models import File, Folder, FolderGrant

from .test_folder_grants import adapt_file, adapt_folder

User = get_user_model()


class VisibleCountsTree(TestCase):
    """ADAPT/{Soils/{2024, 2025}, Grazing, Flux, Weather} with cached totals as recompute_folder_sizes leaves them."""

    @classmethod
    def setUpTestData(cls):
        cls.sync = User.objects.create_user('sync', password='pw')
        cls.alice = User.objects.create_user('alice', password='pw')
        cls.admin = User.objects.create_superuser('admin', password='pw')

        cls.root = adapt_folder('ADAPT', None, cls.sync, cached_total_file_count=1000)
        cls.soils = adapt_folder('Soils', cls.root, cls.sync, cached_total_file_count=70)
        cls.soils_2024 = adapt_folder('2024', cls.soils, cls.sync, cached_total_file_count=40)
        cls.soils_2025 = adapt_folder('2025', cls.soils, cls.sync, cached_total_file_count=29)
        for name in ('Grazing', 'Flux', 'Weather'):
            adapt_folder(name, cls.root, cls.sync, cached_total_file_count=300)
        adapt_file('share_readme.txt', cls.root)
        adapt_file('soil_cores.csv', cls.soils)
        adapt_file('cores_2024.csv', cls.soils_2024)

    def login(self, user):
        self.client.force_login(user)

    def dashboard(self):
        return self.client.get('/dashboard/').content.decode()


class DashboardRow(VisibleCountsTree):

    def test_a_user_granted_one_branch_sees_only_that_branch_in_the_adapt_row(self):
        FolderGrant.objects.create(folder=self.soils, user=self.alice)
        self.login(self.alice)
        page = self.dashboard()
        self.assertIn('1 folder, 70 files', page)
        self.assertNotIn('4 folders', page)
        self.assertNotIn('1000 files', page)

    def test_a_grant_two_levels_down_counts_only_the_granted_subtree(self):
        FolderGrant.objects.create(folder=self.soils_2024, user=self.alice)
        self.login(self.alice)
        self.assertIn('1 folder, 40 files', self.dashboard())

    def test_grants_nested_inside_another_grant_are_not_counted_twice(self):
        FolderGrant.objects.create(folder=self.soils, user=self.alice)
        FolderGrant.objects.create(folder=self.soils_2024, user=self.alice)
        self.login(self.alice)
        self.assertIn('1 folder, 70 files', self.dashboard())

    def test_a_grant_on_the_root_shows_the_whole_share(self):
        FolderGrant.objects.create(folder=self.root, user=self.alice)
        self.login(self.alice)
        self.assertIn('4 folders, 1000 files', self.dashboard())

    def test_superusers_see_the_whole_share(self):
        self.login(self.admin)
        self.assertIn('4 folders, 1000 files', self.dashboard())

    def test_the_row_costs_the_same_queries_however_big_the_tree(self):
        FolderGrant.objects.create(folder=self.soils_2024, user=self.alice)
        self.login(self.alice)

        def queries():
            with CaptureQueriesContext(connection) as ctx:
                self.dashboard()
            return len(ctx.captured_queries)

        before = queries()
        for i in range(30):
            adapt_folder(f'Extra {i}', self.root, self.sync)
            adapt_folder(f'Extra {i} child', self.soils, self.sync)
        self.assertEqual(queries(), before)


class FolderPageRows(VisibleCountsTree):

    def test_a_folder_on_the_way_to_a_grant_shows_only_the_way_down(self):
        FolderGrant.objects.create(folder=self.soils_2024, user=self.alice)
        self.login(self.alice)
        page = self.client.get(f'/folder/{self.root.pk}/').content.decode()
        # The Soils row: one visible subfolder (2024), none of the files beside it.
        self.assertRegex(page, r'0 files • 1 folder\s*<')
        self.assertNotIn('2 folders', page)

    def test_a_granted_folder_shows_its_real_counts(self):
        FolderGrant.objects.create(folder=self.soils, user=self.alice)
        self.login(self.alice)
        page = self.client.get(f'/folder/{self.root.pk}/').content.decode()
        self.assertRegex(page, r'1 file • 2 folders\s*<')


    def test_search_results_count_only_the_way_down(self):
        FolderGrant.objects.create(folder=self.soils_2024, user=self.alice)
        self.login(self.alice)
        page = self.client.get('/search/', {'q': 'Soils'}).content.decode()
        self.assertRegex(page, r'0 files • 1 folder\s*<')
        self.assertNotIn('2 folders', page)
        self.assertNotIn('1 file ', page)


class Pluralization(VisibleCountsTree):

    def test_one_folder_is_not_called_folders(self):
        FolderGrant.objects.create(folder=self.soils, user=self.alice)
        self.login(self.alice)
        page = self.dashboard()
        self.assertIn('>1 folder, 0 files<', page)
        self.assertNotIn('1 folders', page)
        page = self.client.get(f'/folder/{self.root.pk}/').content.decode()
        self.assertIn('(1 folder, 0 files)', page)
        self.assertNotIn('1 folders', page)

    def test_own_folders_count_files_in_the_singular(self):
        mine = Folder.objects.create(name='Mine', owner=self.alice)
        File.objects.bulk_create([File(name='x.csv', file='u/x.csv', folder=mine, owner=self.alice, file_size=1)])
        self.login(self.alice)
        page = self.dashboard()
        self.assertIn('1 file<', page)
        self.assertNotIn('1 files', page)

