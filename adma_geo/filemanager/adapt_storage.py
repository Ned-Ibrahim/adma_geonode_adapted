"""Write-back to the mounted ADAPT share.

ADAPT is an SMB share mounted at ``settings.ADAPT_MOUNT``. ADMA shows it as it
is (see adapt_share) and keeps no row for an ordinary file or folder on it, so
an upload or a new folder lands on the share and nothing else changes. A GIS
file is the exception: its processing and its map layers hang off a File
record, so an upload of one gets that record at once, owned by the uploader.

This module is the only place in ADMA that writes to the share, and it holds
two rules so that the rest of the code does not have to:

1.  A folder is named by its path on the share, which arrives from a browser
    or an API client and is untrusted. adapt_share.resolve() confirms that it
    lands inside the mount before anything is touched.
2.  A write never overwrites. Names are reserved with ``O_EXCL`` (and with
    ``mkdir`` for folders), which the server applies atomically, so two
    simultaneous uploads and a file dropped on the share out of band all lose
    the race instead of clobbering each other.

Who may write where is not decided here: callers check
``permissions.can_write_path`` first (see ``views.upload_target``).
"""
import logging
import mimetypes
import os
import uuid
from collections import namedtuple
from pathlib import Path

from django.conf import settings

from . import adapt_share

logger = logging.getLogger(__name__)

# third_party_id of the ADAPT root folder. It is a sentinel, not a relative path:
# the root maps to the mount point itself.
ROOT_SENTINEL = 'adapt_root'

# How many "name_1", "name_2" variants to try before falling back to a random
# suffix. Matches the ceiling in views.generate_unique_name.
MAX_NAME_ATTEMPTS = 1000

# Characters no SMB share will store, plus the POSIX separators.
_ILLEGAL_CHARS = set('/\\:*?"<>|\0')

# Windows refuses these as file names in any directory, with or without an
# extension. The share is Windows-backed, so ADMA has to refuse them too.
_WINDOWS_RESERVED = frozenset(
    {'CON', 'PRN', 'AUX', 'NUL'}
    | {f'COM{i}' for i in range(1, 10)}
    | {f'LPT{i}' for i in range(1, 10)}
)


class AdaptWriteError(Exception):
    """A write to the ADAPT share was refused or failed."""


class AdaptWriteDisabled(AdaptWriteError):
    """Write-back is not enabled for this deployment."""


WRITE_DISABLED_MESSAGE = 'Uploading to the ADAPT share is turned off on this server.'


# --------------------------------------------------------------- introspection

def mount_root():
    return os.path.realpath(getattr(settings, 'ADAPT_MOUNT', '/adapt'))


def write_enabled():
    return bool(getattr(settings, 'ADAPT_WRITE_ENABLED', False))


def is_adapt_folder(folder):
    """True if this Folder represents a directory on the ADAPT share."""
    return bool(
        folder is not None
        and folder.is_third_party
        and folder.third_party_source == 'adapt'
    )


def rel_to_mount(abs_path):
    """Path relative to the mount, forward slashes. The identity sync_adapt uses."""
    return os.path.relpath(abs_path, mount_root()).replace(os.sep, '/')


def reference_url(abs_path):
    """The path sync_adapt records in third_party_url for this file.

    sync_adapt joins names onto the mount as configured, without resolving
    symlinks, so the same is done here; otherwise a symlinked mount would make
    the next sync rewrite every uploaded row's path.
    """
    configured = os.path.abspath(getattr(settings, 'ADAPT_MOUNT', '/adapt'))
    return os.path.join(configured, *rel_to_mount(abs_path).split('/'))


def is_gis(name):
    """True for a file ADMA processes into map layers: an extension in settings.GIS_FILE_EXTENSIONS."""
    return os.path.splitext(name)[1].lower() in getattr(settings, 'GIS_FILE_EXTENSIONS', [])


def _listing_changed(rel_dir):
    """Called after every write into folder `rel_dir` on the share. Does nothing yet.

    The cached listing of that folder (adapt_share.forget_listing) is dropped
    here once the listing cache lands, so the write shows at once.
    """


# ------------------------------------------------------------------- path rules

