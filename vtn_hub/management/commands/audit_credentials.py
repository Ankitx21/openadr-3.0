from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from vtn_hub.models import Team
import re


class Command(BaseCommand):
    help = 'Fail deployment if known original demo credentials remain in use.'

    def handle(self, *args, **options):
        weak_teams = [team.team_name for team in Team.objects.all() if re.fullmatch(r'KEY-TEAM\d{2}-[A-Z0-9]{4}', team.token)]
        weak_users = [user.username for user in get_user_model().objects.filter(is_staff=True, is_active=True) if user.check_password('admin123')]
        if weak_teams or weak_users:
            raise CommandError('Known demo credentials detected. Rotate team tokens for: ' + ', '.join(weak_teams) + '; change organizer passwords for: ' + ', '.join(weak_users))
        self.stdout.write(self.style.SUCCESS('No known original demo credentials detected.'))
