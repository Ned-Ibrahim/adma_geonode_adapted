"""The access-check algorithm against synthetic descriptors. No network, no database."""
from django.test import SimpleTestCase
from smbprotocol.security_descriptor import AceFlags

from filemanager import adapt_acl
from filemanager.tests import acl_fixtures as fx


class ParseTests(SimpleTestCase):

    def test_roundtrip_owner_group_and_aces(self):
        blob = fx.team_folder_sd(fx.FLUX_TEAM)
        sd = adapt_acl.parse(blob)
        self.assertEqual(sd.owner, fx.ADMIN_USER)
        self.assertEqual(sd.group, fx.DOMAIN_ADMINS)
        self.assertTrue(sd.dacl_present)
        self.assertEqual([a.sid for a in sd.aces], [fx.DOMAIN_ADMINS, fx.FLUX_TEAM])
        self.assertTrue(all(a.allow for a in sd.aces))

    def test_deny_ace_parsed_as_deny(self):
        sd = adapt_acl.parse(fx.build_sd(aces=[(False, fx.BOB, fx.READ), (True, fx.FLUX_TEAM, fx.READ)]))
        self.assertFalse(sd.aces[0].allow)
        self.assertTrue(sd.aces[1].allow)

    def test_null_dacl_is_recorded_as_absent(self):
        sd = adapt_acl.parse(fx.build_sd(dacl=False))
        self.assertFalse(sd.dacl_present)
        self.assertEqual(sd.aces, ())

    def test_garbage_raises_descriptor_error(self):
        with self.assertRaises(adapt_acl.DescriptorError):
            adapt_acl.parse(b'\x01\x02\x03')
        with self.assertRaises(adapt_acl.DescriptorError):
            adapt_acl.parse(b'')


class GenericMappingTests(SimpleTestCase):

    def test_generic_read_maps_to_file_read_data(self):
        self.assertTrue(adapt_acl.map_generic(adapt_acl.GENERIC_READ) & adapt_acl.FILE_READ_DATA)
        self.assertFalse(adapt_acl.map_generic(adapt_acl.GENERIC_READ) & adapt_acl.FILE_WRITE_DATA)

    def test_generic_write_maps_to_add_file_and_subdirectory(self):
        mapped = adapt_acl.map_generic(adapt_acl.GENERIC_WRITE)
        self.assertEqual(mapped & adapt_acl.WRITE_MASK, adapt_acl.WRITE_MASK)

    def test_generic_all_covers_everything(self):
        self.assertEqual(adapt_acl.map_generic(adapt_acl.GENERIC_ALL) & adapt_acl.FILE_ALL_ACCESS,
                         adapt_acl.FILE_ALL_ACCESS)

    def test_specific_bits_pass_through(self):
        self.assertEqual(adapt_acl.map_generic(0x1F01FF), 0x1F01FF)


