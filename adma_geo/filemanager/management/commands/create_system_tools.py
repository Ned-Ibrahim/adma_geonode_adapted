"""
Management command to create/update system tools in the database.

Usage:
    python manage.py create_system_tools
"""

from django.core.management.base import BaseCommand
from filemanager.models import Tool


class Command(BaseCommand):
    help = 'Create or update system tools in the database'

    def add_arguments(self, parser):
        parser.add_argument(
            '--force',
            action='store_true',
            help='Force update all system tools even if they exist',
        )

    def handle(self, *args, **options):
        self.stdout.write('Creating/updating system tools...')
        
        results = Tool.create_system_tools()
        
        created_count = 0
        updated_count = 0
        
        for tool, was_created in results:
            if was_created:
                created_count += 1
                self.stdout.write(
                    self.style.SUCCESS(f'  Created: {tool.name} ({tool.slug})')
                )
            else:
                updated_count += 1
                self.stdout.write(
                    self.style.WARNING(f'  Updated: {tool.name} ({tool.slug})')
                )
        
        self.stdout.write('')
        self.stdout.write(
            self.style.SUCCESS(
                f'Done! Created {created_count} new tools, updated {updated_count} existing tools.'
            )
        )
        
        # Show summary of all tools
        self.stdout.write('')
        self.stdout.write('Current system tools:')
        for tool in Tool.objects.filter(is_system_tool=True).order_by('category', 'name'):
            status_icon = '✓' if tool.status == 'available' else '○'
            self.stdout.write(
                f'  {status_icon} [{tool.get_category_display()}] {tool.name} v{tool.version} - {tool.status}'
            )
