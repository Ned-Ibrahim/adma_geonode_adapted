"""
Grant, revoke and list access to ADAPT folders.

ADMA reads the ADAPT share through one account that sees everything, so each
ADMA user sees only the folders granted here (or in the admin, under Folder
grants). A grant covers the folder, its files and every folder beneath it.

Paths are folder names below the ADAPT root, joined with "/". An empty path
means the whole share.

    python manage.py adapt_grants list
    python manage.py adapt_grants grant yu.pan "Soils"
    python manage.py adapt_grants grant group:flux-team "Flux Measurements" --write
    python manage.py adapt_grants revoke yu.pan "Soils"
"""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand, CommandError

from filemanager.models import Folder, FolderGrant

User = get_user_model()


class Command(BaseCommand):
    help = 'Grant, revoke and list access to ADAPT folders'

    def add_arguments(self, parser):
        sub = parser.add_subparsers(dest='action', required=True)
        sub.add_parser('list', help='List every grant')
        for action in ('grant', 'revoke'):
            p = sub.add_parser(action)
            p.add_argument('who', help='A username, or group:<name> for a Django group')
            p.add_argument('path', help='Folder path below the ADAPT root, e.g. "Soils/2024"')
            if action == 'grant':
                p.add_argument('--write', action='store_true', help='Also allow uploads and changes')

    def handle(self, *args, **options):
        action = options['action']
        if action == 'list':
            return self._list()
        who = self._principal(options['who'], create_group=(action == 'grant'))
        folder = self._folder(options['path'])
        target = {'group': who} if isinstance(who, Group) else {'user': who}
        label = f"{options['who']} on {folder.get_full_path()}"
        if action == 'grant':
            grant, created = FolderGrant.objects.update_or_create(
                folder=folder, **target, defaults={'can_write': options['write']},
            )
            mode = 'read/write' if grant.can_write else 'read'
            self.stdout.write(self.style.SUCCESS(f"{'Granted' if created else 'Updated'} {mode}: {label}"))
        else:
            deleted, _ = FolderGrant.objects.filter(folder=folder, **target).delete()
            if not deleted:
                raise CommandError(f'No grant for {label}')
            self.stdout.write(self.style.SUCCESS(f'Revoked: {label}'))

    def _list(self):
        grants = FolderGrant.objects.select_related('folder', 'user', 'group').order_by('user__username', 'group__name')
        if not grants:
            self.stdout.write('No grants. Only superusers can see ADAPT.')
        for g in grants:
            who = g.user.username if g.user_id else f'group:{g.group.name}'
            mode = 'read/write' if g.can_write else 'read'
            self.stdout.write(f'{who:<24} {mode:<10} {g.folder.get_full_path()}')

    def _principal(self, who, create_group):
        if who.startswith('group:'):
            name = who[len('group:'):]
            if create_group:
                group, created = Group.objects.get_or_create(name=name)
                if created:
                    self.stdout.write(f'Created group {name}. Add users to it in the admin, under Groups.')
                return group
            try:
                return Group.objects.get(name=name)
            except Group.DoesNotExist:
                raise CommandError(f'No group named {name}')
        try:
            return User.objects.get(username=who)
        except User.DoesNotExist:
            raise CommandError(f'No user named {who}')

    def _folder(self, path):
        root = Folder.objects.filter(
            name='ADAPT', parent=None, is_third_party=True, third_party_source='adapt',
        ).first()
        if root is None:
            raise CommandError('No ADAPT root folder. Run setup_adapt and sync_adapt first.')
        folder = root
        for name in [part for part in path.strip('/').split('/') if part]:
            child = folder.subfolders.filter(name=name, deletion_in_progress=False).first()
            if child is None:
                names = ', '.join(folder.subfolders.order_by('name').values_list('name', flat=True)[:20])
                raise CommandError(f'No folder "{name}" in {folder.get_full_path()}. Folders there: {names}')
            folder = child
        return folder
