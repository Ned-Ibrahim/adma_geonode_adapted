"""Two users, two teams, one ADAPT tree: who sees what, through the module, the views and the API."""
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.authtoken.models import Token

from filemanager import permissions
from filemanager.models import DirectoryIdentity, File, Folder, FolderAcl
from filemanager.tests import acl_fixtures as fx

User = get_user_model()


def give_identity(user, sid, groups):
    return DirectoryIdentity.objects.create(user=user, dn=f'CN={user.username}', sid=sid,
                                            group_sids=list(groups), fetched_at=timezone.now())


def give_acl(folder, blob):
    return FolderAcl.objects.create(folder=folder, descriptor=blob, fetched_at=timezone.now(), ace_count=0)


class AdaptTreeTestCase(TestCase):
    """ADAPT/  (everyone on the share may list it)
         Flux Measurements/   (flux team: modify)
           2024/              (no descriptor of its own: inherits Flux)
             tower.csv
         Soils/               (soils team: read only)
    """

    def setUp(self):
        self.sync_user = User.objects.create_user('syncbot', password='x')
        self.jane = User.objects.create_user('jane', password='x')   # flux team
        self.bob = User.objects.create_user('bob', password='x')     # soils team
        self.nobody = User.objects.create_user('nobody', password='x')  # never logged in via LDAP
        self.admin = User.objects.create_superuser('admin', 'a@x', 'x')
        give_identity(self.jane, fx.JANE, [fx.FLUX_TEAM])
        give_identity(self.bob, fx.BOB, [fx.SOILS_TEAM])

        mk = lambda name, parent, rel: Folder.objects.create(
            name=name, parent=parent, owner=self.sync_user, is_public=False,
            is_third_party=True, third_party_source='adapt', third_party_id=rel)
        self.root = mk('ADAPT', None, None)
        self.flux = mk('Flux Measurements', self.root, 'Flux Measurements')
        self.flux_2024 = mk('2024', self.flux, 'Flux Measurements/2024')
        self.soils = mk('Soils', self.root, 'Soils')

        give_acl(self.root, fx.build_sd(aces=[(True, permissions.adapt_acl.AUTHENTICATED_USERS, fx.READ)]))
        give_acl(self.flux, fx.team_folder_sd(fx.FLUX_TEAM))
        give_acl(self.soils, fx.build_sd(aces=[(True, fx.SOILS_TEAM, fx.READ)]))

        self.tower = File.objects.create(
            name='tower', owner=self.sync_user, folder=self.flux_2024, is_public=False,
            is_third_party=True, third_party_source='adapt',
            third_party_url='/adapt/Flux Measurements/2024/tower.csv', file_type='csv', file_size=10)

        # Something that is not ADAPT, to prove the old rule still holds.
        self.private = Folder.objects.create(name='mine', owner=self.jane, is_public=False)
        self.public = Folder.objects.create(name='shared', owner=self.bob, is_public=True)


class PermissionModuleTests(AdaptTreeTestCase):

    def test_team_member_reads_and_writes_own_team_folder(self):
        self.assertTrue(permissions.can_read(self.jane, self.flux))
        self.assertTrue(permissions.can_write(self.jane, self.flux))

    def test_other_team_is_shut_out(self):
        self.assertFalse(permissions.can_read(self.bob, self.flux))
        self.assertFalse(permissions.can_read(self.jane, self.soils))

    def test_read_only_team_cannot_write(self):
        self.assertTrue(permissions.can_read(self.bob, self.soils))
        self.assertFalse(permissions.can_write(self.bob, self.soils))

    def test_subfolder_without_own_descriptor_inherits_nearest_ancestor(self):
        self.assertTrue(permissions.can_read(self.jane, self.flux_2024))
        self.assertFalse(permissions.can_read(self.bob, self.flux_2024))

    def test_file_takes_its_folder_descriptor(self):
        self.assertTrue(permissions.can_read(self.jane, self.tower))
        self.assertFalse(permissions.can_read(self.bob, self.tower))

    def test_user_without_directory_identity_fails_closed(self):
        self.assertFalse(permissions.can_read(self.nobody, self.root))
        self.assertFalse(permissions.can_read(self.nobody, self.flux))

    def test_folder_without_any_descriptor_fails_closed(self):
        orphan = Folder.objects.create(name='Orphan', parent=None, owner=self.sync_user, is_public=False,
                                       is_third_party=True, third_party_source='adapt', third_party_id='Orphan')
        self.assertFalse(permissions.can_read(self.jane, orphan))

    def test_superuser_bypasses_adapt_check(self):
        self.assertTrue(permissions.can_read(self.admin, self.flux))
        self.assertTrue(permissions.can_read(self.admin, self.soils))

    def test_sync_account_still_reads_as_owner(self):
        self.assertTrue(permissions.can_read(self.sync_user, self.flux))

    def test_non_adapt_objects_keep_owner_or_public_rule(self):
        self.assertTrue(permissions.can_read(self.jane, self.private))
        self.assertFalse(permissions.can_read(self.bob, self.private))
        self.assertTrue(permissions.can_read(self.jane, self.public))
        self.assertFalse(permissions.can_write(self.jane, self.public))

    def test_readable_adapt_roots_for_each_user(self):
        self.assertEqual(permissions.readable_adapt_roots(self.jane), [self.root])
        self.assertEqual(permissions.readable_adapt_roots(self.bob), [self.root])
        self.assertEqual(permissions.readable_adapt_roots(self.nobody), [])

    def test_root_listing_is_filtered_per_child(self):
        children = list(self.root.subfolders.all())
        self.assertEqual(permissions.filter_readable(self.jane, children), [self.flux])
        self.assertEqual(permissions.filter_readable(self.bob, children), [self.soils])

    def test_anonymous_never_reads(self):
        from django.contrib.auth.models import AnonymousUser
        self.assertFalse(permissions.can_read(AnonymousUser(), self.public))


