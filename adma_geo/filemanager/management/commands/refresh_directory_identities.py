"""Re-read objectSid and tokenGroups for users who have logged in through LDAP.

    python manage.py refresh_directory_identities            # everyone with a DN
    python manage.py refresh_directory_identities --user jdoe2

Group membership changes in Active Directory do not reach ADMA until the user
logs in again or this runs. Celery beat runs it daily (settings.CELERY_BEAT_SCHEDULE).
Binds once as the service account and reuses the connection.
"""
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from filemanager import directory
from filemanager.models import DirectoryIdentity

User = get_user_model()


class Command(BaseCommand):
    help = 'Refresh stored Active Directory SIDs for LDAP users'

    def add_arguments(self, parser):
        parser.add_argument('--user', default='', help='Only this username')

    def handle(self, *args, **options):
        if not directory.ldap_enabled():
            raise CommandError('LDAP_URI is not set; there is no directory to refresh from.')

        identities = DirectoryIdentity.objects.exclude(dn='').select_related('user')
        if options['user']:
            identities = identities.filter(user__username=options['user'])
            if not identities.exists():
                raise CommandError(f"{options['user']} has no directory identity yet (never logged in via LDAP).")

        connection = directory.bind_service_connection()
        refreshed = errors = 0
        try:
            for identity in identities:
                try:
                    before = set(identity.group_sids or [])
                    updated = directory.refresh_identity(identity.user, connection=connection)
                    after = set(updated.group_sids or []) if updated else before
                    delta = ''
                    if after != before:
                        delta = f'  (+{len(after - before)} -{len(before - after)} groups)'
                    self.stdout.write(f'  · {identity.user.username}: {len(after)} groups{delta}')
                    refreshed += 1
                except Exception as exc:
                    errors += 1
                    self.stderr.write(self.style.ERROR(f'  ! {identity.user.username}: {exc}'))
        finally:
            connection.unbind_s()
        self.stdout.write(f'Refreshed {refreshed}, errors {errors}')
