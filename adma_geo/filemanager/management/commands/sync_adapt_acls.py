"""Fetch the security descriptor of every ADAPT folder and store it on FolderAcl.

    python manage.py sync_adapt_acls                # every ADAPT folder
    python manage.py sync_adapt_acls --only "Data Management"   # one subtree
    python manage.py sync_adapt_acls --dry-run      # fetch, report, write nothing
    python manage.py sync_adapt_acls --stale 2      # only rows older than 2 days or missing

Runs as the service account over a metadata-only SMB session (READ_CONTROL on
each directory, nothing else). Paths come from Folder.third_party_id, which
sync_adapt fills with the share-relative path. Descriptors are stored raw; the
evaluation happens per request in filemanager.permissions.

A folder whose descriptor cannot be read keeps its previous descriptor (if any)
and records the error, so a transient failure never widens access: with no
descriptor at all, the permission check fails closed.
"""
import time
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from filemanager import adapt_acl
from filemanager.models import Folder, FolderAcl


class Command(BaseCommand):
    help = 'Fetch ADAPT folder security descriptors from the share into FolderAcl'

    def add_arguments(self, parser):
        parser.add_argument('--only', default='', metavar='PATH',
                            help='Share-relative path prefix to limit the run to (e.g. "Data Management")')
        parser.add_argument('--dry-run', action='store_true', help='Fetch and report, write nothing')
        parser.add_argument('--stale', type=float, default=None, metavar='DAYS',
                            help='Only refresh folders with no descriptor or one older than DAYS')
        parser.add_argument('--limit', type=int, default=0, help='Stop after N folders (testing)')

    def handle(self, *args, **options):
        self.verbosity = options['verbosity']
        folders = Folder.objects.filter(
            is_third_party=True, third_party_source='adapt', deletion_in_progress=False,
        ).select_related('parent').order_by('third_party_id')

        only = options['only'].strip('/').replace('\\', '/')
        if only:
            folders = folders.filter(third_party_id__startswith=only)

        if options['stale'] is not None:
            cutoff = timezone.now() - timedelta(days=options['stale'])
            folders = folders.exclude(acl__fetched_at__gte=cutoff, acl__error='')

        if options['limit']:
            folders = folders[:options['limit']]

        total = folders.count() if not options['limit'] else len(list(folders))
        self.stdout.write(f'ADAPT folders to check: {total}' + ('  (dry run)' if options['dry_run'] else ''))

        stats = {'fetched': 0, 'stored': 0, 'unchanged': 0, 'errors': 0}
        started = time.monotonic()
        for folder in folders:
            rel = folder.third_party_id
            if rel is None:
                # The ADAPT root itself: share root.
                rel = ''
            try:
                blob = self.fetch(rel)
                descriptor = adapt_acl.parse(blob)
            except Exception as exc:  # network, auth, or a descriptor we cannot read
                stats['errors'] += 1
                self._log(1, f'  ! {rel or "/"}: {exc}')
                if not options['dry_run']:
                    self._record_error(folder, str(exc))
                continue

            stats['fetched'] += 1
            self._log(2, f'  · {rel or "/"}: owner {descriptor.owner}, {len(descriptor.aces)} ACEs')
            if options['dry_run']:
                continue

            existing = FolderAcl.objects.filter(folder=folder).first()
            if existing is not None and bytes(existing.descriptor) == blob and not existing.error:
                existing.fetched_at = timezone.now()
                existing.save(update_fields=['fetched_at'])
                stats['unchanged'] += 1
                continue

            FolderAcl.objects.update_or_create(
                folder=folder,
                defaults={
                    'descriptor': blob,
                    'owner_sid': descriptor.owner or '',
                    'ace_count': len(descriptor.aces),
                    'fetched_at': timezone.now(),
                    'error': '',
                },
            )
            stats['stored'] += 1

        elapsed = time.monotonic() - started
        self.stdout.write(
            f"Done in {elapsed:.1f}s: fetched {stats['fetched']}, stored {stats['stored']}, "
            f"unchanged {stats['unchanged']}, errors {stats['errors']}"
        )
        if stats['errors'] and stats['fetched'] == 0:
            self.stderr.write(self.style.ERROR(
                'Every fetch failed. Check ADAPT_HOST/ADAPT_USER/ADAPT_PASS and that port 445 on the '
                'file server is reachable from this container.'
            ))

    # Separated so tests can replace it without touching the network.
    def fetch(self, rel_path):
        return adapt_acl.fetch_security_descriptor(rel_path)

    def _record_error(self, folder, message):
        existing = FolderAcl.objects.filter(folder=folder).first()
        if existing is None:
            # No descriptor yet: store the error with an empty descriptor so the
            # failure is visible in admin. permissions treats empty as absent.
            FolderAcl.objects.create(folder=folder, descriptor=b'', fetched_at=timezone.now(), error=message[:2000])
        else:
            existing.error = message[:2000]
            existing.save(update_fields=['error'])

    def _log(self, level, message):
        if self.verbosity >= level:
            self.stdout.write(message)
