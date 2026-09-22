"""Active Directory identity for ADMA users.

The share's security descriptors speak in SIDs. To evaluate them for a person,
ADMA needs that person's own SID and every group SID they carry, including
nested groups. The domain controller computes exactly that as the constructed
attribute `tokenGroups`, so ADMA asks for it at login and stores it on
`DirectoryIdentity`.

Login itself is django-auth-ldap. This module hangs off its `populate_user`
signal, which fires with an open, bound LDAP connection every time a user
authenticates. `refresh_identity` does the same thing outside a login, for the
daily refresh and for the runbook.
"""
import logging
import struct
from typing import Iterable, List, Optional, Tuple

from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)


def sid_to_string(blob: bytes) -> str:
    """Binary SID ([MS-DTYP] 2.4.2.2) to its S-1-... form."""
    if not blob or len(blob) < 8:
        raise ValueError('SID blob too short')
    revision = blob[0]
    count = blob[1]
    if len(blob) != 8 + 4 * count:
        raise ValueError('SID blob length does not match sub-authority count')
    authority = int.from_bytes(blob[2:8], 'big')
    subs = struct.unpack('<%dI' % count, blob[8:])
    return 'S-%d-%d%s' % (revision, authority, ''.join('-%d' % s for s in subs))


def string_to_sid(sid: str) -> bytes:
    """Inverse of sid_to_string, used by tests and the check command."""
    parts = sid.split('-')
    if len(parts) < 3 or parts[0] != 'S':
        raise ValueError('not a SID string')
    revision = int(parts[1])
    authority = int(parts[2])
    subs = [int(p) for p in parts[3:]]
    return bytes([revision, len(subs)]) + authority.to_bytes(6, 'big') + struct.pack('<%dI' % len(subs), *subs)


def extract_sids(attrs: dict) -> Tuple[Optional[str], List[str]]:
    """Pull objectSid and tokenGroups out of an LDAP attribute dict (values are bytes)."""
    object_sid = None
    raw = attrs.get('objectSid') or []
    if raw:
        object_sid = sid_to_string(raw[0])
    groups = []
    for blob in attrs.get('tokenGroups') or []:
        try:
            groups.append(sid_to_string(blob))
        except ValueError:
            logger.warning('Skipping unreadable tokenGroups entry')
    return object_sid, sorted(set(groups))


def store_identity(user, dn: Optional[str], object_sid: Optional[str], group_sids: Iterable[str]):
    """Write (or update) the DirectoryIdentity row for a user."""
    from .models import DirectoryIdentity
    identity, _ = DirectoryIdentity.objects.update_or_create(
        user=user,
        defaults={
            'dn': dn or '',
            'sid': object_sid or '',
            'group_sids': sorted(set(group_sids)),
            'fetched_at': timezone.now(),
        },
    )
    return identity


def fetch_token_sids(connection, dn: str) -> Tuple[Optional[str], List[str]]:
    """Base-scope search for the constructed attributes on a user's own entry.

    `tokenGroups` is only returned for a base search of the object itself, which
    is why django-auth-ldap's normal user search does not carry it.
    """
    import ldap  # python-ldap; only needed on hosts that actually talk to AD
    results = connection.search_s(dn, ldap.SCOPE_BASE, '(objectClass=*)', ['objectSid', 'tokenGroups'])
    if not results:
        return None, []
    _dn, attrs = results[0]
    return extract_sids(attrs)


def on_populate_user(sender, user=None, ldap_user=None, **kwargs):
    """django_auth_ldap.backend.populate_user receiver: record SIDs at every login."""
    if user is None or ldap_user is None:
        return
    try:
        object_sid, group_sids = fetch_token_sids(ldap_user.connection, ldap_user.dn)
    except Exception:
        logger.exception('Could not read tokenGroups for %s', getattr(user, 'username', '?'))
        return
    # populate_user fires before the user row is saved on first login. Defer the
    # write until it has a primary key.
    if user.pk is None:
        user._pending_directory_identity = (ldap_user.dn, object_sid, group_sids)
        return
    store_identity(user, ldap_user.dn, object_sid, group_sids)


def flush_pending_identity(user):
    """Called after save (see signals) to persist an identity captured pre-save."""
    pending = getattr(user, '_pending_directory_identity', None)
    if pending and user.pk:
        dn, object_sid, group_sids = pending
        store_identity(user, dn, object_sid, group_sids)
        del user._pending_directory_identity


def refresh_identity(user, connection=None):
    """Re-read a user's SIDs with a bound service connection. Returns the identity or None."""
    from .models import DirectoryIdentity
    try:
        identity = user.directory_identity
    except DirectoryIdentity.DoesNotExist:
        identity = None
    dn = identity.dn if identity else None
    if not dn:
        logger.info('No DN recorded for %s; they must log in once via LDAP first', user.username)
        return None
    close = False
    if connection is None:
        connection = bind_service_connection()
        close = True
    try:
        object_sid, group_sids = fetch_token_sids(connection, dn)
    finally:
        if close:
            connection.unbind_s()
    return store_identity(user, dn, object_sid, group_sids)


def bind_service_connection():
    """A python-ldap connection bound as the configured service account."""
    import ldap
    uri = settings.AUTH_LDAP_SERVER_URI
    connection = ldap.initialize(uri)
    for option, value in getattr(settings, 'AUTH_LDAP_CONNECTION_OPTIONS', {}).items():
        connection.set_option(option, value)
    connection.simple_bind_s(settings.AUTH_LDAP_BIND_DN, settings.AUTH_LDAP_BIND_PASSWORD)
    return connection


def ldap_enabled() -> bool:
    return bool(getattr(settings, 'AUTH_LDAP_SERVER_URI', ''))
