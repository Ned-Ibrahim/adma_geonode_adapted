"""
Management command to sync the ADAPT data warehouse into ADMA by REFERENCE.

Walks the mounted ADAPT share (the whole share by default), recreates the
folder tree under the ADAPT third-party root, and creates File records that
REFERENCE the files on the mount (stored in third_party_url) instead of
copying their content. ADMA streams the bytes live from the mount when a user
views or downloads them, so nothing is duplicated and the view always reflects
the warehouse.

Read-only toward the source. Idempotent: rows are matched by their path
relative to the mount (third_party_id), so re-running only adds what is new,
refreshes sizes that changed, and, with --prune, removes rows whose file is
gone from the share or now falls under an exclusion.

Some directories on the share are not warehouse data and are never indexed:
ADMA's own production storage (adma_geo_production, pg_data), Windows system
folders, and hidden files. See DEFAULT_EXCLUDED_NAMES / DEFAULT_EXCLUDED_FILES.

Usage:
    python manage.py sync_adapt                         # dry run, whole share
    python manage.py sync_adapt --once                  # import, whole share
    python manage.py sync_adapt --once --prune          # import + remove stale/excluded rows
    python manage.py sync_adapt --once --subdir "Data Management"
    python manage.py sync_adapt --once --exclude "Archive/*" --exclude "*.bak"
"""
import fnmatch
import mimetypes
import os
from pathlib import Path, PurePosixPath

from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db import transaction

from filemanager.models import File, Folder


# Directory or file names skipped wherever they appear in the tree.
DEFAULT_EXCLUDED_NAMES = frozenset({
    'adma_geo_production',      # ADMA's own storage on the share (pg_data, media)
    'pg_data',                  # Postgres internals, never user data
    '$RECYCLE.BIN',
    'System Volume Information',
    'lost+found',
    '.snapshot',
})

# File names skipped wherever they appear.
DEFAULT_EXCLUDED_FILES = frozenset({
    'Thumbs.db',
    'desktop.ini',
    '.DS_Store',
})

NAME_MAX = File._meta.get_field('name').max_length
ID_MAX = File._meta.get_field('third_party_id').max_length
URL_MAX = File._meta.get_field('third_party_url').max_length
BATCH = 1000


def guess_file_type(filename):
    ext = Path(filename).suffix.lower()
    if ext == '.csv':
        return 'csv'
    if ext in ('.xlsx', '.xls'):
        return 'spreadsheet'
    if ext in ('.shp', '.geojson', '.kml', '.tif', '.tiff', '.gpkg', '.json'):
        return 'gis'
    if ext in ('.png', '.jpg', '.jpeg', '.gif', '.bmp', '.webp'):
        return 'image'
    if ext in ('.txt', '.md', '.log'):
        return 'text'
    if ext in ('.pdf', '.doc', '.docx'):
        return 'document'
    return 'other'


