"""adapt_acl import turns the snr18 permission export into ADAPT folder grants.

Every name, NUID and folder here is made up. The real export and roster are
personal data and never enter the repository.
"""
import shutil
import tempfile
from io import StringIO
from pathlib import Path

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management import CommandError, call_command
from django.test import TestCase

from filemanager.models import Folder, FolderGrant, UserProfile

User = get_user_model()

HEADER = '"Path","Protected","Identity","Rights","Type","AppliesTo"\n'
ALL = 'ContainerInherit, ObjectInherit/None'
READ = 'ReadAndExecute, Synchronize'


def entry(path, identity, rights, protected=False, applies_to=ALL, kind='Allow'):
    return f'"{path}","{protected}","{identity}","{rights}","{kind}","{applies_to}"\n'


def adapt_folder(name, parent, owner):
    return Folder.objects.create(
        name=name, parent=parent, owner=owner, is_third_party=True, third_party_source='adapt',
    )


class AclTree(TestCase):
    """ADAPT/{Soils/{2024}, Grazing Systems, Flux Measurements} and four people.

    alice and bob are in snr_adapt_all, alice also in snr_adapt_admin; carol is
    named on her own; dave has an account and nothing else.
    """

    @classmethod
    def setUpTestData(cls):
        sync = User.objects.create_user('sync')
        cls.root = adapt_folder('ADAPT', None, sync)
        cls.soils = adapt_folder('Soils', cls.root, sync)
        cls.soils_2024 = adapt_folder('2024', cls.soils, sync)
        cls.grazing = adapt_folder('Grazing Systems', cls.root, sync)
        cls.flux = adapt_folder('Flux Measurements', cls.root, sync)

        cls.all = Group.objects.create(name='snr_adapt_all')
        cls.admin = Group.objects.create(name='snr_adapt_admin')
        cls.alice = cls.person('alice.a', '00000001', cls.all, cls.admin)
        cls.bob = cls.person('bob2', '00000002', cls.all)
        cls.carol = cls.person('carol3', '00000003')
        cls.dave = cls.person('dave4', '00000004')

    @classmethod
    def person(cls, username, nuid, *groups):
        user = User.objects.create_user(username)
        UserProfile.objects.create(user=user, nuid=nuid)
        user.groups.add(*groups)
        return user

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir)

    def write(self, name, text):
        path = self.dir / name
        path.write_text(text)
        return str(path)

    def run_acl(self, *args):
        """Run adapt_acl; return (output, CommandError or None)."""
        out, err = StringIO(), StringIO()
        try:
            call_command('adapt_acl', *args, stdout=out, stderr=err)
        except CommandError as exc:
            return out.getvalue() + err.getvalue(), exc
        return out.getvalue() + err.getvalue(), None

    def grants(self):
        """{(folder path, 'user:name' or 'group:name'): 'read' or 'write'}"""
        found = {}
        for g in FolderGrant.objects.select_related('folder', 'user', 'group'):
            who = f'user:{g.user.username}' if g.user_id else f'group:{g.group.name}'
            found[(g.folder.get_full_path(), who)] = 'write' if g.can_write else 'read'
        return found


