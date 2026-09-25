"""Uploading from ADMA onto the ADAPT share, gated by FolderGrant write grants.

A temporary directory stands in for the mount. Everything goes through the
Django test client, the way a browser or an API client reaches it, and then
checks the two places that matter: the file on the share and the row in ADMA.

The tests that matter most are in SyncAfterWrite: an upload has to survive the
next sync_adapt run untouched, neither duplicated nor pruned.
"""
import io
import json
import os
import shutil
import tempfile
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework.authtoken.models import Token

from filemanager.models import File, Folder, FolderGrant, UserProfile
from filemanager.views import delete_file_complete

User = get_user_model()

WAREHOUSE_BYTES = b'depth,carbon\n10,1.2\n'


class WriteBackCase(TestCase):
    """ADAPT/{Soils/cores.csv, Flux/tower.csv} on a temporary mount, synced into ADMA.

    reader: read grant on Soils. writer: write grant on Soils. flux_member: in a
    group with a write grant on Flux. stranger: no grant at all.
    """

    def setUp(self):
        self.mount = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.mount, True)
        # A sibling of the mount: the place a path traversal would land.
        self.outside = os.path.dirname(self.mount)
        self.write_file('Soils/cores.csv', WAREHOUSE_BYTES)
        self.write_file('Flux/tower.csv', b'co2\n400\n')

        self.sync_user = User.objects.create_superuser('sync', password='pw')
        self.reader = User.objects.create_user('reader', password='pw')
        self.writer = User.objects.create_user('writer', password='pw')
        self.flux_member = User.objects.create_user('fluxer', password='pw')
        self.stranger = User.objects.create_user('stranger', password='pw')
        self.root = Folder.objects.create(
            name='ADAPT', owner=self.sync_user, is_third_party=True,
            third_party_source='adapt', third_party_id='adapt_root',
        )

        settings_patch = override_settings(
            ADAPT_MOUNT=self.mount, ADAPT_WRITE_ENABLED=True, ADAPT_WRITABLE_PREFIXES=[],
        )
        settings_patch.enable()
        self.addCleanup(settings_patch.disable)

        self.sync()
        self.soils = Folder.objects.get(name='Soils', parent=self.root)
        self.flux = Folder.objects.get(name='Flux', parent=self.root)
        FolderGrant.objects.create(folder=self.soils, user=self.reader)
        FolderGrant.objects.create(folder=self.soils, user=self.writer, can_write=True)
        flux_team = Group.objects.create(name='flux-team')
        self.flux_member.groups.add(flux_team)
        FolderGrant.objects.create(folder=self.flux, group=flux_team, can_write=True)

    # ---------------------------------------------------------------- helpers

    def write_file(self, rel, content):
        path = os.path.join(self.mount, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'wb') as fh:
            fh.write(content)

    def on_share(self, rel):
        return os.path.join(self.mount, rel)

    def read_share(self, rel):
        with open(self.on_share(rel), 'rb') as fh:
            return fh.read()

    def sync(self, *extra):
        out = io.StringIO()
        call_command('sync_adapt', '--once', '--no-recompute', *extra, stdout=out, stderr=out)
        return out.getvalue()

    def upload(self, user, folder, name='field.csv', content=b'x,y\n3,4\n'):
        self.client.force_login(user)
        return self.client.post(reverse('filemanager:upload_files'), {
            'files': [SimpleUploadedFile(name, content)], 'folder_id': str(folder.id),
        })

    def create_folder(self, user, parent, name):
        self.client.force_login(user)
        return self.client.post(
            reverse('filemanager:create_folder'),
            data=json.dumps({'name': name, 'parent_id': str(parent.id)}),
            content_type='application/json',
        )

    def upload_tree(self, user, parent, paths):
        self.client.force_login(user)
        return self.client.post(reverse('filemanager:upload_folders'), {
            'files': [SimpleUploadedFile(p.rsplit('/', 1)[-1], b'data') for p in paths],
            'file_paths': paths,
            'folder_id': str(parent.id),
        })

    def api_upload(self, user, folder, name='field.csv', content=b'x,y\n3,4\n'):
        token = Token.objects.create(user=user)
        self.client.logout()
        return self.client.post(
            '/api/v1/files/upload/',
            {'files': [SimpleUploadedFile(name, content)], 'folder_id': str(folder.id)},
            HTTP_AUTHORIZATION=f'Token {token.key}',
        )

    def share_listing(self):
        found = []
        for current, _dirs, files in os.walk(self.mount):
            for name in files:
                found.append(os.path.relpath(os.path.join(current, name), self.mount))
        return sorted(found)


