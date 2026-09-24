"""ADAPT folders are visible only through a FolderGrant.

The share is mounted with one account that can read everything, so these tests
are the whole of ADAPT access control: if they pass, a user sees exactly the
branches granted to them and nothing beside them.
"""
from importlib import import_module
from io import StringIO

from django.apps import apps
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management import CommandError, call_command
from django.test import TestCase
from rest_framework.authtoken.models import Token

from filemanager import permissions
from filemanager.models import File, Folder, FolderGrant
from filemanager.search_engine import search_engine

User = get_user_model()


def adapt_folder(name, parent, owner, **extra):
    return Folder.objects.create(
        name=name, parent=parent, owner=owner, is_third_party=True, third_party_source='adapt',
        third_party_id=f'adapt:{name}', **extra,
    )


def adapt_file(name, folder, **extra):
    # bulk_create skips File.save(), which would stat a file that does not exist.
    return File.objects.bulk_create([File(
        name=name, file=f'adapt/{name}', folder=folder, owner=folder.owner, file_size=10,
        is_third_party=True, third_party_source='adapt', third_party_id=f'adapt:{name}', **extra,
    )])[0]


class GrantTree(TestCase):
    """ADAPT/{Soils/{2024}, Grazing Systems, Flux Measurements} with one file in each."""

    @classmethod
    def setUpTestData(cls):
        cls.sync = User.objects.create_user('sync', password='pw')
        cls.alice = User.objects.create_user('alice', password='pw')
        cls.bob = User.objects.create_user('bob', password='pw')
        cls.carol = User.objects.create_user('carol', password='pw')
        cls.admin = User.objects.create_superuser('admin', password='pw')

        cls.root = adapt_folder('ADAPT', None, cls.sync)
        cls.soils = adapt_folder('Soils', cls.root, cls.sync)
        cls.soils_2024 = adapt_folder('2024', cls.soils, cls.sync)
        cls.grazing = adapt_folder('Grazing Systems', cls.root, cls.sync)
        cls.flux = adapt_folder('Flux Measurements', cls.root, cls.sync)

        cls.root_file = adapt_file('share_readme.txt', cls.root)
        cls.soils_file = adapt_file('soil_cores.csv', cls.soils)
        cls.soils_2024_file = adapt_file('cores_2024.csv', cls.soils_2024)
        cls.grazing_file = adapt_file('paddocks.csv', cls.grazing)
        cls.flux_file = adapt_file('tower.csv', cls.flux)

        FolderGrant.objects.create(folder=cls.soils, user=cls.alice)
        flux_team = Group.objects.create(name='flux-team')
        cls.carol.groups.add(flux_team)
        FolderGrant.objects.create(folder=cls.flux, group=flux_team, can_write=True)

    def fresh(self, user):
        # Grants are cached per user object, as they are per request in a view.
        return User.objects.get(pk=user.pk)