EXPORT = HEADER + (
    entry('(root)', 'BUILTIN\\Administrators', 'FullControl', protected=True)
    + entry('(root)', 'NEAD\\snr_adapt_all', READ, protected=True)
    + entry('(root)', 'NEAD\\snr_adapt_admin', 'FullControl', protected=True)
    + entry('(root)', 'NEAD\\00000003', READ, protected=True)
    + entry('(root)', 'NEAD\\00000001', READ, protected=True)
    + entry('Grazing Systems', 'NEAD\\00000002', 'FullControl')
    + entry('Grazing Systems', 'UNL-AD\\bob2', READ)
    + entry('Grazing Systems', 'NEAD\\99999999', 'FullControl')
    + entry('Grazing Systems', 'NEAD\\snr-helpdesk', 'FullControl')
    + entry('Soils\\2024', 'NEAD\\00000001', 'Modify, Synchronize')
    + entry('Flux Measurements', 'NEAD\\snr_adapt_all', READ, protected=True)
    + entry('Flux Measurements', 'NEAD\\00000002', 'FullControl', protected=True,
            applies_to='ContainerInherit, ObjectInherit/NoPropagateInherit')
    + entry('Flux Measurements', 'BUILTIN\\Users', READ, protected=True)
    + entry('Flux Measurements', 'CREATOR OWNER', 'FullControl', protected=True,
            applies_to='ContainerInherit, ObjectInherit/InheritOnly')
    + entry('Flux Measurements', 'NT AUTHORITY\\SYSTEM', 'FullControl', protected=True)
    + entry('Flux Measurements', 'BUILTIN\\Administrators', 'FullControl', protected=True)
    + entry('Old Projects\\Archive', 'NEAD\\00000001', 'FullControl')
    + entry('Old Projects\\Archive\\2019', 'NEAD\\00000001', 'FullControl')
)

IMPORTED = {
    ('ADAPT', 'group:snr_adapt_all'): 'read',
    ('ADAPT', 'group:snr_adapt_admin'): 'write',
    ('ADAPT', 'user:carol3'): 'read',
    ('ADAPT', 'user:alice.a'): 'read',
    ('ADAPT/Grazing Systems', 'user:bob2'): 'write',
    ('ADAPT/Soils/2024', 'user:alice.a'): 'write',
    ('ADAPT/Flux Measurements', 'group:snr_adapt_all'): 'read',
    ('ADAPT/Flux Measurements', 'user:bob2'): 'write',
}


