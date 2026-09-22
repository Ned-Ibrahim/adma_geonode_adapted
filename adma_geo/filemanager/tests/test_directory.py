"""SID conversion and identity capture at LDAP login, with a fake directory."""
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase

from filemanager import directory
from filemanager.models import DirectoryIdentity
from filemanager.tests import acl_fixtures as fx

User = get_user_model()


class SidConversionTests(SimpleTestCase):

    def test_roundtrip_domain_sid(self):
        for sid in (fx.JANE, fx.FLUX_TEAM, 'S-1-5-11', 'S-1-1-0', 'S-1-5-21-3623811015-3361044348-30300820-1013'):
            self.assertEqual(directory.sid_to_string(directory.string_to_sid(sid)), sid)

    def test_known_binary_form(self):
        # S-1-5-21-1-2-3-1001 hand-packed: revision 1, 6 sub-authorities, authority 5
        blob = bytes([1, 6, 0, 0, 0, 0, 0, 5]) + (21).to_bytes(4, 'little') + (1).to_bytes(4, 'little') \
            + (2).to_bytes(4, 'little') + (3).to_bytes(4, 'little') + (1001).to_bytes(4, 'little') + (7).to_bytes(4, 'little')
        self.assertEqual(directory.sid_to_string(blob), 'S-1-5-21-1-2-3-1001-7')

    def test_short_or_inconsistent_blob_rejected(self):
        with self.assertRaises(ValueError):
            directory.sid_to_string(b'\x01\x02')
        with self.assertRaises(ValueError):
            directory.sid_to_string(bytes([1, 3, 0, 0, 0, 0, 0, 5]) + b'\x00' * 4)

    def test_extract_sids_sorts_and_dedupes(self):
        attrs = {
            'objectSid': [directory.string_to_sid(fx.JANE)],
            'tokenGroups': [directory.string_to_sid(fx.FLUX_TEAM), directory.string_to_sid(fx.DOMAIN_ADMINS),
                            directory.string_to_sid(fx.FLUX_TEAM)],
        }
        sid, groups = directory.extract_sids(attrs)
        self.assertEqual(sid, fx.JANE)
        self.assertEqual(groups, sorted({fx.FLUX_TEAM, fx.DOMAIN_ADMINS}))

    def test_extract_sids_tolerates_missing_attributes(self):
        self.assertEqual(directory.extract_sids({}), (None, []))


class FakeConnection:
    def __init__(self, entries):
        self.entries = entries
        self.calls = []

    def search_s(self, dn, scope, filterstr, attrlist):
        self.calls.append((dn, scope, filterstr, tuple(attrlist)))
        return [(dn, self.entries[dn])] if dn in self.entries else []

    def unbind_s(self):
        pass


class FakeLdapUser:
    def __init__(self, dn, connection):
        self.dn = dn
        self.connection = connection


class PopulateUserTests(TestCase):

    def setUp(self):
        self.dn = 'CN=jane,OU=People,DC=nead,DC=test'
        self.connection = FakeConnection({self.dn: {
            'objectSid': [directory.string_to_sid(fx.JANE)],
            'tokenGroups': [directory.string_to_sid(fx.FLUX_TEAM)],
        }})

    def test_existing_user_gets_identity_immediately(self):
        jane = User.objects.create_user('jane', password='x')
        directory.on_populate_user(None, user=jane, ldap_user=FakeLdapUser(self.dn, self.connection))
        identity = DirectoryIdentity.objects.get(user=jane)
        self.assertEqual(identity.sid, fx.JANE)
        self.assertEqual(identity.group_sids, [fx.FLUX_TEAM])
        self.assertEqual(identity.dn, self.dn)
        # Base-scope search on the user's own entry, which is the only way to get tokenGroups.
        self.assertEqual(self.connection.calls[0][0], self.dn)
        self.assertEqual(self.connection.calls[0][1], 0)

    def test_first_login_defers_until_the_user_row_exists(self):
        jane = User(username='jane')  # unsaved, as django-auth-ldap hands it over on first login
        directory.on_populate_user(None, user=jane, ldap_user=FakeLdapUser(self.dn, self.connection))
        self.assertFalse(DirectoryIdentity.objects.filter(user__username='jane').exists())
        jane.set_unusable_password()
        jane.save()  # post_save signal flushes the pending identity
        identity = DirectoryIdentity.objects.get(user=jane)
        self.assertEqual(identity.group_sids, [fx.FLUX_TEAM])

    def test_directory_failure_leaves_previous_identity_untouched(self):
        jane = User.objects.create_user('jane', password='x')
        directory.store_identity(jane, self.dn, fx.JANE, [fx.FLUX_TEAM])

        class Broken:
            def search_s(self, *a, **k):
                raise RuntimeError('LDAP down')
        directory.on_populate_user(None, user=jane, ldap_user=FakeLdapUser(self.dn, Broken()))
        self.assertEqual(DirectoryIdentity.objects.get(user=jane).group_sids, [fx.FLUX_TEAM])

    def test_refresh_identity_updates_groups(self):
        jane = User.objects.create_user('jane', password='x')
        directory.store_identity(jane, self.dn, fx.JANE, [])
        directory.refresh_identity(jane, connection=self.connection)
        self.assertEqual(DirectoryIdentity.objects.get(user=jane).group_sids, [fx.FLUX_TEAM])

    def test_refresh_identity_without_dn_is_a_noop(self):
        jane = User.objects.create_user('jane', password='x')
        self.assertIsNone(directory.refresh_identity(jane, connection=self.connection))
        self.assertEqual(self.connection.calls, [])
