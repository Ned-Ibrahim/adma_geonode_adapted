"""Write-back to the mounted ADAPT share.

ADAPT is an SMB share mounted at ``settings.ADAPT_MOUNT``. ADMA indexes it by
reference: a :class:`~filemanager.models.File` row carries the path on the mount
in ``third_party_url`` and streams the bytes live, instead of copying them into
``MEDIA_ROOT``. ``sync_adapt`` creates those rows by walking the share.

This module is the only place in ADMA that writes to the share, and it holds
three rules so that the rest of the code does not have to:

1.  Every path is resolved and confirmed to sit under the mount before it is
    touched. A folder's ``third_party_id`` is editable in Django admin, so it is
    untrusted input rather than a trusted path.
2.  A write never overwrites. Names are reserved with ``O_EXCL``, which the
    server applies atomically, so two simultaneous uploads and a file dropped on
    the share out of band all lose the race instead of clobbering each other.
3.  Rows are written in exactly the shape ``sync_adapt`` produces, so the next
    sync recognises an upload as an existing file rather than re-importing it or
    pruning it away.

Who may write where is not decided here: callers check
``permissions.can_write`` first (see ``views.upload_target``).
"""
import logging
import mimetypes
import os
import uuid
from pathlib import Path

from django.conf import settings

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


# ------------------------------------------------------------------- path rules

def _contained(path, root):
    try:
        return os.path.commonpath([os.path.realpath(path), root]) == root
    except ValueError:
        # Different drives, or a mix of absolute and relative paths.
        return False


def folder_share_path(folder):
    """Absolute path on the mount for an ADAPT folder.

    Raises AdaptWriteError if the folder is not an ADAPT folder, or if its
    recorded path resolves outside the mount.
    """
    if not is_adapt_folder(folder):
        raise AdaptWriteError(f'"{folder}" is not a folder on the ADAPT share')

    root = mount_root()
    rel = (folder.third_party_id or '').strip()
    if rel == ROOT_SENTINEL or (not rel and folder.parent_id is None):
        return root
    if not rel:
        # A nested folder with no recorded path would otherwise resolve to the
        # share root, quietly landing uploads at the top of the warehouse.
        raise AdaptWriteError(
            f'Folder "{folder}" has no recorded path on the ADAPT share. '
            f'Run sync_adapt to repair it.'
        )

    rel = rel.replace('\\', '/')
    if rel.startswith('/') or os.path.isabs(rel) or (len(rel) > 1 and rel[1] == ':'):
        # Recorded paths are relative to the mount. An absolute one was not
        # written by sync_adapt and is not trusted.
        raise AdaptWriteError(f'Refusing to touch "{rel}": not a path relative to the ADAPT mount')
    rel = rel.strip('/')
    candidate = os.path.realpath(os.path.join(root, rel))
    if not _contained(candidate, root):
        raise AdaptWriteError(
            f'Refusing to touch "{rel}": it resolves outside the ADAPT mount'
        )
    return candidate


def _assert_mounted(root):
    """Refuse to write into an unmounted /adapt.

    When the CIFS mount is missing, /adapt is an ordinary empty directory inside
    the container. Writing there succeeds and then vanishes with the container,
    which looks exactly like silent data loss. An empty share is indistinguishable
    from a dropped mount, so both are refused. sync_adapt refuses on the same test.
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


def assert_writable(folder):
    """Confirm ADMA may write into this ADAPT folder, and return its path."""
    if not write_enabled():
        raise AdaptWriteDisabled(WRITE_DISABLED_MESSAGE)
    root = mount_root()
    _assert_mounted(root)
    path = folder_share_path(folder)
    _assert_prefix_allowed(path, root)
    if not os.path.isdir(path):
        raise AdaptWriteError(f'"{rel_to_mount(path)}" is not a directory on the share')
    return path


def validate_name(name):
    """Reject names the share cannot store faithfully.

    Stricter than POSIX needs, because the warehouse is a Windows share. Windows
    silently strips trailing dots and spaces, which would leave the name in the
    database and the name on the share disagreeing with each other permanently.
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

def write_upload(folder, uploaded_file, preferred_name, taken=()):
    """Stream an uploaded file onto the share inside ``folder``.

    Returns (absolute_path, name_actually_used, size_in_bytes).

    The name is reserved with O_EXCL before a single byte is written, so the
    file on the share is never overwritten and the name cannot be taken by a
    concurrent upload between the check and the write. Names in ``taken`` are
    skipped as well: they belong to rows whose file has gone from the share but
    which the next prune has not removed yet.
    """
    dir_path = assert_writable(folder)
    validate_name(preferred_name)

    fd = None
    target = None
    for candidate in _candidate_names(preferred_name):
        if candidate in taken:
            continue
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
    return target, name, written


