"""Explain, ACE by ACE, why a user may or may not read or write an ADAPT path.

    python manage.py check_adapt_access --user jdoe2 --path "Flux Measurements"
    python manage.py check_adapt_access --user jdoe2 --path "Flux Measurements/2024" --write

Prints the user's token, which folder's descriptor applied, and what each
ACE did. This is the command to run in the room when someone asks "why can
Jane not see this?".
"""
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from filemanager import adapt_acl, permissions
from filemanager.models import Folder

User = get_user_model()

RIGHT_NAMES = [
    (adapt_acl.FILE_READ_DATA, 'READ_DATA/LIST'),
    (adapt_acl.FILE_WRITE_DATA, 'WRITE_DATA/ADD_FILE'),
    (adapt_acl.FILE_APPEND_DATA, 'APPEND/ADD_SUBDIR'),
    (adapt_acl.FILE_EXECUTE, 'EXECUTE/TRAVERSE'),
    (adapt_acl.FILE_DELETE_CHILD, 'DELETE_CHILD'),
    (adapt_acl.DELETE, 'DELETE'),
    (adapt_acl.WRITE_DAC, 'WRITE_DAC'),
    (adapt_acl.WRITE_OWNER, 'WRITE_OWNER'),
]


def describe_mask(mask):
    names = [name for bit, name in RIGHT_NAMES if mask & bit]
    return ', '.join(names) if names else f'0x{mask:x}'


class Command(BaseCommand):
    help = 'Explain the ADAPT access decision for one user and one path'

    def add_arguments(self, parser):
        parser.add_argument('--user', required=True, help='ADMA username')
        parser.add_argument('--path', required=True, help='Share-relative folder path, e.g. "Data Management"')
        parser.add_argument('--write', action='store_true', help='Check write instead of read')

    def handle(self, *args, **options):
        try:
            user = User.objects.get(username=options['user'])
        except User.DoesNotExist:
            raise CommandError(f"No ADMA user named {options['user']}")

        rel = options['path'].strip('/').replace('\\', '/')
        folder = Folder.objects.filter(third_party_source='adapt', third_party_id=rel).first()
        if folder is None and rel == '':
            folder = Folder.objects.filter(third_party_source='adapt', parent__isnull=True).first()
        if folder is None:
            raise CommandError(f'No ADAPT folder catalogued at "{rel}". Run sync_adapt first.')

        mask = adapt_acl.WRITE_MASK if options['write'] else adapt_acl.READ_MASK
        self.stdout.write(f'User   : {user.username}' + ('  (superuser: bypasses the ADAPT check)' if user.is_superuser else ''))
        token = permissions.token_sids(user)
        if not token:
            self.stdout.write(self.style.WARNING(
                '         no DirectoryIdentity: this user has never logged in through LDAP, '
                'so every ADAPT check fails closed'))
        else:
            self.stdout.write(f'Token  : {len(token)} SIDs + Everyone + Authenticated Users')
            for sid in sorted(token):
                self.stdout.write(f'         {sid}')

        source, descriptor = permissions.effective_descriptor(folder)
        self.stdout.write(f'Folder : {folder.get_full_path()}')
        if descriptor is None:
            self.stdout.write(self.style.ERROR(
                '         no security descriptor stored on this folder or any ancestor. Run sync_adapt_acls.'))
            self.stdout.write(self.style.ERROR('Result : DENIED (fail closed)'))
            return
        if source != folder:
            self.stdout.write(f'ACL from ancestor: {source.get_full_path()}')
        self.stdout.write(f'Owner  : {descriptor.owner}')
        self.stdout.write(f'Asking : {describe_mask(mask)}')
        self.stdout.write('')
        self.stdout.write(f'{"#":>3}  {"type":5}  {"in token":8}  {"effect":22}  SID and rights')
        for i, row in enumerate(adapt_acl.explain(descriptor, token or [], mask)):
            kind = 'allow' if row['allow'] else 'DENY'
            flag = 'inherit-only' if row['inherit_only'] else ('yes' if row['in_token'] else 'no')
            self.stdout.write(f'{i:>3}  {kind:5}  {flag:8}  {row["effect"]:22}  {row["sid"]}  [{describe_mask(row["mask"])}]')
        self.stdout.write('')
        allowed = permissions.can_write(user, folder) if options['write'] else permissions.can_read(user, folder)
        self.stdout.write(self.style.SUCCESS('Result : ALLOWED') if allowed else self.style.ERROR('Result : DENIED'))
