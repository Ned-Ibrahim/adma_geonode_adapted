"""Reading the ADAPT share: paths, what stays hidden, and folder listings.

ADMA does not keep a copy of the share's catalogue. A folder or file on the
share is named by its path relative to the mount, with forward slashes:
"Soils/2024/cores.csv", and "" for the whole share. Pages, grants and records
all use that path.

Every path that reaches ADMA from a browser, an API client, the admin or a
command is untrusted. normalize() turns it into the one spelling ADMA uses or
refuses it, and resolve() then confirms that it lands inside the mount before
anything touches the share.

The share is a Windows share, so names compare without case: key() is the form
to compare, normalize() keeps the spelling it was given, and canonical() finds
the spelling the share itself uses.
"""
import os

from django.conf import settings
from django.urls import reverse

# Directory names never shown, wherever they appear: ADMA's own storage on the
# share and Windows system folders.
EXCLUDED_NAMES = frozenset({
    'adma_geo_production',      # ADMA's own storage on the share (pg_data, media)
    'pg_data',                  # Postgres internals, never user data
    '$RECYCLE.BIN',
    'System Volume Information',
    'lost+found',
    '.snapshot',
})

# File names never shown, wherever they appear.
EXCLUDED_FILES = frozenset({
    'Thumbs.db',
    'desktop.ini',
    '.DS_Store',
})


_EXCLUDED_NAMES = frozenset(n.casefold() for n in EXCLUDED_NAMES)
_EXCLUDED_FILES = frozenset(n.casefold() for n in EXCLUDED_FILES)


class AdaptPathError(ValueError):
    """A path that does not name a place on the share ADMA may show."""


def is_hidden(name, is_dir=True):
    """True for names ADMA never shows: hidden and Office lock files, system folders, ADMA's own storage.

    Without case, as Windows compares names: "PG_DATA" is "pg_data".
    """
    if name.startswith('.') or name.startswith('~$'):
        return True
    folded = name.casefold()
    if folded in _EXCLUDED_NAMES:
        return True
    return not is_dir and folded in _EXCLUDED_FILES


def normalize(rel):
    """`rel` as ADMA spells a path on the share, or AdaptPathError.

    Backslashes become slashes, surrounding and repeated slashes go, so
    "\\\\Soils\\\\2024\\\\" and "Soils//2024" are both "Soils/2024". Refused: "." and
    "..", control characters, ":" (a drive letter, or an NTFS alternate data
    stream), and any hidden or excluded name along the way.
    """
    text = '' if rel is None else str(rel).replace('\\', '/')
    if any(ord(ch) < 32 for ch in text):
        raise AdaptPathError('The path contains control characters')
    parts = [part for part in text.split('/') if part]
    for part in parts:
        if part in ('.', '..'):
            raise AdaptPathError(f'"{part}" is not allowed in a path on the ADAPT share')
        if ':' in part:
            raise AdaptPathError(f'"{part}" is not a name on the ADAPT share')
        if is_hidden(part):
            raise AdaptPathError(f'"{part}" is not shown in ADMA')
    return '/'.join(parts)


def key(rel):
    """`rel` in the form paths compare in: a tuple of names without case. () is the whole share."""
    return tuple(part.casefold() for part in rel.split('/')) if rel else ()


def join(rel, name):
    return f'{rel}/{name}' if rel else name


def parent(rel):
    return rel.rpartition('/')[0]


def name(rel):
    return rel.rpartition('/')[2]


def is_within(rel, ancestor):
    """True if `rel` is `ancestor` or anywhere beneath it, without case."""
    k, a = key(rel), key(ancestor)
    return k[:len(a)] == a


def breadcrumbs(rel):
    """[(name, path)] for every folder from the top of the share down to `rel`, `rel` included."""
    crumbs = []
    path = ''
    for part in rel.split('/') if rel else ():
        path = join(path, part)
        crumbs.append((part, path))
    return crumbs


def mount_root():
    return os.path.realpath(getattr(settings, 'ADAPT_MOUNT', '/adapt'))


def contained(path, root):
    try:
        return os.path.commonpath([os.path.realpath(path), root]) == root
    except ValueError:
        # Different drives, or a mix of absolute and relative paths.
        return False


def resolve(rel):
    """The absolute path of `rel` on the mount, or AdaptPathError if it would land outside it.

    Symlinks are followed before the check, so a link on the share that points
    out of it is refused rather than served.
    """
    rel = normalize(rel)
    root = mount_root()
    path = os.path.realpath(os.path.join(root, *rel.split('/'))) if rel else root
    if not contained(path, root):
        raise AdaptPathError(f'Refusing "{rel}": it resolves outside the ADAPT share')
    return path


def canonical(rel):
    """`rel` spelled as the share spells it, or AdaptPathError if a part of it is not there.

    Each part is matched against its folder's listing, exactly first and then
    without case. For paths typed by a person or read from an export, where
    "soils/2024" should find "Soils/2024".
    """
    spelled = ''
    for part in normalize(rel).split('/') if rel else ():
        here = resolve(spelled)
        try:
            names = os.listdir(here)
        except OSError:
            raise AdaptPathError(f'"{spelled or "/"}" is not a folder on the ADAPT share')
        found = part if part in names else next(
            (n for n in sorted(names) if n.casefold() == part.casefold()), None)
        if found is None:
            raise AdaptPathError(f'"{join(spelled, part)}" is not on the ADAPT share')
        spelled = join(spelled, found)
    return spelled


def is_folder(rel):
    """True if `rel` is a folder on the share that ADMA may show."""
    try:
        return os.path.isdir(resolve(rel))
    except AdaptPathError:
        return False


def folder_url(rel):
    """The page that lists folder `rel` on the share."""
    return reverse('filemanager:adapt_folder', args=[rel]) if rel else reverse('filemanager:adapt_root')


def file_url(rel):
    """The address that opens file `rel` on the share: it makes the file's record and shows its page."""
    return reverse('filemanager:adapt_open', args=[rel])