class UploadGate(WriteBackCase):

    def test_a_write_grantee_uploads_onto_the_share(self):
        response = self.upload(self.writer, self.soils, 'field.csv', b'x,y\n3,4\n')

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(self.read_share('Soils/field.csv'), b'x,y\n3,4\n')
        row = File.objects.get(name='field.csv')
        self.assertEqual(row.folder, self.soils)
        self.assertEqual(row.owner, self.writer)
        self.assertTrue(row.is_adapt_reference())
        self.assertFalse(row.file, 'an ADAPT upload must not be copied into MEDIA_ROOT')
        self.assertEqual(row.third_party_id, 'Soils/field.csv')
        self.assertEqual(row.third_party_url, self.on_share('Soils/field.csv'))
        self.assertEqual(row.file_size, 8)
        self.assertEqual(row.file_type, 'csv')
        self.assertFalse(row.is_public)

    def test_the_folder_totals_count_the_upload_at_once(self):
        # ADAPT folders show totals precomputed by sync; an upload adds to them.
        call_command('recompute_folder_sizes', source='adapt', verbosity=0)
        self.upload(self.writer, self.soils, 'field.csv', b'12345')
        self.soils.refresh_from_db()
        self.root.refresh_from_db()
        self.assertEqual((self.soils.get_total_size(), self.soils.total_file_count), (len(WAREHOUSE_BYTES) + 5, 2))
        self.assertEqual(self.root.total_file_count, 3)

    def test_other_readers_of_the_folder_see_the_upload(self):
        self.upload(self.writer, self.soils, 'field.csv')
        self.client.force_login(self.reader)
        page = self.client.get(reverse('filemanager:folder_detail', args=[self.soils.id]))
        self.assertContains(page, 'field.csv')
        row = File.objects.get(name='field.csv')
        download = self.client.get(reverse('filemanager:download_file', args=[row.id]))
        self.assertEqual(download.status_code, 200)
        self.assertEqual(b''.join(download.streaming_content), b'x,y\n3,4\n')

    def test_a_read_grantee_is_refused(self):
        response = self.upload(self.reader, self.soils)

        self.assertEqual(response.status_code, 403)
        self.assertIn('read-only access', response.json()['error'])
        self.assertFalse(os.path.exists(self.on_share('Soils/field.csv')))
        self.assertFalse(File.objects.filter(name='field.csv').exists())

    def test_a_user_without_a_grant_does_not_even_find_the_folder(self):
        response = self.upload(self.stranger, self.soils)

        self.assertEqual(response.status_code, 404)
        self.assertFalse(os.path.exists(self.on_share('Soils/field.csv')))

    def test_a_write_grant_on_one_folder_does_not_reach_a_sibling(self):
        response = self.upload(self.writer, self.flux)
        self.assertEqual(response.status_code, 404)
        self.assertFalse(os.path.exists(self.on_share('Flux/field.csv')))

    def test_a_group_write_grant_works(self):
        response = self.upload(self.flux_member, self.flux, 'fluxes.csv')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(os.path.exists(self.on_share('Flux/fluxes.csv')))

    def test_a_write_grant_covers_subfolders_too(self):
        os.makedirs(self.on_share('Soils/2024'))
        self.sync()
        sub = Folder.objects.get(name='2024', parent=self.soils)
        response = self.upload(self.writer, sub, 'deep.csv')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(os.path.exists(self.on_share('Soils/2024/deep.csv')))

    def test_a_superuser_may_write_without_a_grant(self):
        admin = User.objects.create_superuser('admin', password='pw')
        response = self.upload(admin, self.flux, 'admin.csv')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(os.path.exists(self.on_share('Flux/admin.csv')))

    def test_write_disabled_refuses_with_a_clear_message(self):
        with override_settings(ADAPT_WRITE_ENABLED=False):
            response = self.upload(self.writer, self.soils)
            owner_response = self.upload(self.sync_user, self.soils, 'mine.csv')

        self.assertEqual(response.status_code, 403)
        self.assertIn('turned off', response.json()['error'])
        # Not even the account that owns the ADAPT rows gets a copy into MEDIA_ROOT.
        self.assertEqual(owner_response.status_code, 403)
        self.assertEqual(self.share_listing(), ['Flux/tower.csv', 'Soils/cores.csv'])
        self.assertFalse(File.objects.filter(name__in=['field.csv', 'mine.csv']).exists())

    def test_a_user_who_must_change_their_password_cannot_upload(self):
        UserProfile.objects.create(user=self.writer, must_change_password=True)
        response = self.upload(self.writer, self.soils)
        self.assertEqual(response.status_code, 403)
        self.assertIn('password', response.json()['error'])
        self.assertFalse(os.path.exists(self.on_share('Soils/field.csv')))

    def test_uploads_into_own_folders_still_go_to_media(self):
        mine = Folder.objects.create(name='Mine', owner=self.writer)
        media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, media, True)
        with override_settings(MEDIA_ROOT=media):
            response = self.upload(self.writer, mine, 'n.csv')
        self.assertEqual(response.status_code, 200, response.content)
        row = File.objects.get(name='n.csv')
        self.assertFalse(row.is_third_party)
        self.assertEqual(self.share_listing(), ['Flux/tower.csv', 'Soils/cores.csv'])


