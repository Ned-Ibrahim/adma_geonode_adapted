"""One place that answers "may this user read or write this object?".

Two regimes, chosen per object:

- Anything that is not an ADAPT reference keeps ADMA's own rule: the owner may,
  the public may read, nobody else.
- An ADAPT folder or file is decided by the share's own security descriptor,
  evaluated for the user's own Active Directory SIDs (see adapt_acl and
  directory). The descriptor used is the one on the nearest ancestor folder
  that has one stored; files take their parent's, which is what NTFS
  inheritance produces on this share.

Fail closed: an ADAPT object with no stored descriptor, or a user with no stored
SIDs, is not accessible. Superusers bypass the ADAPT check so an administrator
can always see what is catalogued.
"""
from functools import lru_cache
from typing import Iterable, List, Optional

from . import adapt_acl
from .models import DirectoryIdentity, File, Folder, FolderAcl


def _is_adapt(obj) -> bool:
    return bool(getattr(obj, 'is_third_party', False) and getattr(obj, 'third_party_source', None) == 'adapt')


def _folder_of(obj) -> Optional[Folder]:
    if isinstance(obj, Folder):
        return obj
    return getattr(obj, 'folder', None)


def token_sids(user) -> Optional[frozenset]:
    """The user's SIDs, or None when no directory identity has been recorded."""
    if user is None or not getattr(user, 'is_authenticated', False):
        return None
    cached = getattr(user, '_adma_token_sids', None)
    if cached is not None:
        return cached
    try:
        identity = user.directory_identity
    except DirectoryIdentity.DoesNotExist:
        user._adma_token_sids = frozenset()
        return None
    sids = set(identity.group_sids or [])
    if identity.sid:
        sids.add(identity.sid)
    token = frozenset(sids)
    user._adma_token_sids = token
    return token or None


def effective_descriptor(folder: Optional[Folder]):
    """Walk up from `folder` to the nearest FolderAcl; returns (folder, SecurityDescriptor) or (None, None)."""
    current = folder
    while current is not None:
        try:
            acl = current.acl
        except FolderAcl.DoesNotExist:
            acl = None
        if acl is not None and acl.descriptor:
            return current, _parse_cached(bytes(acl.descriptor))
        current = current.parent
    return None, None


@lru_cache(maxsize=2048)
def _parse_cached(blob: bytes):
    return adapt_acl.parse(blob)


def _adapt_check(user, obj, mask: int) -> bool:
    if getattr(user, 'is_superuser', False):
        return True
    token = token_sids(user)
    if not token:
        return False
    _source, descriptor = effective_descriptor(_folder_of(obj))
    if descriptor is None:
        return False
    return adapt_acl.access_check(descriptor, token, mask)


def can_read(user, obj) -> bool:
    if obj is None or user is None or not getattr(user, 'is_authenticated', False):
        return False
    if obj.owner_id == user.id or getattr(obj, 'is_public', False):
        return True
    if _is_adapt(obj):
        return _adapt_check(user, obj, adapt_acl.READ_MASK)
    return False


def can_write(user, obj) -> bool:
    if obj is None or user is None or not getattr(user, 'is_authenticated', False):
        return False
    if obj.owner_id == user.id:
        return True
    if _is_adapt(obj):
        return _adapt_check(user, obj, adapt_acl.WRITE_MASK)
    return False


def filter_readable(user, objects: Iterable) -> List:
    """Keep only the objects `user` may read. Evaluates in Python; ADAPT trees are hundreds of folders, not millions."""
    return [obj for obj in objects if can_read(user, obj)]


def readable_adapt_roots(user) -> List[Folder]:
    """Top-level ADAPT team folders the user may list. Used by the dashboard's third-party panel."""
    roots = Folder.objects.filter(
        is_third_party=True, third_party_source='adapt', parent__isnull=True, deletion_in_progress=False,
    )
    visible = []
    for root in roots:
        if can_read(user, root):
            visible.append(root)
            continue
        # The ADAPT root itself is usually readable by everyone on the share, but if
        # it is not, a user may still hold rights on a team folder beneath it.
        if any(can_read(user, team) for team in root.subfolders.filter(deletion_in_progress=False)):
            visible.append(root)
    return visible
