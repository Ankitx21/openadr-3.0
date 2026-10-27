# OpenADR 3.0

Public organizer and jury dashboard for verifying team progress. The homepage and analytics open without a password; anyone with the URL can view the dashboard and charts. Telemetry CSV exports remain restricted to organizer staff. Django admin and all API write operations remain protected. Routes: `/` for the overview, `/analytics/` for team telemetry, and `/admin/` for team management. Teams submit data using their assigned API tokens and the Python/device clients. There is no student web workspace or dashboard tariff publishing control.

## Existing Render test deployment

This repository is itself the Django service root. Leave Render's Root Directory blank. Use the existing SQLite database (`db.sqlite3`); no PostgreSQL or Redis service is required. Keep the service's existing SECRET_KEY and DEBUG=False environment variables. The Render hostname is allowed automatically. For a custom domain add its hostname to ALLOWED_HOSTS.

Build command:

```sh
pip install -r requirements.txt && python manage.py collectstatic --noinput && python manage.py migrate && python manage.py seed_teams
```

Start command:

```sh
gunicorn openadr_django.wsgi:application --bind 0.0.0.0:$PORT --workers 1 --threads 4 --timeout 60
```

The included `gunicorn.conf.py` also selects a single worker when Redis is absent, even if the existing Render start command still specifies two workers. Threads share the in-memory cache and its request limits. Use a single Render service instance for this test setup. Limits reset when its worker restarts. REDIS_URL remains optional for future shared-cache deployments; it is not needed for this test.

The blueprint uses the existing free web-service plan and creates no additional databases or cache services. An existing manually configured Render service may keep its saved build/start commands; compare them to the commands above. Pushing main may automatically deploy when Render auto-deploy is enabled.

SQLite on Render's default filesystem is temporary. Runtime data can disappear on redeploys/restarts, as with the original test setup. This update does not transfer a live database from Render or upload your local test database. Back up any trial data you need before redeploying. Durable production storage can be configured separately later. See [Render filesystem documentation](https://render.com/docs/disks).

Seeding creates only missing teams with random tokens and never resets existing passwords or tokens. The already-tracked original SQLite snapshot remains unchanged by this update. Existing demo credentials continue working for process testing; change them before exposing the service beyond that trial. Use `python manage.py changepassword admin` to change the organizer password, and `python manage.py rotate_team_token Team-01` to rotate a team's token. `audit_credentials` is available as a manual check and does not block the test deployment.

## Local run (PowerShell)

From this repository's directory:

```powershell
$env:DEBUG = 'True'
Remove-Item Env:REDIS_URL -ErrorAction SilentlyContinue
python -m pip install -r requirements.txt
python manage.py migrate
python manage.py seed_teams
python manage.py runserver
```

Open http://127.0.0.1:8000/ directly; no dashboard account is required. Use `python manage.py createsuperuser` only if you need protected Django admin access. If using the shared workspace virtual environment, replace `python` with `D:\openADR\.venv\Scripts\python.exe`. Set SQLITE_PATH only if you want to use a different SQLite file.

## Request protection

Dashboard chart data and tariff CSV downloads are public and rate limited. Telemetry CSV exports require organizer staff access. Free-text telemetry notes are included only in staff exports. Team-authenticated requests remain scoped to their own telemetry. Team submissions require a team token, and tariff publishing and Django administration require organizer staff access; session mutations require CSRF. Login attempts, API traffic, tariff downloads, telemetry, and exports have separate limits and return Retry-After on HTTP 429. JSON bodies are capped at 64 KB; teams can submit at most 12 appliances per phase. Telemetry replaces an existing 15-minute appliance slot instead of accumulating duplicates. Testing retains at most 20 records per team.

The single-worker test configuration shares limits across threads, but does not provide distributed or restart-persistent throttling. Analytics cover at most 31 days and exports stream in chunks. Invalid, nonfinite, negative, or incorrectly timestamped readings are rejected. Production-mode HTTPS, secure cookies, host checks, and CSRF remain enabled. This competition API uses OpenADR terminology; it does not implement the complete OpenADR 3 certification specification.

## Tariffs

The 48-hour graph and tariff downloads remain available. To publish a schedule from a service shell or a future scheduled job:

```sh
python manage.py publish_tariffs --min-price 8 --max-price 20
```

`publish_dynamic_tariffs.py` calls the same command. No continuously running scheduler is installed by this update. Currently the first tariff request after midnight IST rolls the schedule forward and preserves previously announced tomorrow prices. Manually publishing replaces all 48 prices, including prices teams may have cached.

## Verification

```sh
python manage.py collectstatic --noinput
python manage.py test vtn_hub
python manage.py makemigrations --check --dry-run
```

Tests use an isolated database. Migration 0006 removes duplicate legacy slots, keeping the most recently saved record, before adding the uniqueness constraint. Existing team tokens and organizer passwords are preserved. Browser visual checks require an available browser connection.