class NamesOnTheShare(WriteBackCase):

    def test_an_existing_file_is_never_overwritten(self):
        first = self.upload(self.writer, self.soils, 'cores.csv', b'new one')
        second = self.upload(self.writer, self.soils, 'cores.csv', b'newer one')

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(self.read_share('Soils/cores.csv'), WAREHOUSE_BYTES)
        self.assertEqual(self.read_share('Soils/cores_1.csv'), b'new one')
        self.assertEqual(self.read_share('Soils/cores_2.csv'), b'newer one')
        self.assertEqual(
            [f['name'] for f in second.json()['files']], ['cores_2.csv'],
            'the response names the file as it landed on the share',
        )

    def test_a_name_held_by_a_row_whose_file_left_the_share_is_not_reused(self):
        # Until the next prune the old row still holds the name in ADMA.
        self.upload(self.writer, self.soils, 'field.csv', b'first')
        os.unlink(self.on_share('Soils/field.csv'))
        response = self.upload(self.writer, self.soils, 'field.csv', b'second')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(self.read_share('Soils/field_1.csv'), b'second')

    def test_the_prefix_allowlist_refuses_writes_outside_it(self):
        with override_settings(ADAPT_WRITABLE_PREFIXES=['Flux']):
            refused = self.upload(self.writer, self.soils)
            allowed = self.upload(self.flux_member, self.flux, 'ok.csv')
        self.assertEqual(refused.status_code, 400)
        self.assertIn('not allowed', refused.json()['error'])
        self.assertFalse(os.path.exists(self.on_share('Soils/field.csv')))
        self.assertEqual(allowed.status_code, 200)

    def test_a_tampered_folder_path_cannot_leave_the_mount(self):
        # third_party_id is editable in Django admin, so it is untrusted input.
        self.soils.third_party_id = '../escaped'
        self.soils.save(update_fields=['third_party_id'])
        os.makedirs(os.path.join(self.outside, 'escaped'), exist_ok=True)
        self.addCleanup(shutil.rmtree, os.path.join(self.outside, 'escaped'), True)

        response = self.upload(self.writer, self.soils)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(os.listdir(os.path.join(self.outside, 'escaped')), [])

    def test_names_the_share_cannot_store_are_refused(self):
        for name in ('CON.csv', 'bad:name.csv', 'trailing.', 'what?.csv'):
            with self.subTest(name=name):
                response = self.upload(self.writer, self.soils, name)
                self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(self.share_listing(), ['Flux/tower.csv', 'Soils/cores.csv'])

    def test_a_file_name_with_a_path_in_it_lands_inside_the_folder(self):
        self.client.force_login(self.writer)
        upload = io.BytesIO(b'sneaky')
        upload.name = '../../evil.csv'
        response = self.client.post(reverse('filemanager:upload_files'), {
            'files': [upload], 'folder_id': str(self.soils.id)})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertFalse(os.path.exists(os.path.join(self.outside, 'evil.csv')))
        self.assertEqual(self.read_share('Soils/evil.csv'), b'sneaky')

    def test_folder_upload_paths_cannot_traverse(self):
        for path in ('../escape/x.csv', 'a/../../escape/x.csv', '../../x.csv', 'a\\..\\..\\x.csv'):
            with self.subTest(path=path):
                response = self.upload_tree(self.writer, self.soils, [path])
                self.assertEqual(response.status_code, 400, response.content)
        self.assertFalse(os.path.exists(os.path.join(self.outside, 'escape')))
        self.assertFalse(os.path.exists(os.path.join(self.outside, 'x.csv')))
        self.assertEqual(self.share_listing(), ['Flux/tower.csv', 'Soils/cores.csv'])


