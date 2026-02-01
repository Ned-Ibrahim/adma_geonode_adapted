"""
Management command to rename Realm5 device folders from dev_eui to friendly_name.

This is a one-time migration script that should be run after updating the sync_realm5_task
to use friendly_name instead of dev_eui for folder names.

Usage:
    python manage.py rename_realm5_folders          # Dry run (shows what would change)
    python manage.py rename_realm5_folders --apply  # Actually rename folders
"""
from django.core.management.base import BaseCommand
from django.conf import settings
from filemanager.models import Folder
from filemanager.realm5_client import Realm5Client


class Command(BaseCommand):
    help = 'Rename Realm5 device folders from dev_eui to friendly_name'

    def add_arguments(self, parser):
        parser.add_argument(
            '--apply',
            action='store_true',
            help='Actually apply the renames. Without this flag, only shows what would change (dry run).',
        )

    def handle(self, *args, **options):
        apply_changes = options['apply']
        
        if apply_changes:
            self.stdout.write(self.style.WARNING('APPLYING CHANGES - This will rename folders!'))
        else:
            self.stdout.write(self.style.NOTICE('DRY RUN - No changes will be made. Use --apply to actually rename.'))
        
        self.stdout.write('')
        
        # Find the Realm5 root folder
        realm5_folder = Folder.objects.filter(
            name='realm5',
            parent=None,
            is_third_party=True,
            third_party_source='realm5'
        ).first()
        
        if not realm5_folder:
            self.stdout.write(self.style.ERROR('Realm5 root folder not found. Run "python manage.py setup_realm5" first.'))
            return
        
        self.stdout.write(f'Found Realm5 root folder: {realm5_folder.name} (ID: {realm5_folder.id})')
        
        # Initialize Realm5 API client
        api_key = getattr(settings, 'REALM5_API_KEY', None)
        if not api_key:
            self.stdout.write(self.style.ERROR('REALM5_API_KEY not configured in settings.'))
            return
        
        client = Realm5Client(api_key=api_key)
        
        # Get all devices from API
        try:
            devices = client.get_devices()
            self.stdout.write(f'Found {len(devices)} devices from Realm5 API')
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'Failed to fetch devices: {e}'))
            return
        
        # Build mapping of dev_eui -> friendly_name
        dev_eui_to_friendly_name = {}
        for device in devices:
            if device.get('device_type') != 'weather_station':
                continue
            
            dev_eui_hex = device.get('dev_eui_hex')
            dev_eui_numeric = device.get('dev_eui') or device.get('devEui') or device.get('id')
            dev_eui = str(dev_eui_hex) if dev_eui_hex else str(dev_eui_numeric)
            friendly_name = device.get('friendly_name') or device.get('name') or dev_eui
            
            dev_eui_to_friendly_name[dev_eui] = friendly_name.strip()
            # Also map numeric dev_eui string
            dev_eui_to_friendly_name[str(dev_eui_numeric)] = friendly_name.strip()
        
        self.stdout.write(f'Found {len(dev_eui_to_friendly_name) // 2} weather station devices')
        self.stdout.write('')
        
        # Get all device folders under Realm5
        device_folders = Folder.objects.filter(parent=realm5_folder)
        
        renamed_count = 0
        skipped_count = 0
        error_count = 0
        
        for folder in device_folders:
            old_name = folder.name
            
            # Check if folder name looks like a dev_eui (hex or numeric)
            # or if it's already a friendly name
            new_name = None
            
            # Try to find matching friendly_name
            if old_name in dev_eui_to_friendly_name:
                new_name = dev_eui_to_friendly_name[old_name]
            elif folder.third_party_id and folder.third_party_id in dev_eui_to_friendly_name:
                new_name = dev_eui_to_friendly_name[folder.third_party_id]
            
            if not new_name:
                self.stdout.write(f'  SKIP: {old_name} - No matching device found in API')
                skipped_count += 1
                continue
            
            if old_name == new_name:
                self.stdout.write(f'  OK: {old_name} - Already using friendly name')
                skipped_count += 1
                continue
            
            # Check for name conflict
            existing = Folder.objects.filter(
                name=new_name,
                parent=realm5_folder
            ).exclude(id=folder.id).exists()
            
            if existing:
                self.stdout.write(self.style.WARNING(
                    f'  CONFLICT: {old_name} -> {new_name} - Folder with new name already exists'
                ))
                error_count += 1
                continue
            
            if apply_changes:
                try:
                    folder.name = new_name
                    folder.save()
                    self.stdout.write(self.style.SUCCESS(f'  RENAMED: {old_name} -> {new_name}'))
                    renamed_count += 1
                except Exception as e:
                    self.stdout.write(self.style.ERROR(f'  ERROR: {old_name} -> {new_name}: {e}'))
                    error_count += 1
            else:
                self.stdout.write(f'  WOULD RENAME: {old_name} -> {new_name}')
                renamed_count += 1
        
        self.stdout.write('')
        self.stdout.write('=' * 50)
        
        if apply_changes:
            self.stdout.write(f'Renamed: {renamed_count} folders')
        else:
            self.stdout.write(f'Would rename: {renamed_count} folders')
        
        self.stdout.write(f'Skipped: {skipped_count} folders')
        self.stdout.write(f'Errors/Conflicts: {error_count} folders')
        
        if not apply_changes and renamed_count > 0:
            self.stdout.write('')
            self.stdout.write(self.style.NOTICE('Run with --apply to actually rename the folders:'))
            self.stdout.write('  python manage.py rename_realm5_folders --apply')