class Import(AclTree):

    def import_export(self, text=EXPORT, *extra):
        return self.run_acl('import', self.write('acl.csv', text), *extra)

    def test_dry_run_prints_the_plan_and_writes_nothing(self):
        out, error = self.import_export()
        self.assertIsNone(error)
        self.assertEqual(self.grants(), {})
        self.assertIn('Add 8, change 0, remove 0, unchanged 0.', out)
        self.assertIn('Dry run', out)

    def test_apply_maps_read_write_users_and_groups(self):
        _, error = self.import_export(EXPORT, '--apply')
        self.assertIsNone(error)
        self.assertEqual(self.grants(), IMPORTED)

    def test_identities_it_cannot_map_are_listed_and_not_imported(self):
        out, _ = self.import_export(EXPORT, '--apply')
        section = out.split('Not imported:')[1].split('Folders not in the catalogue')[0]
        for identity in ('BUILTIN\\Administrators', 'BUILTIN\\Users', 'CREATOR OWNER', 'NT AUTHORITY\\SYSTEM',
                         'UNL-AD\\bob2', 'NEAD\\99999999', 'NEAD\\snr-helpdesk'):
            with self.subTest(identity=identity):
                self.assertIn(identity, section)
        self.assertIn('no ADMA account has NUID 99999999', section)
        self.assertIn('no ADMA group named snr-helpdesk', section)
        self.assertIn('FullControl on (root), Flux Measurements', section)
        self.assertEqual(Group.objects.count(), 2)

    def test_folders_missing_from_the_catalogue_are_listed_once_per_branch(self):
        out, _ = self.import_export()
        section = out.split('Folders not in the catalogue')[1]
        self.assertIn('Old Projects\\Archive (and 1 folder beneath it)', section)
        self.assertNotIn('Archive\\2019', section.split('Grants these')[0])

    def test_grants_lost_to_missing_folders_are_listed_and_counted(self):
        out, _ = self.import_export()
        lost = out.split('Grants these folders would carry')[1].split('Warnings:')[0]
        self.assertIn('user alice.a', lost)
        self.assertIn('Old Projects\\Archive\\2019', lost)
        self.assertIn('10 entries not imported', out)

    def test_no_propagate_inherit_is_imported_with_a_warning(self):
        out, _ = self.import_export(EXPORT, '--apply')
        self.assertEqual(self.grants()[('ADAPT/Flux Measurements', 'user:bob2')], 'write')
        self.assertIn('NoPropagateInherit', out.split('Warnings:')[1])

    def test_a_protected_folder_that_ancestor_grants_still_reach_is_a_warning(self):
        out, _ = self.import_export()
        warnings = out.split('Warnings:')[1]
        self.assertIn('Flux Measurements blocks inheritance on snr18', warnings)
        self.assertIn('user carol3', warnings)
        self.assertIn('group snr_adapt_admin', warnings)
        # alice reaches it again through snr_adapt_all, which the folder names.
        self.assertNotIn('alice.a', warnings)

    def test_apply_replaces_every_existing_adapt_grant(self):
        hand_made = Group.objects.create(name='adapt-all')
        FolderGrant.objects.create(folder=self.root, group=hand_made)
        FolderGrant.objects.create(folder=self.grazing, user=self.dave, can_write=True)
        FolderGrant.objects.create(folder=self.grazing, user=self.bob, can_write=False)
        kept = FolderGrant.objects.create(folder=self.root, user=self.carol)

        out, _ = self.import_export(EXPORT, '--apply')

        self.assertEqual(self.grants(), IMPORTED)
        self.assertTrue(FolderGrant.objects.filter(pk=kept.pk).exists())
        self.assertIn('Add 6, change 1, remove 2, unchanged 1.', out)

    def test_rerun_with_the_same_export_changes_nothing(self):
        self.import_export(EXPORT, '--apply')
        before = set(FolderGrant.objects.values_list('pk', 'folder_id', 'user_id', 'group_id', 'can_write'))
        out, error = self.import_export(EXPORT, '--apply')
        self.assertIsNone(error)
        self.assertIn('Add 0, change 0, remove 0, unchanged 8.', out)
        after = set(FolderGrant.objects.values_list('pk', 'folder_id', 'user_id', 'group_id', 'can_write'))
        self.assertEqual(before, after)

    def test_two_entries_for_the_same_identity_and_folder_become_one_grant(self):
        text = HEADER + entry('Soils', 'NEAD\\00000002', READ) + entry('Soils', 'NEAD\\00000002', 'FullControl')
        self.import_export(text, '--apply')
        self.assertEqual(self.grants(), {('ADAPT/Soils', 'user:bob2'): 'write'})

    def test_rights_that_are_neither_read_nor_write_are_listed(self):
        text = HEADER + entry('Soils', 'NEAD\\00000002', 'CreateFiles, AppendData, Synchronize')
        out, _ = self.import_export(text, '--apply')
        self.assertEqual(self.grants(), {})
        self.assertIn('CreateFiles, AppendData', out.split('Not imported:')[1])

    def test_inherited_entries_are_skipped_when_the_export_marks_them(self):
        text = ('"Path","Protected","Identity","Rights","Type","AppliesTo","IsInherited"\n'
                f'"Soils","False","NEAD\\00000002","FullControl","Allow","{ALL}","True"\n'
                f'"Soils","False","NEAD\\00000003","FullControl","Allow","{ALL}","False"\n')
        self.import_export(text, '--apply')
        self.assertEqual(self.grants(), {('ADAPT/Soils', 'user:carol3'): 'write'})

    def test_deny_entries_stop_the_import(self):
        text = EXPORT + entry('Soils', 'NEAD\\00000002', 'FullControl', kind='Deny')
        _, error = self.import_export(text, '--apply')
        self.assertIsNotNone(error)
        self.assertIn('Deny', str(error))
        self.assertEqual(self.grants(), {})

    def test_a_file_without_the_export_columns_is_refused(self):
        _, error = self.import_export('Path,Identity\n(root),NEAD\\00000001\n', '--apply')
        self.assertIn('Protected', str(error))

    def test_no_adapt_root_is_an_error(self):
        Folder.objects.filter(pk=self.root.pk).delete()
        _, error = self.import_export()
        self.assertIn('No ADAPT root folder', str(error))