class FolderWrites(WriteBackCase):

    def test_a_write_grantee_creates_a_folder_on_the_share(self):
        response = self.create_folder(self.writer, self.soils, 'Uploads')

        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(os.path.isdir(self.on_share('Soils/Uploads')))
        folder = Folder.objects.get(name='Uploads')
        self.assertEqual(folder.parent, self.soils)
        self.assertEqual(folder.owner, self.writer)
        self.assertEqual((folder.is_third_party, folder.third_party_source), (True, 'adapt'))
        self.assertEqual(folder.third_party_id, 'Soils/Uploads')

    def test_a_folder_name_already_on_the_share_gets_a_suffix(self):
        os.makedirs(self.on_share('Soils/Uploads'))
        response = self.create_folder(self.writer, self.soils, 'Uploads')
        self.assertEqual(response.json()['folder']['name'], 'Uploads_1')
        self.assertTrue(os.path.isdir(self.on_share('Soils/Uploads_1')))

    def test_a_read_grantee_cannot_create_a_folder(self):
        response = self.create_folder(self.reader, self.soils, 'Uploads')
        self.assertEqual(response.status_code, 403)
        self.assertFalse(os.path.exists(self.on_share('Soils/Uploads')))

    def test_a_folder_name_cannot_traverse(self):
        for name in ('..', '../escape', 'a/b', 'a\\b'):
            with self.subTest(name=name):
                response = self.create_folder(self.writer, self.soils, name)
                self.assertEqual(response.status_code, 400, response.content)
        self.assertFalse(os.path.exists(os.path.join(self.outside, 'escape')))
        self.assertEqual(os.listdir(self.on_share('Soils')), ['cores.csv'])

    def test_a_write_grantee_uploads_a_folder_tree(self):
        response = self.upload_tree(self.writer, self.soils, ['Plots/a.csv', 'Plots/North/b.csv'])

        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(os.path.exists(self.on_share('Soils/Plots/a.csv')))
        self.assertTrue(os.path.exists(self.on_share('Soils/Plots/North/b.csv')))
        north = Folder.objects.get(name='North')
        self.assertEqual(north.third_party_id, 'Soils/Plots/North')
        self.assertEqual(File.objects.get(name='b.csv').third_party_id, 'Soils/Plots/North/b.csv')

    def test_a_read_grantee_cannot_upload_a_folder_tree(self):
        response = self.upload_tree(self.reader, self.soils, ['Plots/a.csv'])
        self.assertEqual(response.status_code, 403)
        self.assertFalse(os.path.exists(self.on_share('Soils/Plots')))


class ApiUpload(WriteBackCase):

    def test_a_write_grantee_uploads_through_the_api(self):
        response = self.api_upload(self.writer, self.soils, 'api.csv')
        self.assertEqual(response.status_code, 201, response.content)
        self.assertTrue(os.path.exists(self.on_share('Soils/api.csv')))
        self.assertTrue(File.objects.get(name='api.csv').is_adapt_reference())

    def test_a_read_grantee_is_refused_by_the_api(self):
        response = self.api_upload(self.reader, self.soils, 'api.csv')
        self.assertEqual(response.status_code, 403)
        self.assertIn('read-only access', response.json()['error'])
        self.assertFalse(os.path.exists(self.on_share('Soils/api.csv')))

    def test_a_user_without_a_grant_gets_a_404_from_the_api(self):
        response = self.api_upload(self.stranger, self.soils, 'api.csv')
        self.assertEqual(response.status_code, 404)

    def test_write_disabled_refuses_through_the_api(self):
        with override_settings(ADAPT_WRITE_ENABLED=False):
            response = self.api_upload(self.writer, self.soils, 'api.csv')
        self.assertEqual(response.status_code, 403)
        self.assertFalse(os.path.exists(self.on_share('Soils/api.csv')))

    def test_a_write_grantee_uploads_a_folder_tree_through_the_api(self):
        token = Token.objects.create(user=self.writer)
        response = self.client.post('/api/v1/folders/upload/', {
            'files': [SimpleUploadedFile('a.csv', b'1')], 'file_paths': ['Plots/a.csv'],
            'folder_id': str(self.soils.id),
        }, HTTP_AUTHORIZATION=f'Token {token.key}')
        self.assertEqual(response.status_code, 201, response.content)
        self.assertTrue(os.path.exists(self.on_share('Soils/Plots/a.csv')))
        self.assertEqual(Folder.objects.get(name='Plots').third_party_id, 'Soils/Plots')

    def test_a_read_grantee_cannot_upload_a_folder_tree_through_the_api(self):
        token = Token.objects.create(user=self.reader)
        response = self.client.post('/api/v1/folders/upload/', {
            'files': [SimpleUploadedFile('a.csv', b'1')], 'file_paths': ['Plots/a.csv'],
            'folder_id': str(self.soils.id),
        }, HTTP_AUTHORIZATION=f'Token {token.key}')
        self.assertEqual(response.status_code, 403)
        self.assertFalse(os.path.exists(self.on_share('Soils/Plots')))