def _normalize(rel):
    try:
        return adapt_share.normalize(rel)
    except adapt_share.AdaptPathError as exc:
        raise AdaptWriteError(str(exc)) from exc


def _assert_mounted(root):
    """Refuse to write into an unmounted /adapt.

    When the CIFS mount is missing, /adapt is an ordinary empty directory inside
    the container. Writing there succeeds and then vanishes with the container,
    which looks exactly like silent data loss. An empty share is indistinguishable
    from a dropped mount, so both are refused.
    """
    if not os.path.isdir(root):
        raise AdaptWriteError(f'ADAPT mount {root} does not exist')
    try:
        if not os.listdir(root):
            raise AdaptWriteError(
                f'ADAPT mount {root} is empty. Is the share mounted? '
                f'Refusing to write into what may be a dropped mount.'
            )
    except OSError as exc:
        raise AdaptWriteError(f'Cannot read ADAPT mount {root}: {exc}') from exc


def _assert_prefix_allowed(abs_path, root):
    prefixes = getattr(settings, 'ADAPT_WRITABLE_PREFIXES', None) or []
    if not prefixes:
        return
    rel = rel_to_mount(abs_path)
    for prefix in prefixes:
        if rel == prefix or rel.startswith(prefix + '/'):
            return
    raise AdaptWriteError(
        f'Write-back to "{rel}" is not allowed. '
        f'Permitted prefixes: {", ".join(prefixes)}'
    )


def assert_writable(rel):
    """Confirm ADMA may write into folder `rel` on the share, and return its absolute path.

    The path is untrusted: one that does not normalize, or that resolves
    outside the mount (through "..", or a symlink on the share), is refused.
    """
    if not write_enabled():
        raise AdaptWriteDisabled(WRITE_DISABLED_MESSAGE)
    root = mount_root()
    _assert_mounted(root)
    try:
        path = adapt_share.resolve(rel)
    except adapt_share.AdaptPathError as exc:
        raise AdaptWriteError(str(exc)) from exc
    _assert_prefix_allowed(path, root)
    if not os.path.isdir(path):
        raise AdaptWriteError(f'"{_normalize(rel)}" is not a folder on the ADAPT share')
    return path


def _check_storable(name):
    """Reject names the share cannot store faithfully.

    Stricter than POSIX needs, because the warehouse is a Windows share. Windows
    silently strips trailing dots and spaces, which would leave the name ADMA
    reports and the name on the share disagreeing with each other permanently.
    """
    if not name or name in ('.', '..'):
        raise AdaptWriteError('Invalid name')
    if len(name) > 255:
        raise AdaptWriteError(f'Name is too long (max 255 characters): {name[:60]}...')
    if _ILLEGAL_CHARS & set(name):
        raise AdaptWriteError(f'Name contains characters the share cannot store: {name}')
    if any(ord(ch) < 32 for ch in name):
        raise AdaptWriteError('Name contains control characters')
    if name != name.strip() or name.endswith('.'):
        raise AdaptWriteError(
            f'Name has leading or trailing whitespace or a trailing dot, which '
            f'Windows would silently strip: "{name}"'
        )
    if Path(name).stem.upper() in _WINDOWS_RESERVED:
        raise AdaptWriteError(f'"{name}" is a reserved name on Windows')


def validate_name(name, is_dir=False):
    """Reject names the share cannot store, and names ADMA never shows (see adapt_share.is_hidden).

    A hidden name written to the share would sit there out of sight of
    everyone using ADMA, so it is not written at all.
    """
    _check_storable(name)
    if adapt_share.is_hidden(name, is_dir=is_dir):
        raise AdaptWriteError(f'"{name}" is hidden in ADMA, so it is not written to the ADAPT share')
    return name


def _candidate_names(preferred, is_dir=False):
    """The preferred name first, then deduplicated variants of it.

    Files keep their extension ("map.shp" -> "map_1.shp"). Directories do not get
    split on a dot, because "v1.2" is a whole directory name, not a stem and a
    suffix.
    """
    yield preferred
    stem, ext = (preferred, '') if is_dir else os.path.splitext(preferred)
    for i in range(1, MAX_NAME_ATTEMPTS + 1):
        yield f'{stem}_{i}{ext}'
    yield f'{stem}_{uuid.uuid4().hex[:8]}{ext}'