class AccessCheckTests(SimpleTestCase):

    def setUp(self):
        self.team_sd = adapt_acl.parse(fx.team_folder_sd(fx.FLUX_TEAM))

    def token(self, *sids):
        return frozenset(sids)

    def test_team_member_can_read_and_write(self):
        token = self.token(fx.JANE, fx.FLUX_TEAM)
        self.assertTrue(adapt_acl.access_check(self.team_sd, token, adapt_acl.READ_MASK))
        self.assertTrue(adapt_acl.access_check(self.team_sd, token, adapt_acl.WRITE_MASK))

    def test_non_member_gets_nothing(self):
        token = self.token(fx.BOB, fx.SOILS_TEAM)
        self.assertFalse(adapt_acl.access_check(self.team_sd, token, adapt_acl.READ_MASK))
        self.assertFalse(adapt_acl.access_check(self.team_sd, token, adapt_acl.WRITE_MASK))

    def test_read_only_group_cannot_write(self):
        sd = adapt_acl.parse(fx.build_sd(aces=[(True, fx.SOILS_TEAM, fx.READ)]))
        token = self.token(fx.BOB, fx.SOILS_TEAM)
        self.assertTrue(adapt_acl.access_check(sd, token, adapt_acl.READ_MASK))
        self.assertFalse(adapt_acl.access_check(sd, token, adapt_acl.WRITE_MASK))

    def test_deny_before_allow_wins(self):
        sd = adapt_acl.parse(fx.build_sd(aces=[
            (False, fx.BOB, fx.READ),          # explicit deny for Bob
            (True, fx.FLUX_TEAM, fx.MODIFY),   # but Bob is on the team
        ]))
        self.assertFalse(adapt_acl.access_check(sd, self.token(fx.BOB, fx.FLUX_TEAM), adapt_acl.READ_MASK))
        self.assertTrue(adapt_acl.access_check(sd, self.token(fx.JANE, fx.FLUX_TEAM), adapt_acl.READ_MASK))

    def test_deny_for_bits_not_requested_does_not_block(self):
        sd = adapt_acl.parse(fx.build_sd(aces=[
            (False, fx.FLUX_TEAM, adapt_acl.DELETE),
            (True, fx.FLUX_TEAM, fx.READ),
        ]))
        self.assertTrue(adapt_acl.access_check(sd, self.token(fx.JANE, fx.FLUX_TEAM), adapt_acl.READ_MASK))

    def test_allow_accumulates_across_aces(self):
        sd = adapt_acl.parse(fx.build_sd(aces=[
            (True, fx.FLUX_TEAM, adapt_acl.FILE_WRITE_DATA),
            (True, fx.JANE, adapt_acl.FILE_APPEND_DATA),
        ]))
        self.assertTrue(adapt_acl.access_check(sd, self.token(fx.JANE, fx.FLUX_TEAM), adapt_acl.WRITE_MASK))
        self.assertFalse(adapt_acl.access_check(sd, self.token(fx.BOB, fx.FLUX_TEAM), adapt_acl.WRITE_MASK))

    def test_inherit_only_aces_are_ignored_for_the_folder_itself(self):
        sd = adapt_acl.parse(fx.build_sd(aces=[
            (True, fx.FLUX_TEAM, fx.MODIFY, AceFlags.INHERIT_ONLY_ACE | AceFlags.OBJECT_INHERIT_ACE),
        ]))
        self.assertFalse(adapt_acl.access_check(sd, self.token(fx.JANE, fx.FLUX_TEAM), adapt_acl.READ_MASK))

    def test_everyone_and_authenticated_users_are_implicit(self):
        sd = adapt_acl.parse(fx.build_sd(aces=[(True, adapt_acl.AUTHENTICATED_USERS, fx.READ)]))
        self.assertTrue(adapt_acl.access_check(sd, self.token(fx.BOB), adapt_acl.READ_MASK))
        sd = adapt_acl.parse(fx.build_sd(aces=[(True, adapt_acl.EVERYONE, fx.READ)]))
        self.assertTrue(adapt_acl.access_check(sd, self.token(), adapt_acl.READ_MASK))

    def test_generic_read_ace_grants_read(self):
        sd = adapt_acl.parse(fx.build_sd(aces=[(True, fx.FLUX_TEAM, adapt_acl.GENERIC_READ)]))
        self.assertTrue(adapt_acl.access_check(sd, self.token(fx.FLUX_TEAM), adapt_acl.READ_MASK))
        self.assertFalse(adapt_acl.access_check(sd, self.token(fx.FLUX_TEAM), adapt_acl.WRITE_MASK))

    def test_null_dacl_grants_everything_and_empty_dacl_nothing(self):
        null_dacl = adapt_acl.parse(fx.build_sd(dacl=False))
        empty_dacl = adapt_acl.parse(fx.build_sd(aces=[]))
        self.assertTrue(adapt_acl.access_check(null_dacl, self.token(), adapt_acl.WRITE_MASK))
        self.assertFalse(adapt_acl.access_check(empty_dacl, self.token(fx.DOMAIN_ADMINS), adapt_acl.READ_MASK))

    def test_explain_reports_the_deciding_ace(self):
        rows = adapt_acl.explain(self.team_sd, self.token(fx.JANE, fx.FLUX_TEAM), adapt_acl.READ_MASK)
        self.assertEqual(rows[0]['effect'], 'skipped')          # domain admins, not in token
        self.assertTrue(rows[1]['effect'].startswith('granted'))


class UncPathTests(SimpleTestCase):

    def test_share_relative_to_unc(self):
        with self.settings(ADAPT_SMB_HOST='snr18', ADAPT_SMB_SHARE='Adapt'):
            self.assertEqual(adapt_acl.unc_path('Flux Measurements/2024'), '\\\\snr18\\Adapt\\Flux Measurements\\2024')
            self.assertEqual(adapt_acl.unc_path(''), '\\\\snr18\\Adapt')
            self.assertEqual(adapt_acl.unc_path(None), '\\\\snr18\\Adapt')
