"""Read and evaluate NTFS security descriptors on the ADAPT share.

ADMA reaches the share through one service account, so the file server never
sees the person behind a request. To give each person exactly the access the
share gives them, ADMA fetches each folder's security descriptor (as the service
account, which only needs READ_CONTROL), stores it, and evaluates it against the
person's own Active Directory SIDs using the same algorithm Windows uses.

Nothing here is a copy typed in by hand. The DACL is SNR18's, the SIDs are the
domain controller's, and the check is [MS-DTYP] 2.5.3.2 (Access Check
Algorithm Pseudocode), simplified to the subset that applies to file-system
objects: allow and deny ACEs, ordered, with inherit-only entries skipped.

Two layers live here on purpose:

- pure functions (`parse`, `access_check`, `explain`) that take bytes and SIDs
  and have no I/O, so they can be tested with synthetic descriptors;
- `fetch_security_descriptor`, the one function that talks SMB.
"""
import logging
from dataclasses import dataclass
from typing import Iterable, List, Optional, Tuple

from django.conf import settings

from smbprotocol.security_descriptor import AceFlags, AceType, SMB2CreateSDBuffer

logger = logging.getLogger(__name__)

# File and directory access rights, winnt.h / [MS-DTYP] 2.4.3.
FILE_READ_DATA = 0x0001            # on a directory: FILE_LIST_DIRECTORY
FILE_WRITE_DATA = 0x0002           # on a directory: FILE_ADD_FILE
FILE_APPEND_DATA = 0x0004          # on a directory: FILE_ADD_SUBDIRECTORY
FILE_READ_EA = 0x0008
FILE_WRITE_EA = 0x0010
FILE_EXECUTE = 0x0020              # on a directory: FILE_TRAVERSE
FILE_DELETE_CHILD = 0x0040
FILE_READ_ATTRIBUTES = 0x0080
FILE_WRITE_ATTRIBUTES = 0x0100
DELETE = 0x00010000
READ_CONTROL = 0x00020000
WRITE_DAC = 0x00040000
WRITE_OWNER = 0x00080000
SYNCHRONIZE = 0x00100000
GENERIC_ALL = 0x10000000
GENERIC_EXECUTE = 0x20000000
GENERIC_WRITE = 0x40000000
GENERIC_READ = 0x80000000

STANDARD_RIGHTS_REQUIRED = 0x000F0000
FILE_ALL_ACCESS = STANDARD_RIGHTS_REQUIRED | SYNCHRONIZE | 0x1FF
FILE_GENERIC_READ = READ_CONTROL | FILE_READ_DATA | FILE_READ_ATTRIBUTES | FILE_READ_EA | SYNCHRONIZE
FILE_GENERIC_WRITE = (READ_CONTROL | FILE_WRITE_DATA | FILE_WRITE_ATTRIBUTES | FILE_WRITE_EA
                      | FILE_APPEND_DATA | SYNCHRONIZE)
FILE_GENERIC_EXECUTE = READ_CONTROL | FILE_READ_ATTRIBUTES | FILE_EXECUTE | SYNCHRONIZE

# What ADMA asks for. Read is "list this folder / read this file"; write is
# "create a file or a folder in here", which is what an upload does.
READ_MASK = FILE_READ_DATA
WRITE_MASK = FILE_WRITE_DATA | FILE_APPEND_DATA

# SIDs every authenticated domain user carries implicitly. The domain controller
# does not put these in tokenGroups, so the check adds them itself.
EVERYONE = 'S-1-1-0'
AUTHENTICATED_USERS = 'S-1-5-11'
IMPLICIT_SIDS = frozenset({EVERYONE, AUTHENTICATED_USERS})


@dataclass(frozen=True)
class Ace:
    allow: bool
    flags: int
    mask: int
    sid: str

    @property
    def inherit_only(self):
        return bool(self.flags & AceFlags.INHERIT_ONLY_ACE)

    @property
    def inherited(self):
        return bool(self.flags & AceFlags.INHERITED_ACE)


@dataclass(frozen=True)
class SecurityDescriptor:
    owner: Optional[str]
    group: Optional[str]
    dacl_present: bool
    aces: Tuple[Ace, ...]


class DescriptorError(ValueError):
    """The bytes are not a security descriptor this module can read."""


def parse(blob: bytes) -> SecurityDescriptor:
    """Parse a self-relative SECURITY_DESCRIPTOR as returned by SMB2 QUERY_INFO."""
    if not blob:
        raise DescriptorError('empty security descriptor')
    sd = SMB2CreateSDBuffer()
    try:
        sd.unpack(blob)
        owner = sd.get_owner()
        group = sd.get_group()
        dacl = sd.get_dacl()
    except Exception as exc:  # smbprotocol raises assorted struct/Value errors
        raise DescriptorError(f'unreadable security descriptor: {exc}') from exc

    aces: List[Ace] = []
    if dacl is not None:
        for raw in dacl['aces'].get_value():
            ace_type = raw['ace_type'].get_value()
            if ace_type == AceType.ACCESS_ALLOWED_ACE_TYPE:
                allow = True
            elif ace_type == AceType.ACCESS_DENIED_ACE_TYPE:
                allow = False
            else:
                # Audit, alarm and object ACEs do not grant or deny file access.
                continue
            aces.append(Ace(
                allow=allow,
                flags=raw['ace_flags'].get_value(),
                mask=raw['mask'].get_value(),
                sid=str(raw['sid'].get_value()),
            ))
    return SecurityDescriptor(
        owner=str(owner) if owner is not None else None,
        group=str(group) if group is not None else None,
        dacl_present=dacl is not None,
        aces=tuple(aces),
    )