def create_reference_upload(folder, uploaded_file, owner, preferred_name=None):
    """Write an upload onto the share and record it as an ADAPT reference.

    The resulting row is shaped exactly like one sync_adapt would create, except
    that it is owned by the uploading user rather than by the sync account, so
    that the upload is attributable. Like every ADAPT row it is not public:
    FolderGrants decide who sees it.
    """
    from .models import File

    name_max = File._meta.get_field('name').max_length
    id_max = File._meta.get_field('third_party_id').max_length
    url_max = File._meta.get_field('third_party_url').max_length

    preferred = preferred_name or uploaded_file.name
    if len(preferred) > name_max:
        raise AdaptWriteError(f'File name is too long (max {name_max} characters)')

    taken = set(File.objects.filter(folder=folder).values_list('name', flat=True))
    abs_path, name, size = write_upload(folder, uploaded_file, preferred, taken=taken)
    url = reference_url(abs_path)
    if len(url) > url_max:
        _discard(abs_path)
        raise AdaptWriteError(
            f'Path on the share is too long to record (max {url_max} characters)'
        )

    mime, _ = mimetypes.guess_type(name)
    file_obj = File(
        name=name,
        folder=folder,
        owner=owner,
        is_public=False,
        file_size=size,
        mime_type=mime or '',
        is_third_party=True,
        third_party_source='adapt',
        third_party_id=rel_to_mount(abs_path)[:id_max],
        third_party_url=url,
    )
    file_obj.apply_extension_metadata(name)
    try:
        file_obj.save()
    except BaseException:
        # The bytes are on the share but nothing references them. Roll back so a
        # later sync does not resurrect a file the user was told failed to upload.
        _discard(abs_path)
        raise
    _add_to_cached_totals(folder, size)
    return file_obj


def _add_to_cached_totals(folder, size):
    """Count a new file in the cached totals of its folder and every folder above it.

    ADAPT folders show totals that sync_adapt precomputes (Folder.uses_cached_totals),
    so without this an upload would not show in them until the next sync.
    """
    from django.db.models import F

    from .models import Folder

    ids = []
    current = folder
    while current is not None and current.pk not in ids:
        ids.append(current.pk)
        current = current.parent
    Folder.objects.filter(pk__in=ids).update(
        cached_total_size=F('cached_total_size') + size,
        cached_total_file_count=F('cached_total_file_count') + 1,
    )


def create_reference_folder(parent, name, owner):
    """Create a directory on the share and record it as an ADAPT folder.

    A name already taken on the share gets a suffix (_1, _2), as uploads do.
    """
    from .models import Folder

    id_max = Folder._meta.get_field('third_party_id').max_length
    name_max = Folder._meta.get_field('name').max_length
    if len(name) > name_max:
        raise AdaptWriteError(f'Folder name is too long (max {name_max} characters)')

    dir_path = assert_writable(parent)
    validate_name(name)

    target = None
    created = None
    taken = set(Folder.objects.filter(parent=parent).values_list('name', flat=True))
    for candidate in _candidate_names(name, is_dir=True):
        if candidate in taken:
            continue
        target = os.path.join(dir_path, candidate)
        try:
            os.mkdir(target, 0o755)
        except FileExistsError:
            continue
        except OSError as exc:
            raise AdaptWriteError(
                f'Cannot create folder "{candidate}" on the ADAPT share: {exc}'
            ) from exc
        created = candidate
        break
    else:
        raise AdaptWriteError(
            f'Could not find a free name for folder "{name}" on the share'
        )

    try:
        return Folder.objects.create(
            name=created,
            parent=parent,
            owner=owner,
            is_public=False,
            is_third_party=True,
            third_party_source='adapt',
            third_party_id=rel_to_mount(target)[:id_max],
        )
    except BaseException:
        try:
            os.rmdir(target)
        except OSError as exc:
            logger.error('Could not remove orphaned ADAPT folder %s: %s', target, exc)
        raise


def child_folder(parent, name, owner):
    """The ADAPT folder `name` inside `parent` and whether it was created, making it on the share if needed.

    For uploads that merge into an existing tree rather than creating a fresh
    copy of it. An existing row is reused only while its directory is still on
    the share; a row left behind by a deleted directory is not written into.
    """
    from .models import Folder

    validate_name(name)
    existing = Folder.objects.filter(
        parent=parent, name=name, is_third_party=True, third_party_source='adapt',
    ).order_by('created_at').first()
    if existing is not None:
        assert_writable(existing)
        return existing, False
    return create_reference_folder(parent, name, owner), True
