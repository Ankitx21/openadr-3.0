import os
import secrets
from django.core.management.base import BaseCommand, CommandError
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from vtn_hub.models import Team


class Command(BaseCommand):
    help = 'Create missing teams with random tokens. Never reset existing credentials.'

    def add_arguments(self, parser):
        parser.add_argument('--count', type=int, default=20)

    def handle(self, *args, **options):
        count = options['count']
        if not 1 <= count <= 200:
            raise CommandError('Team count must be between 1 and 200.')
        created = 0
        for index in range(1, count + 1):
            _, new = Team.objects.get_or_create(team_name=f'Team-{index:02d}', defaults={'token': secrets.token_urlsafe(32)})
            created += new
        self.stdout.write(self.style.SUCCESS(f'Created {created} teams. Existing tokens unchanged.'))
        username = os.environ.get('DJANGO_SUPERUSER_USERNAME')
        password = os.environ.get('DJANGO_SUPERUSER_PASSWORD')
        if username and password:
            User = get_user_model()
            if not User.objects.filter(username=username).exists():
                user = User(username=username, email=os.environ.get('DJANGO_SUPERUSER_EMAIL', ''))
                try:
                    validate_password(password, user)
                except ValidationError as exc:
                    raise CommandError('; '.join(exc.messages))
                User.objects.create_superuser(username=username, password=password, email=user.email)
                self.stdout.write('Organizer account created.')
        else:
            self.stdout.write('Use createsuperuser to create an organizer; no default password is installed.')