def _discard(path):
    """Remove a partially written file. Best effort: the original error wins."""
    try:
        os.unlink(path)
    except OSError as exc:
        logger.error('Could not remove partial ADAPT upload %s: %s', path, exc)


# ----------------------------------------------------------------------- writes

# A file written to the share: its path there, the name it landed under (the
# one asked for, or that name with a _1 suffix), its size in bytes, and its File
# record for a GIS file, else None.
Upload = namedtuple('Upload', 'path name size record')

# A folder upload written to the share: the Uploads, the paths of the folders
# it made, (asked, made) for each top folder that got a suffix, and the paths
# in the upload left out because ADMA would not show them.
Tree = namedtuple('Tree', 'files folders renamed skipped')


def write_upload(rel_dir, uploaded_file, preferred_name):
    """Stream an uploaded file onto the share inside folder `rel_dir`.

    Returns (absolute_path, name_actually_used, size_in_bytes).

    The name is reserved with O_EXCL before a single byte is written, so the
    file on the share is never overwritten and the name cannot be taken by a
    concurrent upload between the check and the write.
    """
    rel_dir = _normalize(rel_dir)
    dir_path = assert_writable(rel_dir)
    validate_name(preferred_name)

    fd = None
    target = None
    for candidate in _candidate_names(preferred_name):
        target = os.path.join(dir_path, candidate)
        try:
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        except FileExistsError:
            continue
        except OSError as exc:
            raise AdaptWriteError(
                f'Cannot create "{candidate}" on the ADAPT share: {exc}'
            ) from exc
        name = candidate
        break
    else:
        raise AdaptWriteError(
            f'Could not find a free name for "{preferred_name}" on the share'
        )

    written = 0
    try:
        with os.fdopen(fd, 'wb') as out:
            for chunk in uploaded_file.chunks():
                out.write(chunk)
                written += len(chunk)
            out.flush()
            os.fsync(out.fileno())
    except BaseException:
        # Includes the client disconnecting mid-upload. A half written file on a
        # system of record is worse than no file, so it does not survive.
        _discard(target)
        raise

    logger.info('Wrote %s bytes to ADAPT share at %s', written, rel_to_mount(target))
    _listing_changed(rel_dir)
    return target, name, written


def upload_file(rel_dir, uploaded_file, owner, preferred_name=None):
    """Write an upload into folder `rel_dir` on the share, and return its Upload.

    ADMA records nothing for it, except for a GIS file (is_gis): that gets its
    File record, owned by `owner`, and its processing starts, as for a GIS file
    uploaded to ADMA's own storage.
    """
    rel_dir = _normalize(rel_dir)
    abs_path, name, size = write_upload(rel_dir, uploaded_file, preferred_name or uploaded_file.name)
    rel = adapt_share.join(rel_dir, name)
    if not is_gis(name):
        return Upload(rel, name, size, None)
    try:
        record = _record_upload(rel, owner)
    except BaseException:
        # The user is told the upload failed, so its bytes do not stay behind.
        _discard(abs_path)
        _listing_changed(rel_dir)
        raise
    from .tasks import process_gis_file_task
    process_gis_file_task.delay(str(record.id))
    return Upload(rel, name, size, record)


def _record_upload(rel, owner):
    """The File record of GIS file `rel`, just written to the share: the one already kept for that path, or a new one owned by `owner`.

    Shaped like every ADAPT record: no folder, not public (grants decide who
    sees it), the path in third_party_id, compared without case as the share
    compares names, and the file on the mount in third_party_url. Stands in for
    adapt_records.record_for until that lands.
    """
    from .models import File

    abs_path = adapt_share.resolve(rel)
    url = reference_url(abs_path)
    for field, value in (('third_party_id', rel), ('third_party_url', url)):
        limit = File._meta.get_field(field).max_length
        if len(value) > limit:
            raise AdaptWriteError(f'Path on the share is too long to record (max {limit} characters)')

    record = File.objects.filter(
        third_party_source='adapt', third_party_id__iexact=rel,
    ).order_by('created_at').first()
    if record is None:
        name = adapt_share.name(rel)
        mime, _ = mimetypes.guess_type(name)
        record = File(
            name=name,
            folder=None,
            owner=owner,
            is_public=False,
            mime_type=mime or '',
            is_third_party=True,
            third_party_source='adapt',
            third_party_id=rel,
            third_party_url=url,
        )
        record.apply_extension_metadata(name)
    record.file_size = os.path.getsize(abs_path)
    record.save()
    return record


