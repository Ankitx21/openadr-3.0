"""Owner-controlled logins read exclusively from private server configuration. No credential administration is exposed over HTTP."""
import json
import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from vtn_hub.models import DashboardAccount, Team


class Command(BaseCommand):
    help = 'Apply predefined login IDs using private DASHBOARD_PASSWORDS_JSON credentials only.'

    def add_arguments(self, parser):
        parser.add_argument('--count', type=int, default=20)

    @transaction.atomic
    def handle(self, *args, **options):
        count = options['count']
        if not 1 <= count <= 200:
            raise CommandError('Team count must be between 1 and 200.')
        configured = os.environ.get('DASHBOARD_PASSWORDS_JSON')
        if not configured:
            self.stdout.write('DASHBOARD_PASSWORDS_JSON is not configured. Existing credentials preserved; no dashboard logins created. Set it privately in Render and rebuild to provision logins.')
            return
        try:
            overrides = json.loads(configured)
        except (ValueError, TypeError):
            raise CommandError('DASHBOARD_PASSWORDS_JSON must be a valid JSON object.')
        if not isinstance(overrides, dict) or any(not isinstance(k, str) or not isinstance(v, str) or not 8 <= len(v) <= 128 for k, v in overrides.items()):
            raise CommandError('Password overrides must map login IDs to passwords of 8-128 characters.')
        specs = [('admin', 'organizer', None), ('organizer', 'organizer', None), ('jury', 'jury', None)]
        for index in range(1, count + 1):
            team = Team.objects.filter(team_name=f'Team-{index:02d}').first()
            if team:
                specs.append((f'team{index:02d}', 'team', team))
        canonical = {username for username, _, _ in specs}
        if set(overrides) - canonical:
            raise CommandError('A password override names an unknown predefined login ID.')
        User = get_user_model()
        for username, role, team in specs:
            if username not in overrides:
                continue
            password = overrides[username]
            if team and password == team.token:
                raise CommandError('Dashboard passwords must differ from device API tokens.')
            user, created = User.objects.get_or_create(username=username)
            if team:
                # Replace any previous UI-created alias for this team with the fixed login ID.
                previous = DashboardAccount.objects.filter(team=team).exclude(user=user)
                previous_ids = list(previous.values_list('user_id', flat=True))
                previous.delete()
                User.objects.filter(pk__in=previous_ids, is_superuser=False).exclude(username__in=canonical).update(is_active=False)
            user.is_staff = role == 'organizer'
            user.is_superuser = username == 'admin'
            user.is_active = True
            if created or not user.check_password(password):
                user.set_password(password)
            user.save()
            if role != 'organizer':
                user.groups.clear()
                user.user_permissions.clear()
            DashboardAccount.objects.update_or_create(user=user, defaults={'role': role, 'team': team})
        self.stdout.write(self.style.SUCCESS(f'Applied {len(overrides)} owner-assigned dashboard logins. Passwords are not printed. API tokens unchanged.'))
