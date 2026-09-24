"""One place that answers "may this user read or write this object?".

Anything that is not an ADAPT reference keeps ADMA's own rule: the owner may,
the public may read, nobody else.

ADAPT is different. ADMA mounts the share with one account that can read all of
it, so the share's own permissions do not reach ADMA users. Access comes from
FolderGrant rows that an administrator manages:

- A grant on a folder covers that folder, its files and everything beneath it.
- A user with a grant somewhere below a folder may open that folder to navigate
  down, but sees only the branches that lead to their grants and none of the
  files beside them.
- No grant, no access. ADAPT rows ignore is_public and ownership: they belong to
  the sync account, and older rows were created public.
- Superusers see everything.
"""
from collections import defaultdict
from typing import Iterable, List, Optional, Set

from django.db.models import Q

from .models import Folder, FolderGrant


def _is_adapt(obj) -> bool:
    return bool(getattr(obj, 'is_third_party', False) and getattr(obj, 'third_party_source', None) == 'adapt')


def _folder_of(obj) -> Optional[Folder]:
    if isinstance(obj, Folder):
        return obj
    return getattr(obj, 'folder', None)


class _Grants:
    """One user's ADAPT grants, loaded once per request and cached on the user object."""

    def __init__(self, user):
        self._parents = {}
        self.read = set()
        self.write = set()
        rows = FolderGrant.objects.filter(Q(user=user) | Q(group__in=user.groups.all()))
        for folder_id, can_write in rows.values_list('folder_id', 'can_write'):
            self.read.add(folder_id)
            if can_write:
                self.write.add(folder_id)
        # Folders above a grant: open for navigation only.
        self.on_path = set()
        for folder_id in self.read:
            self.on_path.update(self.chain(folder_id)[1:])

    def chain(self, folder_id) -> List:
        """`folder_id` followed by the ids of all its ancestors, nearest first."""
        ids = []
        current = folder_id
        while current is not None and current not in ids:
            ids.append(current)
            if current not in self._parents:
                self._parents[current] = (
                    Folder.objects.filter(pk=current).values_list('parent_id', flat=True).first()
                )
            current = self._parents[current]
        return ids

    def covers(self, folder: Optional[Folder], granted: set) -> bool:
        if folder is None or not granted:
            return False
        return any(folder_id in granted for folder_id in self.chain(folder.pk))


def _grants(user) -> _Grants:
    cached = getattr(user, '_adma_folder_grants', None)
    if cached is None:
        cached = _Grants(user)
        user._adma_folder_grants = cached
    return cached


def _signed_in(user) -> bool:
    return user is not None and getattr(user, 'is_authenticated', False)


def can_read(user, obj) -> bool:
    if obj is None or not _signed_in(user):
        return False
    if not _is_adapt(obj):
        return obj.owner_id == user.id or getattr(obj, 'is_public', False)
    if user.is_superuser:
        return True
    grants = _grants(user)
    if grants.covers(_folder_of(obj), grants.read):
        return True
    return isinstance(obj, Folder) and obj.pk in grants.on_path


def can_write(user, obj) -> bool:
    if obj is None or not _signed_in(user):
        return False
    if not _is_adapt(obj):
        return obj.owner_id == user.id
    if user.is_superuser:
        return True
    grants = _grants(user)
    return grants.covers(_folder_of(obj), grants.write)


def can_list_files(user, folder) -> bool:
    """Whether the files directly inside `folder` are visible, not just the folder itself."""
    if not _is_adapt(folder):
        return can_read(user, folder)
    if not _signed_in(user):
        return False
    if user.is_superuser:
        return True
    grants = _grants(user)
    return grants.covers(folder, grants.read)


def filter_readable(user, objects: Iterable) -> List:
    """Keep only the objects `user` may read. Evaluates in Python; ADAPT trees are hundreds of folders, not millions."""
    return [obj for obj in objects if can_read(user, obj)]


def readable_adapt_roots(user) -> List[Folder]:
    """Top-level ADAPT folders the user may open. Used by the dashboard's third-party panel."""
    roots = Folder.objects.filter(
        is_third_party=True, third_party_source='adapt', parent__isnull=True, deletion_in_progress=False,
    )
    return filter_readable(user, roots)


def adapt_folder_ids(user, include_path=False) -> Set:
    """Ids of the ADAPT folders the user's read grants cover: each granted folder and its whole subtree.

    For queries over many rows at once, such as search, where calling can_read
    row by row would be too slow. With `include_path`, the folders above a grant
    are included too, as can_read opens them for navigation. Superusers are not
    special-cased here; callers let them see all of ADAPT directly.
    """
    if not _signed_in(user):
        return set()
    grants = _grants(user)
    if not grants.read:
        return set()
    # The ADAPT tree is a few hundred folders, so it is loaded whole.
    children = defaultdict(list)
    for pk, parent_id in Folder.objects.filter(third_party_source='adapt').values_list('id', 'parent_id'):
        children[parent_id].append(pk)
    covered = set()
    pending = list(grants.read)
    while pending:
        pk = pending.pop()
        if pk not in covered:
            covered.add(pk)
            pending.extend(children.get(pk, ()))
    if include_path:
        covered |= grants.on_path
    return covered
