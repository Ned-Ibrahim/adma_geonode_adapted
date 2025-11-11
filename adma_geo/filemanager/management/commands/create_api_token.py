#!/usr/bin/env python3

"""
Management command to create API tokens for users.
"""

from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from rest_framework.authtoken.models import Token


class Command(BaseCommand):
    help = 'Create or retrieve API token for a user'

    def add_arguments(self, parser):
        parser.add_argument('username', type=str, help='Username to create token for')
        parser.add_argument(
            '--regenerate',
            action='store_true',
            help='Regenerate token if it already exists'
        )

    def handle(self, *args, **options):
        username = options['username']
        regenerate = options['regenerate']

        try:
            user = User.objects.get(username=username)
        except User.DoesNotExist:
            self.stdout.write(
                self.style.ERROR(f'User "{username}" does not exist')
            )
            return

        if regenerate:
            # Delete existing token if it exists
            Token.objects.filter(user=user).delete()
            token, created = Token.objects.get_or_create(user=user)
            self.stdout.write(
                self.style.SUCCESS(f'Regenerated token for user "{username}": {token.key}')
            )
        else:
            token, created = Token.objects.get_or_create(user=user)
            if created:
                self.stdout.write(
                    self.style.SUCCESS(f'Created token for user "{username}": {token.key}')
                )
            else:
                self.stdout.write(
                    self.style.WARNING(f'Token already exists for user "{username}": {token.key}')
                )
                self.stdout.write(
                    self.style.WARNING('Use --regenerate to create a new token')
                )