class WebViewTests(AdaptTreeTestCase):

    def test_folder_detail_lists_only_readable_children(self):
        self.client.login(username='jane', password='x')
        response = self.client.get(reverse('filemanager:folder_detail', args=[self.root.id]))
        self.assertEqual(response.status_code, 200)
        names = [f.name for f in response.context['subfolders']]
        self.assertEqual(names, ['Flux Measurements'])

    def test_folder_detail_redirects_other_team(self):
        self.client.login(username='bob', password='x')
        response = self.client.get(reverse('filemanager:folder_detail', args=[self.flux.id]))
        self.assertEqual(response.status_code, 302)

    def test_can_edit_reflects_write_right(self):
        self.client.login(username='jane', password='x')
        self.assertTrue(self.client.get(reverse('filemanager:folder_detail', args=[self.flux.id])).context['can_edit'])
        self.client.login(username='bob', password='x')
        self.assertFalse(self.client.get(reverse('filemanager:folder_detail', args=[self.soils.id])).context['can_edit'])

    def test_download_is_gated_by_the_descriptor(self):
        url = reverse('filemanager:download_file', args=[self.tower.id])
        self.client.login(username='bob', password='x')
        self.assertIn(self.client.get(url).status_code, (302, 403, 404))
        self.client.login(username='jane', password='x')
        response = self.client.get(url)
        # jane is allowed; the bytes are not on disk in this test, so a 404 from
        # the filesystem is the expected "allowed but missing" outcome.
        self.assertEqual(response.status_code, 404)

    def test_dashboard_shows_adapt_root_to_team_members_only(self):
        url = reverse('filemanager:dashboard')
        self.client.login(username='jane', password='x')
        self.assertIn(self.root, list(self.client.get(url).context['third_party_folders']))
        self.client.login(username='nobody', password='x')
        self.assertNotIn(self.root, list(self.client.get(url).context['third_party_folders']))


class ApiTests(AdaptTreeTestCase):

    def auth(self, user):
        token, _ = Token.objects.get_or_create(user=user)
        return {'HTTP_AUTHORIZATION': f'Token {token.key}'}

    def test_list_folders_under_root_is_filtered(self):
        response = self.client.get('/api/v1/folders/', {'parent_id': str(self.root.id)}, **self.auth(self.jane))
        self.assertEqual(response.status_code, 200)
        names = sorted(f['name'] for f in response.json()['folders'])
        self.assertEqual(names, ['Flux Measurements'])

    def test_list_files_in_inherited_subfolder(self):
        response = self.client.get('/api/v1/files/', {'folder_id': str(self.flux_2024.id)}, **self.auth(self.jane))
        self.assertEqual(response.status_code, 200)
        self.assertEqual([f['name'] for f in response.json()['files']], ['tower'])
        response = self.client.get('/api/v1/files/', {'folder_id': str(self.flux_2024.id)}, **self.auth(self.bob))
        self.assertEqual(response.status_code, 403)

    def test_download_file_hidden_from_other_team(self):
        # The API answers 404 rather than 403 here on purpose: it does not confirm the file exists.
        response = self.client.get(f'/api/v1/files/{self.tower.id}/download/', **self.auth(self.bob))
        self.assertEqual(response.status_code, 404)

    def test_folder_info_hidden_from_other_team(self):
        response = self.client.get(f'/api/v1/folders/{self.flux.id}/info/', **self.auth(self.bob))
        self.assertEqual(response.status_code, 404)
        response = self.client.get(f'/api/v1/folders/{self.flux.id}/info/', **self.auth(self.jane))
        self.assertEqual(response.status_code, 200)