def map_generic(mask: int) -> int:
    """Translate GENERIC_* bits into file-specific rights, as the object type mapping does."""
    specific = mask & 0x0FFFFFFF
    if mask & GENERIC_READ:
        specific |= FILE_GENERIC_READ
    if mask & GENERIC_WRITE:
        specific |= FILE_GENERIC_WRITE
    if mask & GENERIC_EXECUTE:
        specific |= FILE_GENERIC_EXECUTE
    if mask & GENERIC_ALL:
        specific |= FILE_ALL_ACCESS
    return specific


def token_for(sids: Iterable[str]) -> frozenset:
    """The set of SIDs a signed-in domain user presents, including the implicit ones."""
    return frozenset(sids) | IMPLICIT_SIDS


def access_check(sd: SecurityDescriptor, token: Iterable[str], requested: int) -> bool:
    """Return True if a token holding `token` SIDs is granted every bit of `requested`.

    Walks the DACL in order. A deny ACE for a SID in the token that covers any
    still-outstanding requested bit ends the check with a denial. Allow ACEs
    accumulate. The walk stops as soon as everything requested is granted.
    A missing DACL grants everything (Windows semantics for a NULL DACL); an
    empty one grants nothing.
    """
    if not requested:
        return True
    if not sd.dacl_present:
        return True
    token = token_for(token)
    granted = 0
    for ace in sd.aces:
        if ace.inherit_only or ace.sid not in token:
            continue
        mask = map_generic(ace.mask)
        remaining = requested & ~granted
        if not ace.allow:
            if mask & remaining:
                return False
        else:
            granted |= mask & requested
            if granted == requested:
                return True
    return granted == requested


def explain(sd: SecurityDescriptor, token: Iterable[str], requested: int) -> List[dict]:
    """Same walk as access_check, but returns what each ACE did. For the check command."""
    token = token_for(token)
    granted = 0
    rows = []
    decided = None
    for ace in sd.aces:
        row = {
            'sid': ace.sid, 'allow': ace.allow, 'mask': map_generic(ace.mask),
            'inherit_only': ace.inherit_only, 'in_token': ace.sid in token, 'effect': 'skipped',
        }
        if decided is None and not ace.inherit_only and ace.sid in token:
            mask = row['mask']
            remaining = requested & ~granted
            if not ace.allow:
                if mask & remaining:
                    row['effect'] = 'denied'
                    decided = False
                else:
                    row['effect'] = 'deny not relevant'
            else:
                new = mask & requested & ~granted
                granted |= mask & requested
                row['effect'] = f'granted 0x{new:x}' if new else 'nothing new'
                if granted == requested:
                    decided = True
        rows.append(row)
    return rows


# --------------------------------------------------------------------------
# The one function that talks to the file server.
# --------------------------------------------------------------------------

def _smb_settings():
    return {
        'host': getattr(settings, 'ADAPT_SMB_HOST', 'snr18'),
        'share': getattr(settings, 'ADAPT_SMB_SHARE', 'Adapt'),
        'username': getattr(settings, 'ADAPT_SMB_USERNAME', '') or None,
        'password': getattr(settings, 'ADAPT_SMB_PASSWORD', '') or None,
        'port': int(getattr(settings, 'ADAPT_SMB_PORT', 445)),
        'timeout': int(getattr(settings, 'ADAPT_SMB_TIMEOUT', 30)),
    }


def unc_path(rel_path: str, host: Optional[str] = None, share: Optional[str] = None) -> str:
    """Share-relative path (as stored in Folder.third_party_id) to a UNC path."""
    cfg = _smb_settings()
    rel = (rel_path or '').replace('/', '\\').strip('\\')
    base = f"\\\\{host or cfg['host']}\\{share or cfg['share']}"
    return f'{base}\\{rel}' if rel else base


def fetch_security_descriptor(rel_path: str) -> bytes:
    """Fetch owner, group and DACL of a directory on the share, as the service account.

    Opens the directory with READ_CONTROL only, which any account that can list
    the folder normally has, and issues SMB2 QUERY_INFO with InfoType SECURITY.
    Returns the raw self-relative descriptor for `parse`.
    """
    # Imported here so the pure functions above stay importable without a
    # network stack, and so tests can replace this function wholesale.
    from smbclient._io import SMBDirectoryIO, SMBFileTransaction
    from smbprotocol.open import (
        FilePipePrinterAccessMask, InfoAdditionalInformation, InfoType,
        SMB2QueryInfoRequest, SMB2QueryInfoResponse,
    )

    cfg = _smb_settings()
    path = unc_path(rel_path)
    raw = SMBDirectoryIO(
        path, mode='r', share_access='rwd',
        desired_access=FilePipePrinterAccessMask.READ_CONTROL,
        username=cfg['username'], password=cfg['password'], port=cfg['port'],
        connection_timeout=cfg['timeout'],
    )
    with SMBFileTransaction(raw) as transaction:
        request = SMB2QueryInfoRequest()
        request['info_type'] = InfoType.SMB2_0_INFO_SECURITY
        request['output_buffer_length'] = 65535
        request['additional_information'] = (
            InfoAdditionalInformation.OWNER_SECURTIY_INFORMATION
            | InfoAdditionalInformation.GROUP_SECURITY_INFORMATION
            | InfoAdditionalInformation.DACL_SECURITY_INFORMATION
        )
        request['file_id'] = transaction.raw.fd.file_id

        def _receive(req):
            response = transaction.raw.fd.connection.receive(req)
            info = SMB2QueryInfoResponse()
            info.unpack(response['data'].get_value())
            return info['buffer'].get_value()

        transaction += (request, _receive)
    return transaction.results[0]
