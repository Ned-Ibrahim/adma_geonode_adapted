"""
Management command to set up the John Deere root data folder.

Usage:
    python manage.py setup_johndeere
    python manage.py setup_johndeere --force  # Recreate and make existing folder public
"""

from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from filemanager.models import Folder, File

User = get_user_model()


class Command(BaseCommand):
    help = 'Set up the John Deere root data folder for third-party data sync (public and visible to all users)'

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
            help='Make existing John Deere folder and all contents public (without recreating)',
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
        
        # Check if John Deere folder already exists (from any owner with this source)
        existing_folder = Folder.objects.filter(
            name='John Deere',
            parent=None,
            is_third_party=True,
            third_party_source='johndeere'
        ).first()
        
        # Handle --make-public flag
        if make_public and existing_folder:
            self._make_folder_public_recursive(existing_folder)
            return
        
        if existing_folder and not force:
            self.stdout.write(
                self.style.WARNING(f'John Deere folder already exists (ID: {existing_folder.id})')
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
            self.stdout.write(self.style.WARNING('Deleting existing John Deere folder...'))
            existing_folder.delete()
        
        # Create the John Deere root folder - PUBLIC by default so all users can see it
        johndeere_folder = Folder.objects.create(
            name='John Deere',
            owner=owner,
            parent=None,
            is_public=True,  # PUBLIC: visible to all users
            is_third_party=True,
            third_party_source='johndeere',
            third_party_id='johndeere_root',
        )
        
        self.stdout.write(self.style.SUCCESS('Successfully created John Deere root folder!'))
        self.stdout.write(f'  Folder ID: {johndeere_folder.id}')
        self.stdout.write(f'  Name: {johndeere_folder.name}')
        self.stdout.write(f'  Owner: {johndeere_folder.owner.username}')
        self.stdout.write(f'  Public: {johndeere_folder.is_public} (visible to ALL users)')
        self.stdout.write(f'  Third-party: {johndeere_folder.is_third_party}')
        self.stdout.write(f'  Source: {johndeere_folder.third_party_source}')
        self.stdout.write('')
        self.stdout.write(self.style.NOTICE('Note: All folders and files added will also be PUBLIC.'))
        self.stdout.write('')
        self.stdout.write('Next steps:')
        self.stdout.write('  1. Upload John Deere data files to this folder')
        self.stdout.write('  2. Or implement John Deere API integration for automatic sync')
    
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
