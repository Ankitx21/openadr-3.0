web: gunicorn openadr_django.wsgi:application --bind 0.0.0.0:$PORT --workers 2 --threads 8 --timeout 60
release: python manage.py migrate && python manage.py seed_teams
