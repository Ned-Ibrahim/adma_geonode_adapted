"""
Management command to sync data from Realm5 API.

Usage:
    python manage.py sync_realm5           # Run async via Celery
    python manage.py sync_realm5 --once    # Run synchronously (for testing)
"""

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Sync data from Realm5 API (devices and observations)'

    def add_arguments(self, parser):
        parser.add_argument(
            '--once',
            action='store_true',
            help='Run synchronously instead of via Celery (for testing)',
        )
        parser.add_argument(
            '--test-connection',
            action='store_true',
            help='Only test the API connection',
        )

    def handle(self, *args, **options):
        from django.conf import settings
        
        # Check if API key is configured
        api_key = getattr(settings, 'REALM5_API_KEY', None)
        if not api_key:
            self.stderr.write(
                self.style.ERROR('REALM5_API_KEY not configured in Django settings!')
            )
            self.stdout.write('Add to your settings.py:')
            self.stdout.write('  REALM5_API_KEY = "your_api_key_here"')
            return
        
        if options['test_connection']:
            self._test_connection(api_key)
            return
        
        if options['once']:
            self._run_sync_once()
        else:
            self._run_sync_async()
    
    def _test_connection(self, api_key):
        """Test the Realm5 API connection."""
        from filemanager.realm5_client import Realm5Client
        
        self.stdout.write('Testing Realm5 API connection...')
        
        client = Realm5Client(api_key=api_key)
        
        try:
            devices = client.get_devices()
            self.stdout.write(self.style.SUCCESS(f'✓ Connection successful!'))
            self.stdout.write(f'  Found {len(devices)} devices')
            
            if devices:
                self.stdout.write('')
                self.stdout.write('Devices:')
                for device in devices[:10]:  # Show first 10 devices
                    dev_eui = device.get('dev_eui_hex') or device.get('dev_eui') or device.get('devEui') or device.get('id', 'unknown')
                    name = device.get('friendly_name') or device.get('name') or 'N/A'
                    device_type = device.get('device_type', 'unknown')
                    self.stdout.write(f'  - {dev_eui}: {name} ({device_type})')
                
                if len(devices) > 10:
                    self.stdout.write(f'  ... and {len(devices) - 10} more')
                    
        except Exception as e:
            self.stderr.write(self.style.ERROR(f'✗ Connection failed: {e}'))
    
    def _run_sync_once(self):
        """Run the sync task synchronously."""
        from filemanager.tasks import sync_realm5_task
        
        self.stdout.write('Running Realm5 sync synchronously...')
        self.stdout.write('')
        
        # Run the task directly (not via Celery)
        result = sync_realm5_task()
        
        if result['success']:
            self.stdout.write(self.style.SUCCESS('✓ Sync completed successfully!'))
        else:
            self.stdout.write(self.style.ERROR('✗ Sync completed with errors'))
        
        self.stdout.write('')
        self.stdout.write('Results:')
        self.stdout.write(f'  Devices found: {result["devices_found"]}')
        self.stdout.write(f'  Folders created: {result["folders_created"]}')
        self.stdout.write(f'  Files created: {result["files_created"]}')
        
        if result['errors']:
            self.stdout.write('')
            self.stdout.write(self.style.WARNING('Errors:'))
            for error in result['errors']:
                self.stdout.write(f'  - {error}')
    
    def _run_sync_async(self):
        """Run the sync task via Celery."""
        from filemanager.tasks import sync_realm5_task
        
        self.stdout.write('Queuing Realm5 sync task via Celery...')
        
        task = sync_realm5_task.delay()
        
        self.stdout.write(self.style.SUCCESS(f'✓ Task queued successfully!'))
        self.stdout.write(f'  Task ID: {task.id}')
        self.stdout.write('')
        self.stdout.write('Monitor progress with:')
        self.stdout.write('  docker-compose logs -f celery')
