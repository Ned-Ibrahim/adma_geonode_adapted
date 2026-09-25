"""
Mirror the snr18 permissions of the ADAPT share into ADMA, and check the result.

import turns the snr18 ACL export (see filemanager.ntfs_acl for its format)
into FolderGrant rows. It replaces every ADAPT grant, hand-made ones included,
so the export is the only source of ADAPT access afterwards:

- NEAD\\<NUID> becomes a grant for the account with that NUID (adapt_users load
  stores it), NEAD\\<group> a grant for the Django group of that name.
- ReadAndExecute (or Read) gives read, FullControl or Modify gives write.
  Deny entries, which a grant cannot express, stop the command.
- Anything else is listed and not imported: built-in and local identities
  (BUILTIN\\Users, BUILTIN\\Administrators, CREATOR OWNER, NT AUTHORITY\\SYSTEM),
  accounts of other domains (UNL-AD\\...), NUIDs without an account, AD groups
  without a Django group, and rights that are neither read nor write.
- Folders the catalogue does not hold are listed, and their entries skipped.
- An ADMA grant covers the folder and everything beneath it. Entries that
  reach less on snr18 (NoPropagateInherit, InheritOnly) are imported as normal
  grants and listed as warnings, and so are folders that block inheritance on
  snr18 while an ADMA grant above them still reaches them.

Without --apply it only prints what would change. With --apply every change is
written in one transaction. A rerun with the same export changes nothing.

check prints, for every roster user and every team folder (the folders
directly under ADAPT), what ADMA allows next to what snr18 allows, and fails
if they differ. The ADMA side comes from filemanager.permissions; read there
means the folder's files are visible (can_list_files), so a folder that is
only open to navigate towards a grant further down does not count. The snr18
side is worked out from the export and the roster alone: Windows inheritance
over the exported entries, with group membership taken from the roster.

BUILTIN\\Users holds every domain account on snr18, so check counts it as
every roster user. import cannot map it, so access that snr18 gives only
through BUILTIN\\Users is not a failure: where ADMA gives the same access
through other grants the cell is marked +, where ADMA does not the cell is
marked * as an accepted difference. Both are listed under the matrix. Other
identities ADMA cannot map (BUILTIN\\Administrators, snr-computersupport and
so on) have members the roster does not name; neither side counts them, and
they are listed per folder.

    python manage.py adapt_acl import acl.csv
    python manage.py adapt_acl import acl.csv --apply
    python manage.py adapt_acl check acl.csv --roster roster.csv
"""
import re
from collections import defaultdict

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from filemanager import permissions
from filemanager.management.commands.adapt_users import read_roster
from filemanager.models import Folder, FolderGrant, UserProfile
from filemanager.ntfs_acl import (
    EVERY_DOMAIN_USER,
    Acl,
    ExportError,
    path_text,
    read_export,
)

User = get_user_model()

NUID = re.compile(r'\d{8}')
DOMAIN = 'NEAD'


def mode(read, write):
    return 'write' if write else 'read' if read else 'nothing'


def plural(count, word, many=None):
    return f'{count} {word if count == 1 else many or word + "s"}'


class Principals:
    """Maps export identities to ADMA users and groups, or says why it cannot."""

    def __init__(self):
        self.users = {p.nuid: p.user for p in UserProfile.objects.filter(nuid__isnull=False).select_related('user')}
        self.groups = {g.name.lower(): g for g in Group.objects.all()}

    def resolve(self, identity):
        """(('user' or 'group', object), None) or (None, reason)."""
        domain, sep, name = identity.partition('\\')
        if domain.upper() == 'BUILTIN':
            return None, 'a local group on snr18; ADMA does not know its members'
        if not sep or domain.upper() == 'NT AUTHORITY':
            return None, 'a Windows built-in identity, not a person'
        if domain.upper() != DOMAIN:
            return None, f'a {domain} account; ADMA maps only {DOMAIN} accounts and groups'
        if NUID.fullmatch(name):
            user = self.users.get(name)
            return (('user', user), None) if user else (None, f'no ADMA account has NUID {name}')
        group = self.groups.get(name.lower())
        return (('group', group), None) if group else (None, f'no ADMA group named {name}')