class PermissionRules(GrantTree):

    def test_a_grant_covers_the_folder_its_files_and_everything_beneath(self):
        alice = self.fresh(self.alice)
        for obj in (self.soils, self.soils_file, self.soils_2024, self.soils_2024_file):
            with self.subTest(obj=obj.name):
                self.assertTrue(permissions.can_read(alice, obj))

    def test_sibling_branches_and_their_files_stay_hidden(self):
        alice = self.fresh(self.alice)
        for obj in (self.grazing, self.grazing_file, self.flux, self.flux_file):
            with self.subTest(obj=obj.name):
                self.assertFalse(permissions.can_read(alice, obj))

    def test_folders_above_a_grant_open_for_navigation_but_not_their_files(self):
        alice = self.fresh(self.alice)
        self.assertTrue(permissions.can_read(alice, self.root))
        self.assertFalse(permissions.can_list_files(alice, self.root))
        self.assertFalse(permissions.can_read(alice, self.root_file))

    def test_no_grant_means_no_access(self):
        bob = self.fresh(self.bob)
        for obj in (self.root, self.root_file, self.soils, self.grazing_file):
            with self.subTest(obj=obj.name):
                self.assertFalse(permissions.can_read(bob, obj))

    def test_a_group_grant_reaches_every_member(self):
        carol = self.fresh(self.carol)
        self.assertTrue(permissions.can_read(carol, self.flux_file))
        self.assertFalse(permissions.can_read(carol, self.soils))

    def test_write_needs_a_write_grant(self):
        self.assertFalse(permissions.can_write(self.fresh(self.alice), self.soils_file))
        self.assertTrue(permissions.can_write(self.fresh(self.carol), self.flux_file))
        self.assertFalse(permissions.can_write(self.fresh(self.carol), self.root))

    def test_superusers_see_everything(self):
        admin = self.fresh(self.admin)
        for obj in (self.root, self.root_file, self.grazing_file):
            with self.subTest(obj=obj.name):
                self.assertTrue(permissions.can_read(admin, obj))

    def test_a_legacy_public_flag_opens_nothing(self):
        Folder.objects.filter(pk=self.grazing.pk).update(is_public=True)
        File.objects.filter(pk=self.grazing_file.pk).update(is_public=True)
        bob = self.fresh(self.bob)
        self.assertFalse(permissions.can_read(bob, Folder.objects.get(pk=self.grazing.pk)))
        self.assertFalse(permissions.can_read(bob, File.objects.get(pk=self.grazing_file.pk)))

    def test_ordinary_folders_keep_the_owner_or_public_rule(self):
        mine = Folder.objects.create(name='mine', owner=self.bob)
        shared = Folder.objects.create(name='shared', owner=self.bob, is_public=True)
        alice = self.fresh(self.alice)
        self.assertFalse(permissions.can_read(alice, mine))
        self.assertTrue(permissions.can_read(alice, shared))
        self.assertTrue(permissions.can_read(self.fresh(self.bob), mine))


class WebPages(GrantTree):

    def test_the_root_lists_only_the_granted_branch_and_no_files(self):
        self.client.force_login(self.alice)
        response = self.client.get(f'/folder/{self.root.id}/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Soils')
        self.assertNotContains(response, 'Grazing Systems')
        self.assertNotContains(response, 'Flux Measurements')
        self.assertNotContains(response, 'share_readme.txt')

    def test_a_granted_folder_lists_its_files(self):
        self.client.force_login(self.alice)
        response = self.client.get(f'/folder/{self.soils.id}/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'soil_cores.csv')
        self.assertContains(response, '2024')

    def test_an_ungranted_folder_redirects_to_the_dashboard(self):
        self.client.force_login(self.alice)
        response = self.client.get(f'/folder/{self.grazing.id}/')
        self.assertRedirects(response, '/dashboard/', fetch_redirect_response=False)

    def test_ungranted_files_are_not_found(self):
        self.client.force_login(self.alice)
        for path in (f'/file/{self.grazing_file.id}/download/', f'/file/{self.root_file.id}/download/'):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 404)

    def test_the_dashboard_shows_adapt_only_to_granted_users(self):
        self.client.force_login(self.bob)
        self.assertNotContains(self.client.get('/dashboard/'), f'/folder/{self.root.id}/')
        self.client.force_login(self.alice)
        self.assertContains(self.client.get('/dashboard/'), f'/folder/{self.root.id}/')

    def test_public_pages_never_serve_adapt_rows(self):
        Folder.objects.filter(pk=self.grazing.pk).update(is_public=True)
        File.objects.filter(pk=self.grazing_file.pk).update(is_public=True)
        self.client.force_login(self.bob)
        for path in (f'/public/folder/{self.grazing.id}/', f'/public/file/{self.grazing_file.id}/'):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 404)

    def test_search_never_returns_ungranted_adapt_rows(self):
        Folder.objects.filter(pk=self.grazing.pk).update(is_public=True)
        Folder.objects.create(name='Grazing notes', owner=self.alice, is_public=True)
        results = search_engine.search(self.fresh(self.bob), query='Grazing', content_type='folder')
        names = [r['name'] for r in results]
        self.assertIn('Grazing notes', names)
        self.assertNotIn('Grazing Systems', names)


