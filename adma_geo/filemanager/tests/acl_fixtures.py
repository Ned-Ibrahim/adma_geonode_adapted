"""Build synthetic NTFS security descriptors for tests, with the same library that parses the real ones."""
from smbprotocol.security_descriptor import (
    AccessAllowedAce, AccessDeniedAce, AclPacket, SDControl, SIDPacket, SMB2CreateSDBuffer,
)

from filemanager import adapt_acl

DOMAIN = 'S-1-5-21-1000-2000-3000'
ADMIN_USER = f'{DOMAIN}-500'
JANE = f'{DOMAIN}-1101'
BOB = f'{DOMAIN}-1102'
FLUX_TEAM = f'{DOMAIN}-2001'
SOILS_TEAM = f'{DOMAIN}-2002'
DOMAIN_ADMINS = f'{DOMAIN}-512'

READ = adapt_acl.FILE_GENERIC_READ | adapt_acl.FILE_EXECUTE
MODIFY = READ | adapt_acl.FILE_GENERIC_WRITE | adapt_acl.DELETE
FULL = adapt_acl.FILE_ALL_ACCESS


def sid(s):
    packet = SIDPacket()
    packet.from_string(s)
    return packet


def ace(allow, sid_string, mask, flags=0):
    packet = AccessAllowedAce() if allow else AccessDeniedAce()
    packet['ace_flags'] = flags
    packet['mask'] = mask
    packet['sid'] = sid(sid_string)
    return packet


def build_sd(owner=ADMIN_USER, group=DOMAIN_ADMINS, aces=(), dacl=True):
    """aces: iterable of (allow, sid, mask[, flags]). Returns the packed self-relative descriptor."""
    sd = SMB2CreateSDBuffer()
    sd['control'].set_flag(SDControl.SELF_RELATIVE)
    sd.set_owner(sid(owner))
    sd.set_group(sid(group))
    if dacl:
        acl = AclPacket()
        acl['aces'] = [ace(*entry) for entry in aces]
        sd.set_dacl(acl)
    return sd.pack()


def team_folder_sd(team_sid, extra=()):
    """The shape SNR18 team folders are expected to have: admins full, the team modify, nobody else."""
    return build_sd(aces=[
        (True, DOMAIN_ADMINS, FULL),
        (True, team_sid, MODIFY),
        *extra,
    ])