def label(principal):
    kind, obj = principal
    return f'user {obj.username}' if kind == 'user' else f'group {obj.name}'


class Catalogue:
    """The ADAPT folder tree, loaded once, looked up by export path."""

    def __init__(self):
        self.root = Folder.objects.filter(
            name='ADAPT', parent=None, is_third_party=True, third_party_source='adapt',
        ).first()
        if self.root is None:
            raise CommandError('No ADAPT root folder. Run setup_adapt and sync_adapt first.')
        self.children = {}
        rows = Folder.objects.filter(third_party_source='adapt', deletion_in_progress=False)
        for pk, parent_id, name in rows.values_list('id', 'parent_id', 'name'):
            self.children[(parent_id, name)] = pk

    def find(self, path):
        """Folder id for an export path, or None."""
        pk = self.root.pk
        for name in path:
            pk = self.children.get((pk, name))
            if pk is None:
                return None
        return pk

    def display(self, path):
        return '/'.join(('ADAPT',) + tuple(path))


class Command(BaseCommand):
    help = 'Import ADAPT folder grants from the snr18 ACL export, or check them against it'

    def add_arguments(self, parser):
        sub = parser.add_subparsers(dest='action', required=True)
        imp = sub.add_parser('import', help='Replace every ADAPT grant with the grants in the export')
        imp.add_argument('export', help='snr18 ACL export (CSV)')
        imp.add_argument('--apply', action='store_true', help='Write the changes; without it nothing is saved')
        check = sub.add_parser('check', help='Compare ADMA access with snr18 for every roster user')
        check.add_argument('export', help='snr18 ACL export (CSV)')
        check.add_argument('--roster', required=True, help='Roster CSV, as for adapt_users load')

    def handle(self, *args, **options):
        entries = self._read(options['export'])
        if options['action'] == 'import':
            self._import(entries, options['apply'])
        else:
            self._check(entries, options['roster'])

    def _read(self, path):
        try:
            entries = read_export(path)
        except ExportError as exc:
            raise CommandError(str(exc))
        denies = [e for e in entries if not e.allow]
        if denies:
            where = '; '.join(f'line {e.line}: {e.identity} on {e.display_path}' for e in denies)
            raise CommandError(f'The export has Deny entries, which ADMA grants cannot express: {where}')
        return entries

    # import

    def _import(self, entries, apply):
        catalogue = Catalogue()
        principals = Principals()
        wanted = {}                      # (folder id, kind, principal pk) -> [can_write, principal, path]
        skipped = defaultdict(list)      # (identity, reason) -> entries on known folders
        skipped_elsewhere = defaultdict(int)
        unknown = set()
        lost = []                        # entries for ADMA users or groups on folders the catalogue lacks
        warnings = []

        for e in entries:
            folder_id = catalogue.find(e.path)
            principal, reason = principals.resolve(e.identity)
            if principal and not (e.reads or e.writes):
                principal, reason = None, f'rights {e.rights_text} give neither read nor write'
            if folder_id is None:
                unknown.add(e.path)
                if principal is None:
                    skipped_elsewhere[(e.identity, reason)] += 1
                else:
                    lost.append(f'  {label(principal):<32} {mode(e.reads, e.writes):<16} {e.display_path}')
                continue
            if principal is None:
                skipped[(e.identity, reason)].append(e)
                continue
            if not e.whole_subtree:
                warnings.append(self._scope_warning(e, principal))
            key = (folder_id, principal[0], principal[1].pk)
            current = wanted.setdefault(key, [False, principal, e.path])
            current[0] = current[0] or e.writes

        warnings += self._protected_warnings(entries, catalogue, wanted)

        existing = {}
        for grant in FolderGrant.objects.filter(folder__third_party_source='adapt').select_related('folder', 'user', 'group'):
            kind, pk = ('user', grant.user_id) if grant.user_id else ('group', grant.group_id)
            existing[(grant.folder_id, kind, pk)] = grant
        add = [key for key in wanted if key not in existing]
        change = [key for key in wanted if key in existing and existing[key].can_write != wanted[key][0]]
        remove = [key for key in existing if key not in wanted]
        unchanged = len(wanted) - len(add) - len(change)

        def line(sign, key, text):
            _, principal, path = wanted[key]
            return f'  {sign} {label(principal):<32} {text:<16} {catalogue.display(path)}'

        def removed(grant):
            who = f'user {grant.user.username}' if grant.user_id else f'group {grant.group.name}'
            path = grant.folder.get_full_path()
            return path, who, f'  - {who:<32} {mode(True, grant.can_write):<16} {path}'

        def by_path(key):
            return catalogue.display(wanted[key][2]), label(wanted[key][1])

        self._section('Grants to add', [line('+', k, mode(True, wanted[k][0])) for k in sorted(add, key=by_path)])
        self._section('Grants to change', [
            line('~', k, f'{mode(True, existing[k].can_write)} -> {mode(True, wanted[k][0])}')
            for k in sorted(change, key=by_path)])
        self._section('Grants to remove', [text for *_, text in sorted(removed(existing[k]) for k in remove)])
        self._section('Not imported:', self._skipped_lines(skipped, skipped_elsewhere), plain=True)
        self._section(f'Folders not in the catalogue ({plural(len(unknown), "folder")}; their entries are skipped):',
                      self._unknown_lines(unknown), plain=True)
        self._section('Grants these folders would carry, not imported:', sorted(lost), plain=True)
        self._section('Warnings:', [f'  {w}' for w in warnings], plain=True)

        if apply:
            with transaction.atomic():
                FolderGrant.objects.filter(pk__in=[existing[k].pk for k in remove]).delete()
                for key in change:
                    grant = existing[key]
                    grant.can_write = wanted[key][0]
                    grant.save(update_fields=['can_write'])
                FolderGrant.objects.bulk_create([
                    FolderGrant(folder_id=key[0], can_write=wanted[key][0], **{key[1]: wanted[key][1][1]})
                    for key in add
                ])

        self.stdout.write(f'Add {len(add)}, change {len(change)}, remove {len(remove)}, unchanged {unchanged}.')
        skipped_count = sum(len(v) for v in skipped.values()) + sum(skipped_elsewhere.values()) + len(lost)
        self.stdout.write(f'{plural(skipped_count, "entry", "entries")} not imported, '
                          f'{plural(len(unknown), "folder")} not in the catalogue, '
                          f'{plural(len(warnings), "warning")}.')
        if not (add or change or remove):
            self.stdout.write('Nothing to change.')
        elif apply:
            self.stdout.write(self.style.SUCCESS('Saved.'))
        else:
            self.stdout.write('Dry run: nothing was saved. Run again with --apply to write these changes.')

    def _section(self, title, lines, plain=False):
        if not lines:
            return
        self.stdout.write(title if plain else f'{title} ({len(lines)}):')
        for text in lines:
            self.stdout.write(text)
        self.stdout.write('')

    @staticmethod
    def _scope_warning(e, principal):
        if e.no_propagate:
            reach = 'NoPropagateInherit: on snr18 it reaches only the folder and its direct children'
        elif e.inherit_only:
            reach = 'InheritOnly: on snr18 it applies beneath the folder but not to the folder itself'
        elif not e.container_inherit:
            reach = 'no ContainerInherit: on snr18 it does not reach subfolders'
        else:
            reach = 'no ObjectInherit: on snr18 it does not reach files'
        return (f'{e.identity} ({label(principal)}) {e.rights_text} on {e.display_path} ({reach}). '
                'Imported as a normal grant, which covers the folder and everything beneath it.')

    @staticmethod
    def _protected_warnings(entries, catalogue, wanted):
        """Folders that block inheritance on snr18, but that ADMA grants on folders above still reach."""
        level = defaultdict(int)             # (folder id, kind, pk) -> 1 read, 2 write
        principal_of = {}
        for (folder_id, kind, pk), (can_write, principal, _) in wanted.items():
            level[(folder_id, kind, pk)] = 2 if can_write else 1
            principal_of[(kind, pk)] = principal
        groups_of = defaultdict(set)
        user_ids = [pk for kind, pk in principal_of if kind == 'user']
        for user_id, group_id in User.groups.through.objects.filter(user_id__in=user_ids).values_list(
                'user_id', 'group_id'):
            groups_of[user_id].add(group_id)
        warnings = []
        for path in sorted({e.path for e in entries if e.protected and e.path}):
            folder_id = catalogue.find(path)
            if folder_id is None:
                continue
            above = [catalogue.find(path[:i]) for i in range(len(path))]
            reach = {}
            for (fid, kind, pk), lvl in level.items():
                if fid in above:
                    reach[(kind, pk)] = max(reach.get((kind, pk), 0), lvl)
            def here(who, folder_id=folder_id):
                """What `who` has on the folder itself, directly or, for a user, through a group."""
                kind, pk = who
                found = level.get((folder_id, kind, pk), 0)
                if kind == 'user':
                    for group_id in groups_of[pk]:
                        found = max(found, level.get((folder_id, 'group', group_id), 0))
                return found

            leaks = sorted(
                f'{label(principal_of[who])} ({"write" if lvl == 2 else "read"})'
                for who, lvl in reach.items() if lvl > here(who)
            )
            if leaks:
                warnings.append(f'{path_text(path)} blocks inheritance on snr18, but ADMA grants on the folders '
                                f'above it still reach it for: {", ".join(leaks)}.')
        return warnings

    @staticmethod
    def _skipped_lines(skipped, skipped_elsewhere):
        lines = []
        for identity, reason in sorted(set(skipped) | set(skipped_elsewhere)):
            lines.append(f'  {identity}: {reason}')
            by_rights = defaultdict(list)
            for e in skipped.get((identity, reason), ()):
                by_rights[e.rights_text].append(e.path)
            for rights, paths in sorted(by_rights.items()):
                lines.append(f'      {rights} on {", ".join(Command._branches(paths))}')
            elsewhere = skipped_elsewhere.get((identity, reason))
            if elsewhere:
                lines.append(f'      and {plural(elsewhere, "entry", "entries")} on folders not in the catalogue')
        return lines

    @staticmethod
    def _branches(paths):
        """Folder paths, with the ones beneath another listed folder counted instead of listed."""
        tops = []
        for path in sorted(set(paths)):
            if tops and tops[-1][0] and path[:len(tops[-1][0])] == tops[-1][0]:
                tops[-1][1] += 1
            else:
                tops.append([path, 0])
        return [path_text(path) + (f' (and {plural(n, "folder")} beneath it)' if n else '') for path, n in tops]

    @staticmethod
    def _unknown_lines(unknown):
        return [f'  {branch}' for branch in Command._branches(unknown)]

    # check

    def _check(self, entries, roster_path):
        rows, failures, _ = read_roster(roster_path)
        if failures:
            problems = '; '.join(f'line {line}: {message}' for line, message, _, _ in failures)
            raise CommandError(f'The roster has rows that cannot be read: {problems}')
        acl = Acl(entries)
        catalogue = Catalogue()
        principals = Principals()
        folders = list(catalogue.root.subfolders.filter(deletion_in_progress=False).order_by('name'))
        accounts = {p.nuid: p.user for p in UserProfile.objects.filter(nuid__in=[r.nuid for r in rows])
                    .select_related('user')}

        cells, mismatches, accepted, notes = {}, [], [], []
        for row in rows:
            user = accounts.get(row.nuid)
            own = {f'{DOMAIN}\\{row.nuid}'} | {f'{DOMAIN}\\{g}' for g in row.groups}
            for folder in folders:
                path = (folder.name,)
                strict = acl.access(path, own)
                snr18 = acl.access(path, own | EVERY_DOMAIN_USER)
                adma = ((permissions.can_list_files(user, folder), permissions.can_write(user, folder))
                        if user else (False, False))
                name = user.username if user else row.username
                where = f'{name} on {folder.name}: ADMA {mode(*adma)}, snr18 {mode(*snr18)}'
                builtin = sorted({e.identity for e in acl.matching(path, EVERY_DOMAIN_USER)})
                if adma == snr18:
                    mark = ''
                    if strict != snr18:
                        mark = '+'
                        notes.append(f'  {where}. On snr18 only through {", ".join(builtin)}; '
                                     'ADMA gives it through other grants.')
                elif adma == strict:
                    mark = '*'
                    accepted.append(f'  {where} through {", ".join(builtin)}, which ADMA cannot import.')
                else:
                    mark = '!'
                    through = sorted({e.identity for e in acl.matching(path, own | EVERY_DOMAIN_USER)})
                    mismatches.append(f'  {where}. snr18 through: {", ".join(through) or "nothing"}.'
                                      + ('' if user else ' No ADMA account has this NUID.'))
                cells[(row.nuid, folder.pk)] = f'{self._cell(*adma)}/{self._cell(*snr18)}{mark}'

        self._matrix(rows, accounts, folders, cells)
        missing = sorted({e.path[0] for e in entries if e.path} - {f.name for f in folders})
        if missing:
            self.stdout.write(self.style.WARNING(
                f'Team folders in the export but not in the catalogue, so not checked: {", ".join(missing)}'))
            self.stdout.write('')
        self._section(f'Mismatches ({len(mismatches)}):', mismatches, plain=True)
        unmappable = self._unmappable(acl, folders, principals)
        if accepted or notes or unmappable:
            self.stdout.write('Accepted differences and notes (not failures):')
            self.stdout.write('')
            self._section('* ADMA differs from snr18 only by access through a built-in group:', accepted, plain=True)
            self._section('+ Same access, but on snr18 it comes only through a built-in group:', notes, plain=True)
            self._section('Identities with access that ADMA cannot map. Neither side counts them, '
                          'as the roster does not name their members:', unmappable, plain=True)
        self.stdout.write(f'{plural(len(rows), "user")}, {plural(len(folders), "folder")}: '
                          f'{plural(len(mismatches), "mismatch", "mismatches")}, '
                          f'{len(accepted)} accepted, {len(notes)} through a built-in group only.')
        if mismatches:
            raise CommandError(f'ADMA differs from snr18 in {plural(len(mismatches), "place")}; see Mismatches above.')

    @staticmethod
    def _cell(read, write):
        return 'W' if write else 'R' if read else '-'

    def _matrix(self, rows, accounts, folders, cells):
        self.stdout.write('Each cell is ADMA/snr18: W read and write, R read only, - nothing.')
        self.stdout.write('! mismatch, * accepted difference, + same access, on snr18 only through a built-in group.')
        for i, folder in enumerate(folders, 1):
            self.stdout.write(f'  {i:>2}  {folder.name}')
        self.stdout.write('')
        names = {row.nuid: accounts[row.nuid].username if row.nuid in accounts else row.username for row in rows}
        width = max([len('user')] + [len(n) for n in names.values()])
        self.stdout.write(f'{"user":<{width}}  ' + ''.join(f'{i:<6}' for i in range(1, len(folders) + 1)).rstrip())
        for row in sorted(rows, key=lambda r: names[r.nuid]):
            self.stdout.write(f'{names[row.nuid]:<{width}}  '
                              + ''.join(f'{cells[(row.nuid, f.pk)]:<6}' for f in folders).rstrip())
        self.stdout.write('')

    @staticmethod
    def _unmappable(acl, folders, principals):
        lines = []
        for folder in folders:
            found = defaultdict(set)
            for e in acl.effective((folder.name,)):
                if e.key() in EVERY_DOMAIN_USER:
                    continue
                principal, _ = principals.resolve(e.identity)
                if principal is None and (e.reads or e.writes):
                    found[e.identity].add(mode(e.reads, e.writes))
            if found:
                who = ', '.join(f'{identity} ({"write" if "write" in modes else "read"})'
                                for identity, modes in sorted(found.items()))
                lines.append(f'  {folder.name}: {who}')
        return lines
