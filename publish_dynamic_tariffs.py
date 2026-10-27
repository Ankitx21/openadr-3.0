"""Publish using the same validated Django tariff implementation as the portal.

Run from the service shell (with DATABASE_URL and REDIS_URL configured), or use
Publish a new tariff schedule in the organizer portal. Team tokens cannot publish.
"""
import os
import sys

if __name__ == '__main__':
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'openadr_django.settings')
    from django.core.management import execute_from_command_line
    execute_from_command_line([sys.argv[0], 'publish_tariffs', *sys.argv[1:]])
