import json
from pathlib import Path
from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from vtn_hub.models import Team

DEFAULT_TEAMS = [
    {"team_name": "Team-01", "token": "KEY-TEAM01-A7F9"},
    {"team_name": "Team-02", "token": "KEY-TEAM02-B8E1"},
    {"team_name": "Team-03", "token": "KEY-TEAM03-C3D4"},
    {"team_name": "Team-04", "token": "KEY-TEAM04-D9E2"},
    {"team_name": "Team-05", "token": "KEY-TEAM05-E4A7"},
    {"team_name": "Team-06", "token": "KEY-TEAM06-F1C8"},
    {"team_name": "Team-07", "token": "KEY-TEAM07-G2B9"},
    {"team_name": "Team-08", "token": "KEY-TEAM08-H5D3"},
    {"team_name": "Team-09", "token": "KEY-TEAM09-J6E4"},
    {"team_name": "Team-10", "token": "KEY-TEAM10-K7F5"},
    {"team_name": "Team-11", "token": "KEY-TEAM11-L8G6"},
    {"team_name": "Team-12", "token": "KEY-TEAM12-M9H7"},
    {"team_name": "Team-13", "token": "KEY-TEAM13-N1J8"},
    {"team_name": "Team-14", "token": "KEY-TEAM14-P2K9"},
    {"team_name": "Team-15", "token": "KEY-TEAM15-Q3L1"},
    {"team_name": "Team-16", "token": "KEY-TEAM16-R4M2"},
    {"team_name": "Team-17", "token": "KEY-TEAM17-S5N3"},
    {"team_name": "Team-18", "token": "KEY-TEAM18-T6P4"},
    {"team_name": "Team-19", "token": "KEY-TEAM19-U7Q5"},
    {"team_name": "Team-20", "token": "KEY-TEAM20-V8R6"},
]


class Command(BaseCommand):
    help = "Seed 20 pre-authorized teams, tokens, and admin account into database"

    def handle(self, *args, **options):
        # 1. Seed teams
        created_cnt = 0
        for t in DEFAULT_TEAMS:
            obj, created = Team.objects.get_or_create(
                team_name=t["team_name"],
                defaults={"token": t["token"]},
            )
            if created:
                created_cnt += 1

        self.stdout.write(self.style.SUCCESS(f"Seeded {created_cnt} new teams ({Team.objects.count()} total)."))

        # 2. Seed superuser admin / admin123
        User = get_user_model()
        admin_user, created = User.objects.get_or_create(
            username="admin",
            defaults={"email": "admin@openadr.local", "is_staff": True, "is_superuser": True},
        )
        admin_user.set_password("admin123")
        admin_user.is_staff = True
        admin_user.is_superuser = True
        admin_user.save()
        if created:
            self.stdout.write(self.style.SUCCESS("Superuser created: admin / admin123"))
        else:
            self.stdout.write(self.style.SUCCESS("Superuser 'admin' verified with password 'admin123'."))
