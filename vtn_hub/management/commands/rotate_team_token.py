from django.core.management.base import BaseCommand, CommandError
from vtn_hub.models import Team
import secrets


class Command(BaseCommand):
    help = 'Rotate the token of one team; its old token stops working immediately.'

    def add_arguments(self, parser):
        parser.add_argument('team_name')

    def handle(self, *args, **options):
        try:
            team = Team.objects.get(team_name=options['team_name'])
        except Team.DoesNotExist:
            raise CommandError('Team not found.')
        team.token = secrets.token_urlsafe(32)
        team.save(update_fields=['token'])
        self.stdout.write(f'Token rotated for {team.team_name}. Retrieve it from organizer team management; it is not printed to logs.')