class DeleteAndRename(WriteBackCase):

    def delete(self, user, row):
        self.client.force_login(user)
        with mock.patch('filemanager.tasks.delete_file_async_task.delay') as task:
            task.return_value.id = 'task'
            return self.client.post(
                reverse('filemanager:delete_item'),
                data=json.dumps({'type': 'file', 'id': str(row.id)}),
                content_type='application/json',
            )

    def test_a_writer_removes_their_upload_from_adma_but_never_from_the_share(self):
        self.upload(self.writer, self.soils, 'field.csv')
        row = File.objects.get(name='field.csv')

        response = self.delete(self.writer, row)
        self.assertEqual(response.status_code, 200, response.content)
        # The async task does the row removal; run what it runs.
        delete_file_complete(File.objects.get(pk=row.pk))

        self.assertFalse(File.objects.filter(pk=row.pk).exists())
        self.assertTrue(os.path.exists(self.on_share('Soils/field.csv')))

    def test_a_read_grantee_cannot_delete_adapt_rows_even_their_own(self):
        self.upload(self.writer, self.soils, 'field.csv')
        row = File.objects.get(name='field.csv')
        FolderGrant.objects.filter(user=self.writer).update(can_write=False)

        response = self.delete(self.writer, row)

        self.assertEqual(response.status_code, 403)
        row.refresh_from_db()
        self.assertFalse(row.deletion_in_progress)

    def test_renaming_an_adapt_file_is_refused(self):
        self.upload(self.writer, self.soils, 'field.csv')
        row = File.objects.get(name='field.csv')
        response = self.client.post(
            reverse('filemanager:rename_file'),
            data=json.dumps({'file_id': str(row.id), 'new_name': 'renamed.csv'}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('cannot be renamed', response.json()['error'])
        row.refresh_from_db()
        self.assertEqual(row.name, 'field.csv')
        self.assertTrue(os.path.exists(self.on_share('Soils/field.csv')))


class SyncAfterWrite(WriteBackCase):
    """sync_adapt matches rows by path whoever owns them, so an upload survives it."""

    def test_sync_with_prune_neither_duplicates_nor_prunes_an_upload(self):
        self.upload(self.writer, self.soils, 'field.csv')
        self.create_folder(self.writer, self.soils, 'Uploads')
        self.upload(self.writer, Folder.objects.get(name='Uploads'), 'inner.csv')
        rows_before = set(File.objects.values_list('id', flat=True))
        folders_before = set(Folder.objects.values_list('id', flat=True))

        out = self.sync('--prune')

        self.assertIn('Pruned files:     0', out)
        self.assertIn('Pruned folders:   0', out)
        self.assertEqual(set(File.objects.values_list('id', flat=True)), rows_before)
        self.assertEqual(set(Folder.objects.values_list('id', flat=True)), folders_before)
        self.assertEqual(File.objects.get(name='field.csv').owner, self.writer)
        self.assertEqual(Folder.objects.get(name='Uploads').owner, self.writer)

    def test_an_upload_deleted_from_the_share_is_pruned(self):
        self.upload(self.writer, self.soils, 'field.csv')
        os.unlink(self.on_share('Soils/field.csv'))
        self.sync('--prune')
        self.assertFalse(File.objects.filter(name='field.csv').exists())


class UploadControls(WriteBackCase):
    """The folder page offers upload only where it would be accepted."""

    def page(self, user, folder):
        self.client.force_login(user)
        return self.client.get(reverse('filemanager:folder_detail', args=[folder.id]))

    def test_a_writer_sees_the_upload_controls(self):
        page = self.page(self.writer, self.soils)
        self.assertContains(page, 'id="uploadModal"')
        self.assertContains(page, 'onclick="showUploadModal()"')
        self.assertNotContains(page, 'Read only')
        # Visibility and deleting the folder stay with its owner.
        self.assertNotContains(page, 'onclick="toggleVisibility()"')

    def test_a_reader_sees_no_upload_controls_and_is_told_why(self):
        page = self.page(self.reader, self.soils)
        self.assertNotContains(page, 'onclick="showUploadModal()"')
        self.assertContains(page, 'Read only')

    def test_nobody_sees_upload_controls_while_write_back_is_off(self):
        with override_settings(ADAPT_WRITE_ENABLED=False):
            page = self.page(self.writer, self.soils)
        self.assertNotContains(page, 'onclick="showUploadModal()"')
        self.assertContains(page, 'Read only')
