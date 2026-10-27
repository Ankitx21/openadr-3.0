web: gunicorn openadr_django.wsgi:application --bind 0.0.0.0:$PORT --workers 1 --threads 4 --timeout 60
release: python manage.py migrate && python manage.py seed_teams
