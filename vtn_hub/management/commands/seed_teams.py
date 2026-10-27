import secrets
from django.core.management.base import BaseCommand, CommandError
from django.core.management import call_command
from vtn_hub.models import Team


class Command(BaseCommand):
    help = 'Create missing teams, preserve API tokens, and apply owner-defined dashboard logins.'

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
        self.stdout.write(self.style.SUCCESS(f'Created {created} teams. Existing API tokens unchanged.'))
        call_command('seed_dashboard_logins', count=count, stdout=self.stdout)
