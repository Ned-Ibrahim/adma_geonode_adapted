"""The two commands that make the permission model observable, with the network replaced."""
from io import StringIO
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone

from filemanager.models import DirectoryIdentity, Folder, FolderAcl
from filemanager.tests import acl_fixtures as fx

User = get_user_model()


class SyncAdaptAclsTests(TestCase):

    def setUp(self):
        self.sync_user = User.objects.create_user('syncbot', password='x')
        mk = lambda name, parent, rel: Folder.objects.create(
            name=name, parent=parent, owner=self.sync_user, is_third_party=True,
            third_party_source='adapt', third_party_id=rel)
        self.root = mk('ADAPT', None, None)
        self.flux = mk('Flux Measurements', self.root, 'Flux Measurements')
        self.soils = mk('Soils', self.root, 'Soils')
        # A non-ADAPT third-party folder must be ignored.
        Folder.objects.create(name='John Deere', owner=self.sync_user, is_third_party=True,
                              third_party_source='johndeere', third_party_id='org1')
        self.descriptors = {
            '': fx.build_sd(aces=[(True, 'S-1-5-11', fx.READ)]),
            'Flux Measurements': fx.team_folder_sd(fx.FLUX_TEAM),
            'Soils': fx.team_folder_sd(fx.SOILS_TEAM),
        }

    def run_command(self, *args, fetch=None):
        out = StringIO()
        fetch = fetch or (lambda rel: self.descriptors[rel])
        with mock.patch('filemanager.management.commands.sync_adapt_acls.Command.fetch', side_effect=fetch):
            call_command('sync_adapt_acls', *args, stdout=out, stderr=out)
        return out.getvalue()

    def test_stores_one_descriptor_per_adapt_folder(self):
        output = self.run_command()
        self.assertEqual(FolderAcl.objects.count(), 3)
        flux_acl = FolderAcl.objects.get(folder=self.flux)
        self.assertEqual(bytes(flux_acl.descriptor), self.descriptors['Flux Measurements'])
        self.assertEqual(flux_acl.owner_sid, fx.ADMIN_USER)
        self.assertEqual(flux_acl.ace_count, 2)
        self.assertIn('stored 3', output)

    def test_second_run_is_unchanged_not_rewritten(self):
        self.run_command()
        first = FolderAcl.objects.get(folder=self.flux)
        output = self.run_command()
        self.assertIn('unchanged 3', output)
        self.assertEqual(FolderAcl.objects.get(folder=self.flux).pk, first.pk)

    def test_changed_descriptor_replaces_the_old_one(self):
        self.run_command()
        self.descriptors['Soils'] = fx.team_folder_sd(fx.FLUX_TEAM)
        self.run_command()
        self.assertEqual(bytes(FolderAcl.objects.get(folder=self.soils).descriptor), self.descriptors['Soils'])

    def test_dry_run_writes_nothing(self):
        output = self.run_command('--dry-run')
        self.assertEqual(FolderAcl.objects.count(), 0)
        self.assertIn('dry run', output)

    def test_only_limits_to_a_subtree(self):
        self.run_command('--only', 'Flux Measurements')
        self.assertEqual(set(FolderAcl.objects.values_list('folder__name', flat=True)), {'Flux Measurements'})

    def test_fetch_error_records_error_and_keeps_previous_descriptor(self):
        self.run_command()

        def flaky(rel):
            if rel == 'Soils':
                raise OSError('STATUS_ACCESS_DENIED')
            return self.descriptors[rel]
        output = self.run_command(fetch=flaky)
        soils_acl = FolderAcl.objects.get(folder=self.soils)
        self.assertIn('ACCESS_DENIED', soils_acl.error)
        self.assertEqual(bytes(soils_acl.descriptor), self.descriptors['Soils'])
        self.assertIn('errors 1', output)

    def test_fetch_error_on_new_folder_fails_closed(self):
        from filemanager import permissions
        jane = User.objects.create_user('jane', password='x')
        DirectoryIdentity.objects.create(user=jane, dn='x', sid=fx.JANE, group_sids=[fx.FLUX_TEAM],
                                         fetched_at=timezone.now())

        def broken(rel):
            raise OSError('unreachable')
        self.run_command(fetch=broken)
        self.assertFalse(permissions.can_read(jane, self.flux))

    def test_stale_skips_fresh_rows(self):
        self.run_command()
        output = self.run_command('--stale', '1')
        self.assertIn('ADAPT folders to check: 0', output)


class CheckAdaptAccessTests(TestCase):

    def setUp(self):
        self.sync_user = User.objects.create_user('syncbot', password='x')
        self.jane = User.objects.create_user('jane', password='x')
        self.bob = User.objects.create_user('bob', password='x')
        DirectoryIdentity.objects.create(user=self.jane, dn='x', sid=fx.JANE, group_sids=[fx.FLUX_TEAM],
                                         fetched_at=timezone.now())
        self.flux = Folder.objects.create(name='Flux Measurements', owner=self.sync_user, is_third_party=True,
                                          third_party_source='adapt', third_party_id='Flux Measurements')
        FolderAcl.objects.create(folder=self.flux, descriptor=fx.team_folder_sd(fx.FLUX_TEAM),
                                 fetched_at=timezone.now(), ace_count=2)

    def run_command(self, *args):
        out = StringIO()
        call_command('check_adapt_access', *args, stdout=out, stderr=out)
        return out.getvalue()

    def test_allowed_member_shows_the_granting_ace(self):
        output = self.run_command('--user', 'jane', '--path', 'Flux Measurements')
        self.assertIn('Result : ALLOWED', output)
        self.assertIn(fx.FLUX_TEAM, output)
        self.assertIn('granted', output)

    def test_user_without_identity_is_explained(self):
        output = self.run_command('--user', 'bob', '--path', 'Flux Measurements')
        self.assertIn('never logged in through LDAP', output)
        self.assertIn('Result : DENIED', output)

    def test_unknown_path_is_an_error(self):
        with self.assertRaises(CommandError):
            self.run_command('--user', 'jane', '--path', 'Nope')
