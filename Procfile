web: gunicorn openadr_django.wsgi:application --bind 0.0.0.0:$PORT
release: python manage.py migrate && python manage.py seed_teams