class Search(GrantTree):

    def names(self, user, query, content_type):
        return {r['name'] for r in search_engine.search(self.fresh(user), query=query, content_type=content_type)}

    def test_a_granted_user_finds_files_anywhere_in_their_subtree(self):
        self.assertEqual(self.names(self.alice, 'cores', 'file'), {'soil_cores.csv', 'cores_2024.csv'})

    def test_files_outside_the_grant_are_never_found(self):
        for query in ('paddocks', 'tower', 'share_readme'):
            with self.subTest(query=query):
                self.assertEqual(self.names(self.alice, query, 'file'), set())

    def test_folders_found_are_the_granted_branch_and_the_way_to_it(self):
        self.assertEqual(self.names(self.alice, '2024', 'folder'), {'2024'})
        self.assertEqual(self.names(self.alice, 'Grazing', 'folder'), set())
        self.assertEqual(self.names(self.alice, 'ADAPT', 'folder'), {'ADAPT'})

    def test_a_group_grant_is_searchable_by_its_members(self):
        self.assertEqual(self.names(self.carol, 'tower', 'file'), {'tower.csv'})

    def test_no_grant_finds_no_adapt_rows(self):
        for query in ('cores', 'paddocks', 'ADAPT', 'Soils'):
            with self.subTest(query=query):
                self.assertEqual(self.names(self.bob, query, None), set())

    def test_superusers_find_everything(self):
        self.assertEqual(self.names(self.admin, 'csv', 'file'),
                         {'soil_cores.csv', 'cores_2024.csv', 'paddocks.csv', 'tower.csv'})

    def test_the_search_page_shows_granted_results_only(self):
        self.client.force_login(self.alice)
        response = self.client.get('/search/', {'q': 'csv'})
        self.assertContains(response, 'soil_cores.csv')
        self.assertNotContains(response, 'paddocks.csv')
        self.assertNotContains(response, 'tower.csv')


class TokenApi(GrantTree):

    def setUp(self):
        token = Token.objects.create(user=self.alice)
        self.client.defaults['HTTP_AUTHORIZATION'] = f'Token {token.key}'

    def test_folder_listing_shows_only_the_granted_branch(self):
        response = self.client.get('/api/v1/folders/', {'parent_id': str(self.root.id)})
        self.assertEqual(response.status_code, 200)
        self.assertIn('Soils', response.content.decode())
        self.assertNotIn('Grazing Systems', response.content.decode())

    def test_file_listing_of_a_navigation_only_folder_is_empty(self):
        response = self.client.get('/api/v1/files/', {'folder_id': str(self.root.id)})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('share_readme.txt', response.content.decode())

    def test_ungranted_download_is_refused(self):
        response = self.client.get(f'/api/v1/files/{self.grazing_file.id}/download/')
        self.assertEqual(response.status_code, 404)


class GrantCommand(GrantTree):

    def run_command(self, *args):
        out = StringIO()
        call_command('adapt_grants', *args, stdout=out)
        return out.getvalue()

    def test_grant_list_and_revoke(self):
        self.run_command('grant', 'bob', 'Soils/2024')
        self.assertTrue(permissions.can_read(self.fresh(self.bob), self.soils_2024_file))
        self.assertIn('bob', self.run_command('list'))
        self.run_command('revoke', 'bob', 'Soils/2024')
        self.assertFalse(permissions.can_read(self.fresh(self.bob), self.soils_2024_file))

    def test_granting_again_updates_instead_of_duplicating(self):
        self.run_command('grant', 'alice', 'Soils', '--write')
        self.assertEqual(FolderGrant.objects.filter(user=self.alice, folder=self.soils).count(), 1)
        self.assertTrue(permissions.can_write(self.fresh(self.alice), self.soils_file))

    def test_a_group_grant_creates_the_group(self):
        self.run_command('grant', 'group:grazing-team', 'Grazing Systems')
        self.assertTrue(Group.objects.filter(name='grazing-team').exists())

    def test_an_unknown_folder_names_the_folders_that_exist(self):
        with self.assertRaisesMessage(CommandError, 'Grazing Systems'):
            self.run_command('grant', 'bob', 'Grazing')


class MigrationMakesAdaptPrivate(GrantTree):

    def test_existing_public_adapt_rows_become_private(self):
        Folder.objects.filter(pk=self.grazing.pk).update(is_public=True)
        File.objects.filter(pk=self.grazing_file.pk).update(is_public=True)
        own = Folder.objects.create(name='mine', owner=self.bob, is_public=True)
        migration = import_module('filemanager.migrations.0015_folder_grants')
        migration.make_adapt_private(apps, None)
        self.assertFalse(Folder.objects.get(pk=self.grazing.pk).is_public)
        self.assertFalse(File.objects.get(pk=self.grazing_file.pk).is_public)
        self.assertTrue(Folder.objects.get(pk=own.pk).is_public)
