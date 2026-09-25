"""The snr18 permission export, and what Windows makes of it.

snr18 exports the ACL of every ADAPT folder that has entries of its own or
blocks inheritance, one row per entry:

    "Path","Protected","Identity","Rights","Type","AppliesTo"
    "Flux Measurements","True","NEAD\\12345678","FullControl","Allow","ContainerInherit, ObjectInherit/None"

Path is relative to the ADAPT root, "(root)" is the root itself and "\\"
separates folders. Protected=True means the folder does not inherit from its
parent. AppliesTo is the inheritance flags and the propagation flags, joined
by "/". An optional IsInherited column marks entries a folder only inherited;
they are dropped, since the folder they come from carries them explicitly.

Everything here is plain Python over the export. adapt_acl check uses it to
work out what snr18 allows independently of filemanager.permissions, so it
must never read FolderGrant or the permissions module.
"""
import csv
from collections.abc import Iterable
from dataclasses import dataclass

COLUMNS = ('Path', 'Protected', 'Identity', 'Rights', 'Type', 'AppliesTo')
ROOT = '(root)'

READ_RIGHTS = frozenset({'Read', 'ReadAndExecute', 'Modify', 'FullControl'})
WRITE_RIGHTS = frozenset({'Modify', 'FullControl'})

# Built-in groups that on a domain server hold every signed-in domain account.
EVERY_DOMAIN_USER = frozenset({'builtin\\users', 'everyone', 'nt authority\\authenticated users'})


class ExportError(ValueError):
    pass


@dataclass(frozen=True)
class Entry:
    line: int
    path: tuple[str, ...]
    protected: bool
    identity: str
    rights_text: str
    allow: bool
    applies_to: str

    @property
    def rights(self):
        return frozenset(r.strip() for r in self.rights_text.split(','))

    @property
    def reads(self):
        return bool(self.rights & READ_RIGHTS)

    @property
    def writes(self):
        return bool(self.rights & WRITE_RIGHTS)

    @property
    def _flags(self):
        inherit, _, propagate = self.applies_to.partition('/')
        return ({f.strip() for f in inherit.split(',')}, {f.strip() for f in propagate.split(',')})

    @property
    def container_inherit(self):
        """Whether subfolders inherit the entry."""
        return 'ContainerInherit' in self._flags[0]

    @property
    def object_inherit(self):
        return 'ObjectInherit' in self._flags[0]

    @property
    def no_propagate(self):
        """Inherited by direct children only, not further down."""
        return 'NoPropagateInherit' in self._flags[1]

    @property
    def inherit_only(self):
        """Applies to what is beneath the folder, not to the folder itself."""
        return 'InheritOnly' in self._flags[1]

    @property
    def whole_subtree(self):
        """Whether the entry reaches the folder, its files and every folder beneath, as an ADMA grant does."""
        return self.container_inherit and self.object_inherit and not self.no_propagate and not self.inherit_only

    @property
    def display_path(self):
        return path_text(self.path)

    def key(self):
        return self.identity.lower()


def split_path(text) -> tuple[str, ...]:
    text = text.strip()
    if text == ROOT:
        return ()
    return tuple(part for part in text.split('\\') if part)


def path_text(path) -> str:
    return '\\'.join(path) if path else ROOT


def _true(value) -> bool:
    return (value or '').strip().lower() == 'true'


def read_export(path) -> list[Entry]:
    """The explicit entries of the export at `path`. Raises ExportError if it cannot be read."""
    try:
        with open(path, newline='', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            fields = [name.strip() for name in reader.fieldnames or []]
            missing = [c for c in COLUMNS if c not in fields]
            if missing:
                raise ExportError(f'{path} is missing the column{"s" if len(missing) > 1 else ""} '
                                  f'{", ".join(missing)}. Expected {", ".join(COLUMNS)}.')
            reader.fieldnames = fields
            entries = []
            for rec in reader:
                if _true(rec.get('IsInherited')):
                    continue
                entries.append(Entry(
                    line=reader.line_num,
                    path=split_path(rec['Path'] or ''),
                    protected=_true(rec['Protected']),
                    identity=(rec['Identity'] or '').strip(),
                    rights_text=(rec['Rights'] or '').strip(),
                    allow=(rec['Type'] or '').strip().lower() == 'allow',
                    applies_to=(rec['AppliesTo'] or '').strip(),
                ))
    except (OSError, UnicodeDecodeError, csv.Error) as exc:
        raise ExportError(f'Cannot read {path}: {exc}')
    return entries


class Acl:
    """Windows inheritance over the exported entries.

    A folder's effective entries are its own plus those it inherits: walking up
    from the folder, each parent passes down the entries that reach subfolders
    (ContainerInherit), and NoPropagateInherit entries only to the parent's direct
    children. The walk stops at a folder that blocks inheritance (Protected).
    Folders the export does not list have no entries of their own and inherit.
    Only Allow entries count: adapt_acl refuses an export with Deny entries.
    """

    def __init__(self, entries: Iterable[Entry]):
        self.explicit = {}
        self.protected = set()
        for e in entries:
            self.explicit.setdefault(e.path, []).append(e)
            if e.protected:
                self.protected.add(e.path)

    def effective(self, path) -> list[Entry]:
        path = tuple(path)
        found = [e for e in self.explicit.get(path, ()) if not e.inherit_only]
        child, distance = path, 1
        while child and child not in self.protected:
            parent = child[:-1]
            found += [e for e in self.explicit.get(parent, ())
                      if e.container_inherit and not (e.no_propagate and distance > 1)]
            child, distance = parent, distance + 1
        return found

    def matching(self, path, identities) -> list[Entry]:
        """Effective entries on `path` for anyone in `identities` (compared without case)."""
        wanted = {i.lower() for i in identities}
        return [e for e in self.effective(path) if e.allow and e.key() in wanted]

    def access(self, path, identities) -> tuple[bool, bool]:
        """(read, write) on the folder itself for someone holding `identities`."""
        entries = self.matching(path, identities)
        write = any(e.writes for e in entries)
        return write or any(e.reads for e in entries), write
