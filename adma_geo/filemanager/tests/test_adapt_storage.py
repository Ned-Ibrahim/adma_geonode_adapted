"""Unit tests for writing to the ADAPT share.

These never touch the database or a real SMB mount. A temporary directory
stands in for the mount and a stub stands in for a Folder row, which keeps the
tests to the one thing this module is responsible for: deciding whether a write
is allowed and performing it without ever destroying what is already there.
"""
import os
import tempfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, override_settings

from filemanager import adapt_storage
from filemanager.adapt_storage import AdaptWriteDisabled, AdaptWriteError


class StubFolder:
    """The attributes adapt_storage reads off a Folder."""

    def __init__(self, third_party_id, source='adapt', is_third_party=True,
                 parent_id=1):
        self.third_party_id = third_party_id
        self.third_party_source = source
        self.is_third_party = is_third_party
        self.parent_id = parent_id

    def __str__(self):
        return f'StubFolder({self.third_party_id})'


class AdaptStorageTestCase(SimpleTestCase):
    """Base: a temporary directory playing the part of the mounted share."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.mount = os.path.realpath(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        # A non-empty mount, because an empty one is treated as a dropped mount.
        os.makedirs(os.path.join(self.mount, 'Data Management'))
        self.root = StubFolder(adapt_storage.ROOT_SENTINEL, parent_id=None)
        self.subfolder = StubFolder('Data Management')
        self.settings_patch = override_settings(
            ADAPT_MOUNT=self.mount,
            ADAPT_WRITE_ENABLED=True,
            ADAPT_WRITABLE_PREFIXES=[],
        )
        self.settings_patch.enable()
        self.addCleanup(self.settings_patch.disable)

    def upload(self, name, content=b'hello'):
        return SimpleUploadedFile(name, content)


class PathResolutionTests(AdaptStorageTestCase):

    def test_root_sentinel_maps_to_the_mount_itself(self):
        self.assertEqual(adapt_storage.folder_share_path(self.root), self.mount)

    def test_subfolder_resolves_under_the_mount(self):
        self.assertEqual(
            adapt_storage.folder_share_path(self.subfolder),
            os.path.join(self.mount, 'Data Management'),
        )

    def test_backslashes_are_accepted_as_separators(self):
        folder = StubFolder('Data Management\\Fields')
        self.assertEqual(
            adapt_storage.folder_share_path(folder),
            os.path.join(self.mount, 'Data Management', 'Fields'),
        )

    def test_traversal_out_of_the_mount_is_refused(self):
        # third_party_id is editable in Django admin, so it is untrusted input.
        folder = StubFolder('../../etc')
        with self.assertRaises(AdaptWriteError):
            adapt_storage.folder_share_path(folder)

    def test_absolute_path_cannot_escape_the_mount(self):
        folder = StubFolder('/etc/passwd')
        with self.assertRaises(AdaptWriteError):
            adapt_storage.folder_share_path(folder)

    def test_symlink_out_of_the_mount_is_refused(self):
        os.symlink('/etc', os.path.join(self.mount, 'escape'))
        with self.assertRaises(AdaptWriteError):
            adapt_storage.folder_share_path(StubFolder('escape'))

    def test_nested_folder_with_no_recorded_path_is_refused(self):
        # Otherwise it resolves to the share root and uploads land at the top of
        # the warehouse instead of inside the folder the user chose.
        with self.assertRaises(AdaptWriteError):
            adapt_storage.folder_share_path(StubFolder('', parent_id=1))

    def test_non_adapt_folder_is_refused(self):
        with self.assertRaises(AdaptWriteError):
            adapt_storage.folder_share_path(StubFolder('x', is_third_party=False))
        with self.assertRaises(AdaptWriteError):
            adapt_storage.folder_share_path(StubFolder('x', source='realm5'))


class NameValidationTests(AdaptStorageTestCase):

    def test_plain_names_pass(self):
        for name in ('field.shp', 'Report 2026.pdf', 'a-b_c.geojson'):
            self.assertEqual(adapt_storage.validate_name(name), name)

    def test_separators_are_refused(self):
        for name in ('a/b.txt', 'a\\b.txt', 'a:b.txt', 'a*b.txt'):
            with self.assertRaises(AdaptWriteError):
                adapt_storage.validate_name(name)

    def test_trailing_dot_or_space_is_refused(self):
        # Windows strips these silently, which would leave the database name and
        # the name on the share disagreeing forever.
        for name in ('report.', 'report ', ' report'):
            with self.assertRaises(AdaptWriteError):
                adapt_storage.validate_name(name)

    def test_windows_device_names_are_refused(self):
        for name in ('CON', 'nul.txt', 'COM1.shp', 'LPT9'):
            with self.assertRaises(AdaptWriteError):
                adapt_storage.validate_name(name)

    def test_dot_and_dotdot_are_refused(self):
        for name in ('.', '..', ''):
            with self.assertRaises(AdaptWriteError):
                adapt_storage.validate_name(name)


class WriteGuardTests(AdaptStorageTestCase):

    def test_write_disabled_is_refused(self):
        with override_settings(ADAPT_WRITE_ENABLED=False):
            with self.assertRaises(AdaptWriteDisabled):
                adapt_storage.write_upload(self.root, self.upload('a.txt'), 'a.txt')

    def test_empty_mount_is_refused_as_a_possible_dropped_mount(self):
        with tempfile.TemporaryDirectory() as empty:
            with override_settings(ADAPT_MOUNT=empty):
                with self.assertRaises(AdaptWriteError):
                    adapt_storage.write_upload(self.root, self.upload('a.txt'), 'a.txt')

    def test_prefix_allowlist_permits_a_listed_subtree(self):
        with override_settings(ADAPT_WRITABLE_PREFIXES=['Data Management']):
            path, name, size = adapt_storage.write_upload(
                self.subfolder, self.upload('a.txt'), 'a.txt')
        self.assertTrue(os.path.exists(path))
        self.assertEqual((name, size), ('a.txt', 5))

    def test_prefix_allowlist_refuses_everything_else(self):
        with override_settings(ADAPT_WRITABLE_PREFIXES=['Shared/Incoming']):
            with self.assertRaises(AdaptWriteError):
                adapt_storage.write_upload(self.subfolder, self.upload('a.txt'), 'a.txt')

    def test_empty_allowlist_means_the_whole_share(self):
        path, _, _ = adapt_storage.write_upload(self.root, self.upload('a.txt'), 'a.txt')
        self.assertEqual(os.path.dirname(path), self.mount)


class WriteTests(AdaptStorageTestCase):

    def test_content_lands_on_the_share(self):
        path, name, size = adapt_storage.write_upload(
            self.subfolder, self.upload('field.txt', b'payload'), 'field.txt')
        with open(path, 'rb') as fh:
            self.assertEqual(fh.read(), b'payload')
        self.assertEqual(name, 'field.txt')
        self.assertEqual(size, 7)

    def test_an_existing_file_is_never_overwritten(self):
        existing = os.path.join(self.mount, 'Data Management', 'field.txt')
        with open(existing, 'wb') as fh:
            fh.write(b'original')

        path, name, _ = adapt_storage.write_upload(
            self.subfolder, self.upload('field.txt', b'new'), 'field.txt')

        self.assertEqual(name, 'field_1.txt')
        with open(existing, 'rb') as fh:
            self.assertEqual(fh.read(), b'original')
        with open(path, 'rb') as fh:
            self.assertEqual(fh.read(), b'new')

    def test_deduplication_keeps_the_extension(self):
        for expected in ('map.shp', 'map_1.shp', 'map_2.shp'):
            _, name, _ = adapt_storage.write_upload(
                self.subfolder, self.upload('map.shp'), 'map.shp')
            self.assertEqual(name, expected)

    def test_a_failed_write_leaves_nothing_behind(self):
        class Exploding:
            name = 'broken.txt'

            def chunks(self):
                yield b'partial'
                raise IOError('connection reset')

        with self.assertRaises(IOError):
            adapt_storage.write_upload(self.subfolder, Exploding(), 'broken.txt')

        # A half written file on a system of record is worse than no file.
        self.assertEqual(os.listdir(os.path.join(self.mount, 'Data Management')), [])


class FolderCreationTests(AdaptStorageTestCase):

    def test_directory_names_are_not_split_on_a_dot(self):
        # "v1.2" is a whole directory name, not a stem and a suffix, so a
        # collision has to produce "v1.2_1" and never "v1_1.2".
        os.mkdir(os.path.join(self.mount, 'v1.2'))
        names = list(adapt_storage._candidate_names('v1.2', is_dir=True))
        self.assertEqual(names[1], 'v1.2_1')


class RelativePathTests(AdaptStorageTestCase):

    def test_rel_to_mount_matches_the_identity_sync_uses(self):
        abs_path = os.path.join(self.mount, 'Data Management', 'field.shp')
        self.assertEqual(adapt_storage.rel_to_mount(abs_path), 'Data Management/field.shp')