def make_folder(rel_parent, name):
    """Make folder `name` inside folder `rel_parent` on the share, and return its path.

    A name already taken on the share gets a suffix (_1, _2), as uploads do.
    """
    rel_parent = _normalize(rel_parent)
    dir_path = assert_writable(rel_parent)
    validate_name(name, is_dir=True)

    for candidate in _candidate_names(name, is_dir=True):
        try:
            os.mkdir(os.path.join(dir_path, candidate), 0o755)
        except FileExistsError:
            continue
        except OSError as exc:
            raise AdaptWriteError(
                f'Cannot create folder "{candidate}" on the ADAPT share: {exc}'
            ) from exc
        _listing_changed(rel_parent)
        return adapt_share.join(rel_parent, candidate)
    raise AdaptWriteError(f'Could not find a free name for folder "{name}" on the share')


def merge_folder(rel_parent, name):
    """Folder `name` inside folder `rel_parent` on the share, made if it is not there: (path, created).

    For uploads that merge into an existing tree rather than creating a fresh
    copy of it. A name held by a file or by a symlink is never written
    through: the new folder then gets a suffix, as make_folder gives it.
    """
    rel_parent = _normalize(rel_parent)
    dir_path = assert_writable(rel_parent)
    validate_name(name, is_dir=True)

    target = os.path.join(dir_path, name)
    try:
        os.mkdir(target, 0o755)
    except FileExistsError:
        if os.path.isdir(target) and not os.path.islink(target):
            return adapt_share.join(rel_parent, name), False
        return make_folder(rel_parent, name), True
    except OSError as exc:
        raise AdaptWriteError(f'Cannot create folder "{name}" on the ADAPT share: {exc}') from exc
    _listing_changed(rel_parent)
    return adapt_share.join(rel_parent, name), True


def upload_tree(rel_dir, entries, owner, fresh_top=False):
    """Write a folder upload into folder `rel_dir` on the share, and return its Tree.

    `entries` are (uploaded_file, path) pairs, each path relative to the
    upload, such as "Plots/North/b.csv". Every name is checked before anything
    is written, so one the share cannot store refuses the whole upload. An
    entry ADMA would not show (".DS_Store", anything under ".git") is left out
    and reported as skipped. Folders along the paths are merged into where
    they exist; with `fresh_top`, a top folder whose name is taken is made
    fresh with a suffix instead ("Plots_1"), as the web folder upload does for
    ADMA's own folders.
    """
    rel_dir = _normalize(rel_dir)
    assert_writable(rel_dir)

    todo, skipped = [], []
    for uploaded_file, path in entries:
        parts = path.split('/')
        for part in parts:
            _check_storable(part)
        last = len(parts) - 1
        if any(adapt_share.is_hidden(part, is_dir=i < last) for i, part in enumerate(parts)):
            skipped.append(path)
        else:
            todo.append((uploaded_file, parts))

    made = {}       # folder path in the upload -> its path on the share
    folders, renamed, files = [], [], []
    for uploaded_file, parts in todo:
        here = rel_dir
        for depth, part in enumerate(parts[:-1]):
            key = '/'.join(parts[:depth + 1])
            if key not in made:
                if fresh_top and depth == 0:
                    made[key], created = make_folder(here, part), True
                    if adapt_share.name(made[key]) != part:
                        renamed.append((part, adapt_share.name(made[key])))
                else:
                    made[key], created = merge_folder(here, part)
                if created:
                    folders.append(made[key])
            here = made[key]
        files.append(upload_file(here, uploaded_file, owner, preferred_name=parts[-1]))
    return Tree(files, folders, renamed, skipped)
