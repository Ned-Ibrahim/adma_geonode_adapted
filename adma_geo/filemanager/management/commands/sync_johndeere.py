"""
Management command to sync John Deere Operations Center data.

Usage:
    python manage.py sync_johndeere --once     # Run sync once synchronously
    python manage.py sync_johndeere --async    # Queue sync task via Celery
"""

from django.core.management.base import BaseCommand
from filemanager.tasks import sync_johndeere_task


class Command(BaseCommand):
    help = 'Sync John Deere Operations Center data (fields, boundaries, and field operations)'

    def add_arguments(self, parser):
        parser.add_argument(
            '--once',
            action='store_true',
            help='Run the sync once synchronously (blocking)',
        )
        parser.add_argument(
            '--async',
            action='store_true',
            dest='run_async',
            help='Queue the sync task via Celery (non-blocking)',
        )

    def handle(self, *args, **options):
        run_once = options['once']
        run_async = options['run_async']
        
        if not run_once and not run_async:
            self.stdout.write(self.style.WARNING('Please specify --once or --async'))
            self.stdout.write('')
            self.stdout.write('Options:')
            self.stdout.write('  --once   Run sync synchronously (blocking, see output immediately)')
            self.stdout.write('  --async  Queue sync via Celery (non-blocking, runs in background)')
            return
        
        if run_async:
            self.stdout.write('Queuing John Deere sync task via Celery...')
            result = sync_johndeere_task.delay()
            self.stdout.write(self.style.SUCCESS(f'Task queued with ID: {result.id}'))
            self.stdout.write('Check Celery logs for progress.')
        else:
            self.stdout.write('Running John Deere sync synchronously...')
            self.stdout.write('')
            
            # Run the task directly (not via Celery)
            result = sync_johndeere_task()
            
            self.stdout.write('')
            if result.get('success'):
                self.stdout.write(self.style.SUCCESS('John Deere sync completed successfully!'))
            else:
                self.stdout.write(self.style.ERROR('John Deere sync completed with errors.'))
            
            self.stdout.write('')
            self.stdout.write('Results:')
            self.stdout.write(f"  Fields found: {result.get('fields_found', 0)}")
            self.stdout.write(f"  Field folders created: {result.get('field_folders_created', 0)}")
            self.stdout.write(f"  Field files created: {result.get('field_files_created', 0)}")
            self.stdout.write(f"  Operations found: {result.get('operations_found', 0)}")
            self.stdout.write(f"  Operation folders created: {result.get('operation_folders_created', 0)}")
            self.stdout.write(f"  Operation files created: {result.get('operation_files_created', 0)}")
            
            if result.get('errors'):
                self.stdout.write('')
                self.stdout.write(self.style.ERROR('Errors:'))
                for error in result['errors']:
                    self.stdout.write(f"  - {error}")
