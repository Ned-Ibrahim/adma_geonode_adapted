"""
Management command to set up the Realm5 root data folder.

Usage:
    python manage.py setup_realm5
    python manage.py setup_realm5 --force  # Recreate and make existing folder public
"""

from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from filemanager.models import Folder, File

User = get_user_model()


class Command(BaseCommand):
    help = 'Set up the Realm5 root data folder for third-party data sync (public and visible to all users)'

    def add_arguments(self, parser):
        parser.add_argument(
            '--owner',
            type=str,
            default='admin',
            help='Username of the folder owner (default: admin)',
        )
        parser.add_argument(
            '--force',
            action='store_true',
            help='Force recreation of the folder even if it exists',
        )
        parser.add_argument(
            '--make-public',
            action='store_true',
            help='Make existing realm5 folder and all contents public (without recreating)',
        )

    def handle(self, *args, **options):
        owner_username = options['owner']
        force = options['force']
        make_public = options['make_public']
        
        # Get the owner user
        try:
            owner = User.objects.get(username=owner_username)
        except User.DoesNotExist:
            self.stderr.write(
                self.style.ERROR(f'User "{owner_username}" does not exist. Please create the user first.')
            )
            return
        
        # Check if realm5 folder already exists (from any owner with this source)
        existing_folder = Folder.objects.filter(
            name='realm5',
            parent=None,
            is_third_party=True,
            third_party_source='realm5'
        ).first()
        
        # Handle --make-public flag
        if make_public and existing_folder:
            self._make_folder_public_recursive(existing_folder)
            return
        
        if existing_folder and not force:
            self.stdout.write(
                self.style.WARNING(f'Realm5 folder already exists (ID: {existing_folder.id})')
            )
            self.stdout.write(f'  Owner: {existing_folder.owner.username}')
            self.stdout.write(f'  Public: {existing_folder.is_public}')
            self.stdout.write(f'  Third-party source: {existing_folder.third_party_source}')
            self.stdout.write('')
            self.stdout.write('Options:')
            self.stdout.write('  --force       Recreate the folder')
            self.stdout.write('  --make-public Make existing folder and all contents public')
            return
        
        if existing_folder and force:
            self.stdout.write(self.style.WARNING('Deleting existing Realm5 folder...'))
            existing_folder.delete()
        
        # Create the realm5 root folder - PUBLIC by default so all users can see it
        realm5_folder = Folder.objects.create(
            name='realm5',
            owner=owner,
            parent=None,
            is_public=True,  # PUBLIC: visible to all users
            is_third_party=True,
            third_party_source='realm5',
            third_party_id='realm5_root',
        )
        
        self.stdout.write(self.style.SUCCESS('Successfully created Realm5 root folder!'))
        self.stdout.write(f'  Folder ID: {realm5_folder.id}')
        self.stdout.write(f'  Name: {realm5_folder.name}')
        self.stdout.write(f'  Owner: {realm5_folder.owner.username}')
        self.stdout.write(f'  Public: {realm5_folder.is_public} (visible to ALL users)')
        self.stdout.write(f'  Third-party: {realm5_folder.is_third_party}')
        self.stdout.write(f'  Source: {realm5_folder.third_party_source}')
        self.stdout.write('')
        self.stdout.write(self.style.NOTICE('Note: All folders and files created by sync will also be PUBLIC.'))
        self.stdout.write('')
        self.stdout.write('Next steps:')
        self.stdout.write('  1. Make sure REALM5_API_KEY is set in your Django settings')
        self.stdout.write('  2. Run: python manage.py sync_realm5 --once')
        self.stdout.write('  3. Or set up Celery Beat for automatic daily sync')
    
    def _make_folder_public_recursive(self, folder):
        """Make a folder and all its contents (subfolders and files) public."""
        self.stdout.write(f'Making folder "{folder.name}" and all contents public...')
        
        folders_updated = 0
        files_updated = 0
        
        def update_recursive(current_folder):
            nonlocal folders_updated, files_updated
            
            # Update current folder
            if not current_folder.is_public:
                current_folder.is_public = True
                current_folder.save(update_fields=['is_public'])
                folders_updated += 1
            
            # Update all files in current folder
            for file_obj in current_folder.files.filter(is_public=False):
                file_obj.is_public = True
                file_obj.save(update_fields=['is_public'])
                files_updated += 1
            
            # Recursively update all subfolders
            for subfolder in current_folder.subfolders.all():
                update_recursive(subfolder)
        
        update_recursive(folder)
        
        self.stdout.write(self.style.SUCCESS(
            f'Done! Updated {folders_updated} folders and {files_updated} files to public.'
        ))