class Command(BaseCommand):
    help = 'Sync the ADAPT warehouse into ADMA by reference (Third Parties)'

    def add_arguments(self, parser):
        parser.add_argument('--once', action='store_true',
                            help='Actually write to the database. Without it: dry run.')
        parser.add_argument('--mount', type=str, default='/adapt',
                            help='Where the ADAPT share is mounted inside the container.')
        parser.add_argument('--subdir', type=str, default='',
                            help='Limit the walk to this directory under the mount. '
                                 'Default: the whole share.')
        parser.add_argument('--exclude', action='append', default=[], metavar='GLOB',
                            help='Skip paths matching this glob (relative to the mount, '
                                 'forward slashes). Repeatable.')
        parser.add_argument('--no-default-excludes', action='store_true',
                            help='Do not apply the built-in exclusions (ADMA storage, '
                                 'Windows system folders, hidden files).')
        parser.add_argument('--prune', action='store_true',
                            help='Remove ADAPT rows whose file is gone from the share or '
                                 'is now excluded. Only within the walked subtree.')
        parser.add_argument('--no-recompute', action='store_true',
                            help='Skip recomputing cached folder totals afterwards.')

    # ------------------------------------------------------------------ setup

    def handle(self, *args, **options):
        self.mount = os.path.abspath(options['mount'])
        self.subdir = options['subdir'].strip('/\\')
        self.write = options['once']
        self.prune = options['prune']
        self.verbosity = options['verbosity']
        self.user_globs = [g.replace('\\', '/') for g in options['exclude']]
        self.use_defaults = not options['no_default_excludes']

        source_root = os.path.join(self.mount, self.subdir) if self.subdir else self.mount

        if not os.path.isdir(source_root):
            self.stderr.write(self.style.ERROR(f'Source directory not found/readable: {source_root}'))
            self.stderr.write(f'Check: docker compose exec django ls "{source_root}"')
            return
        try:
            top_entries = os.listdir(source_root)
        except OSError as e:
            self.stderr.write(self.style.ERROR(f'Cannot list {source_root}: {e}'))
            return
        if not top_entries:
            # An unmounted /adapt is an empty directory. Never treat that as "the share is empty".
            self.stderr.write(self.style.ERROR(
                f'{source_root} is empty. Is the share mounted? Refusing to sync (and prune) against nothing.'))
            return

        adapt_root = Folder.objects.filter(
            name='ADAPT', parent=None, is_third_party=True, third_party_source='adapt'
        ).first()
        if not adapt_root:
            self.stderr.write(self.style.ERROR("ADAPT root not found. Run 'python manage.py setup_adapt' first."))
            return
        self.owner = adapt_root.owner
        self.existing_folders = {}
        stats = {
            'folders_seen': 0, 'folders_created': 0,
            'files_seen': 0, 'files_created': 0, 'files_updated': 0, 'files_unchanged': 0,
            'excluded_dirs': 0, 'excluded_files': 0, 'errors': 0,
            'pruned_files': 0, 'pruned_folders': 0,
        }
        self.stats = stats

        # Which ADMA folder does source_root correspond to? With --subdir the walk
        # starts below the ADAPT root, so the subdir chain must exist (or be created).
        start_folder = adapt_root
        if self.subdir:
            abs_part = self.mount
            for part in PurePosixPath(self.subdir.replace('\\', '/')).parts:
                abs_part = os.path.join(abs_part, part)
                start_folder = self._get_or_create_folder(start_folder, part, abs_part)
                if start_folder is None:
                    return

        mode = 'IMPORT' if self.write else 'DRY RUN'
        self.stdout.write(f'[{mode}] Referencing from: {source_root}')
        self.stdout.write(f'         Into: {start_folder.get_full_path()} (owner: {self.owner.username})')
        if self.use_defaults:
            self.stdout.write(f'         Built-in excludes: {", ".join(sorted(DEFAULT_EXCLUDED_NAMES))}')
        if self.user_globs:
            self.stdout.write(f'         Extra excludes: {", ".join(self.user_globs)}')
        self.stdout.write('')

        # Preload everything under the start folder once, instead of one query per file.
        subtree_folder_ids = self._collect_subtree_ids(start_folder)
        existing_files = {}
        for f in File.objects.filter(
            folder_id__in=subtree_folder_ids, owner=self.owner,
            is_third_party=True, third_party_source='adapt',
        ).only('id', 'name', 'folder_id', 'file_size', 'third_party_id', 'third_party_url'):
            existing_files[(f.folder_id, f.name)] = f
        for fo in Folder.objects.filter(
            id__in=subtree_folder_ids, owner=self.owner,
            is_third_party=True, third_party_source='adapt',
        ).only('id', 'name', 'parent_id', 'third_party_id'):
            self.existing_folders[(fo.parent_id, fo.name)] = fo

        seen_file_ids = set()
        seen_folder_ids = {start_folder.id}
        to_create = []
        to_update = []

        dir_map = {os.path.abspath(source_root): start_folder}

        def on_walk_error(err):
            stats['errors'] += 1
            self.stderr.write(self.style.ERROR(f'  ! cannot read {err.filename}: {err.strerror}'))

        # ------------------------------------------------------------ walk

        for current_dir, subdirs, filenames in os.walk(source_root, onerror=on_walk_error):
            abs_current = os.path.abspath(current_dir)
            parent_folder = dir_map.get(abs_current)
            if parent_folder is None:
                continue

            # Filter in place so os.walk never descends into excluded directories.
            kept = []
            for dname in sorted(subdirs):
                rel = self._rel(os.path.join(abs_current, dname))
                if self._excluded(dname, rel, is_dir=True):
                    stats['excluded_dirs'] += 1
                    self._log(2, f'  - skip dir: {rel}')
                    continue
                kept.append(dname)
            subdirs[:] = kept

            for dname in list(subdirs):
                abs_child = os.path.join(abs_current, dname)
                stats['folders_seen'] += 1
                if len(dname) > NAME_MAX:
                    stats['errors'] += 1
                    self.stderr.write(self.style.ERROR(f'  ! folder name too long, skipped: {self._rel(abs_child)}'))
                    subdirs.remove(dname)
                    continue
                folder = self._get_or_create_folder(parent_folder, dname, abs_child)
                if folder is None:
                    subdirs.remove(dname)
                    continue
                dir_map[abs_child] = folder
                seen_folder_ids.add(folder.id)

            for fname in sorted(filenames):
                abs_file = os.path.join(abs_current, fname)
                rel = self._rel(abs_file)
                if self._excluded(fname, rel, is_dir=False):
                    stats['excluded_files'] += 1
                    continue
                stats['files_seen'] += 1

                if len(fname) > NAME_MAX or len(abs_file) > URL_MAX:
                    stats['errors'] += 1
                    self.stderr.write(self.style.ERROR(f'  ! path too long, skipped: {rel}'))
                    continue
                try:
                    size = os.path.getsize(abs_file)
                except OSError as e:
                    stats['errors'] += 1
                    self.stderr.write(self.style.ERROR(f'  ! stat failed ({rel}): {e}'))
                    continue

                rel_id = rel[:ID_MAX]
                existing = existing_files.get((parent_folder.id, fname))
                if existing is not None:
                    seen_file_ids.add(existing.id)
                    changed = []
                    if existing.file_size != size:
                        existing.file_size = size
                        changed.append('size')
                    if existing.third_party_id != rel_id:
                        existing.third_party_id = rel_id
                        changed.append('id')
                    if existing.third_party_url != abs_file:
                        existing.third_party_url = abs_file
                        changed.append('path')
                    if changed:
                        to_update.append(existing)
                        stats['files_updated'] += 1
                        self._log(2, f'  ~ file: {rel} ({", ".join(changed)})')
                    else:
                        stats['files_unchanged'] += 1
                    continue

                mime, _ = mimetypes.guess_type(fname)
                new_file = File(
                    name=fname,
                    folder=parent_folder,
                    owner=self.owner,
                    file_size=size,
                    file_type=guess_file_type(fname),
                    mime_type=mime or '',
                    is_public=False,
                    is_third_party=True,
                    third_party_source='adapt',
                    third_party_id=rel_id,
                    third_party_url=abs_file,   # absolute path on the mount inside the container
                )
                to_create.append(new_file)
                seen_file_ids.add(new_file.id)   # UUID is assigned at construction, so prune can see it
                stats['files_created'] += 1
                self._log(2, f'  + file: {rel} ({size} bytes)')

                if len(to_create) >= BATCH:
                    self._flush_create(to_create)
                if len(to_update) >= BATCH:
                    self._flush_update(to_update)

        self._flush_create(to_create)
        self._flush_update(to_update)

        # ----------------------------------------------------------- prune

        if self.prune:
            if stats['files_seen'] == 0 and stats['folders_seen'] == 0:
                self.stderr.write(self.style.ERROR(
                    'Walk found nothing. Refusing to prune: this looks like a dropped mount, not an empty share.'))
            else:
                self._prune(subtree_folder_ids, seen_file_ids, seen_folder_ids, start_folder)

        # ---------------------------------------------------------- report

        self.stdout.write('')
        if not self.write:
            self.stdout.write(self.style.WARNING('Dry run: nothing written. Pass --once to import.'))
        elif stats['errors'] == 0:
            self.stdout.write(self.style.SUCCESS('Sync (by reference) completed.'))
        else:
            self.stdout.write(self.style.WARNING('Sync completed with some errors.'))
        rows = [
            ('Folders seen', stats['folders_seen']),
            ('Folders created', stats['folders_created']),
            ('Files seen', stats['files_seen']),
            ('Files created', stats['files_created']),
            ('Files updated', stats['files_updated']),
            ('Files unchanged', stats['files_unchanged']),
            ('Excluded dirs', stats['excluded_dirs']),
            ('Excluded files', stats['excluded_files']),
        ]
        if self.prune:
            rows += [('Pruned files', stats['pruned_files']), ('Pruned folders', stats['pruned_folders'])]
        rows.append(('Errors', stats['errors']))
        for label, value in rows:
            self.stdout.write(f'  {label + ":":<18}{value}')

        if self.write and not options['no_recompute']:
            self.stdout.write('')
            self.stdout.write('Recomputing cached folder totals for the ADAPT tree...')
            call_command('recompute_folder_sizes', source='adapt', verbosity=0)
            self.stdout.write(self.style.SUCCESS('Cached totals updated.'))

    # --------------------------------------------------------------- helpers

    def _log(self, level, msg):
        if self.verbosity >= level:
            self.stdout.write(msg)

    def _rel(self, abs_path):
        """Path relative to the mount, forward slashes. Stable identity for a row."""
        return os.path.relpath(abs_path, self.mount).replace(os.sep, '/')

    def _excluded(self, name, rel, is_dir):
        if self.use_defaults:
            if name.startswith('.') or name.startswith('~$'):
                return True
            if name in DEFAULT_EXCLUDED_NAMES:
                return True
            if not is_dir and name in DEFAULT_EXCLUDED_FILES:
                return True
        for pattern in self.user_globs:
            if fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(name, pattern):
                return True
        return False

    def _get_or_create_folder(self, parent, name, abs_path):
        rel = self._rel(abs_path)[:ID_MAX]
        folder = self.existing_folders.get((parent.id, name))
        if folder is None and parent.pk and not parent._state.adding:
            folder = Folder.objects.filter(name=name, parent=parent, owner=self.owner).first()
        if folder is not None:
            if folder.third_party_id != rel and self.write:
                folder.third_party_id = rel
                folder.save(update_fields=['third_party_id'])
            return folder

        self.stats['folders_created'] += 1
        self._log(1, f'  + folder: {rel}')
        if not self.write:
            # Dry run: an unsaved placeholder so children can still be walked and counted.
            placeholder = Folder(name=name, parent=parent, owner=self.owner, is_third_party=True,
                                 third_party_source='adapt', third_party_id=rel)
            return placeholder
        try:
            folder = Folder.objects.create(
                name=name, parent=parent, owner=self.owner,
                is_public=False, is_third_party=True,
                third_party_source='adapt', third_party_id=rel,
            )
        except Exception as e:
            self.stats['errors'] += 1
            self.stderr.write(self.style.ERROR(f'  ! folder error ({rel}): {e}'))
            return None
        self.existing_folders[(parent.id, name)] = folder
        return folder

    def _collect_subtree_ids(self, root):
        """All folder ids under (and including) root, breadth-first, few queries."""
        ids = [root.id]
        frontier = [root.id]
        while frontier:
            frontier = list(Folder.objects.filter(parent_id__in=frontier).values_list('id', flat=True))
            ids.extend(frontier)
        return ids

    def _flush_create(self, rows):
        if not rows:
            return
        if self.write:
            # bulk_create skips File.save(), which is fine: save() only acts when .file is set.
            File.objects.bulk_create(rows, batch_size=BATCH)
        rows.clear()

    def _flush_update(self, rows):
        if not rows:
            return
        if self.write:
            File.objects.bulk_update(rows, ['file_size', 'third_party_id', 'third_party_url'], batch_size=BATCH)
        rows.clear()

    def _prune(self, subtree_folder_ids, seen_file_ids, seen_folder_ids, start_folder):
        stale_files = File.objects.filter(
            folder_id__in=subtree_folder_ids, owner=self.owner,
            is_third_party=True, third_party_source='adapt',
        ).exclude(id__in=seen_file_ids)
        stale_folders = Folder.objects.filter(
            id__in=subtree_folder_ids, owner=self.owner,
            is_third_party=True, third_party_source='adapt',
        ).exclude(id__in=seen_folder_ids).exclude(id=start_folder.id)

        n_files = stale_files.count()
        n_folders = stale_folders.count()
        self.stats['pruned_files'] = n_files
        self.stats['pruned_folders'] = n_folders
        if self.verbosity >= 2:
            for f in stale_files.only('third_party_id')[:200]:
                self.stdout.write(f'  x file: {f.third_party_id}')
            for fo in stale_folders.only('third_party_id')[:200]:
                self.stdout.write(f'  x folder: {fo.third_party_id}')
        if not self.write or (n_files == 0 and n_folders == 0):
            return

        with transaction.atomic():
            # Rows that were published to GeoServer must go through File.delete() so the
            # layer is removed too. Everything else is a plain DB row: bulk delete.
            for f in stale_files.filter(is_spatial=True).exclude(geoserver_layer_name__isnull=True).exclude(geoserver_layer_name=''):
                f.delete()
            stale_files.delete()
            # Deepest first so a parent CASCADE never races a child we still hold an id for.
            stale_folders.delete()
        self.stdout.write(f'  Pruned {n_files} files and {n_folders} folders.')
