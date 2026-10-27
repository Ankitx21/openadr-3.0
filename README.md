# OpenADR 3.0

Authenticated dashboard for organizers, juries, and teams. Device API tokens stay independent from dashboard passwords. The previously public homepage and chart/download endpoints now require a dashboard login or an appropriate team API token.

## Account roles and testing on Render

| Role | Dashboard access | Data access | Management |
| --- | --- | --- | --- |
| Organizer | Competition overview and all analytics | All teams; all phases; all CSV exports | Create/reset/disable dashboard accounts; manage teams; organizer actions |
| Jury | Competition overview and all analytics | Read-only across teams and phases; CSV exports | Own password only |
| Team | Directly to its own analytics; no team dropdown | Only its assigned team, across testing/baseline/prototype; own exports and tariff graph/download | Own password only; no dashboard telemetry mutations |

Open `/login/` after Render deploys this commit. **Existing organizer staff/superuser usernames and passwords continue to work.** Existing API tokens and telemetry are preserved by the migration. If you need a new organizer account, set DJANGO_SUPERUSER_USERNAME to a new username, DJANGO_SUPERUSER_PASSWORD to a strong password, and optional DJANGO_SUPERUSER_EMAIL in Render Environment, then choose Save, rebuild, and deploy. Seeding only creates missing users; it never resets an existing password.

To test Team-01:

1. Sign in as an organizer and open **Dashboard accounts** in the sidebar (`/accounts/`).
2. Create username `team01`, role **Team**, team **Team-01**, and a strong temporary password. Do not use the API token as the password.
3. Open a private/incognito window, sign in with `team01` and that temporary password, then complete the required password change.
4. You land on Team-01 analytics with only its name and data. Phase, appliance, and time filters remain available. The overview redirects to your analytics; another team's URL or request parameter returns 403.
5. Create a **Jury** account the same way, leaving Team blank. Its dashboard shows every team but offers no account/team administration or API write access.

No role selector is required on the login page. Role and team identity come from server-side account records, not client input. Each team has at most one dashboard account. Passwords use Django password hashing/validation. New dashboard accounts require a password change on first sign-in; organizers can issue a new temporary password or disable an account through Dashboard accounts. Resets invalidate previous dashboard sessions. Disabling a dashboard account does not disable its independent device token. Use token rotation separately when revoking devices.

Django admin stays organizer-only. Passwords and API tokens are never included in team page responses or exports. Team and jury dashboard sessions have read-only data API access, even if they attach a token to a POST. Device clients should use bearer tokens without a dashboard-session cookie; they retain the existing testing/baseline/prototype submission APIs. Session/account mutations require CSRF protection and logout uses POST. First-login password enforcement also applies to the chart API, exports, and Django admin.

The current free Render SQLite setup is for testing: dashboard accounts created at runtime, including their changed passwords, can be lost on a redeploy/restart just like telemetry. No local database or test credentials are uploaded by this commit. For durable accounts and data, plan persistent storage separately.

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

Run `python manage.py createsuperuser` if you do not already have an organizer account, then open http://127.0.0.1:8000/login/. If using the shared workspace virtual environment, replace `python` with `D:\openADR\.venv\Scripts\python.exe`. Set SQLITE_PATH only if you want to use a different SQLite file.

## Request protection

Dashboard pages require an assigned authenticated role. All data APIs require a valid team token or dashboard session. Team-authenticated requests, including CSV exports and free-text notes, remain scoped to their own telemetry. Jury sessions can read all teams, while organizer sessions can additionally manage accounts and perform administrative actions. Team submissions require a team token, and tariff publishing and Django administration require organizer staff access; session mutations require CSRF. Login attempts, API traffic, tariff downloads, telemetry, and exports have separate limits and return Retry-After on HTTP 429. JSON bodies are capped at 64 KB; teams can submit at most 12 appliances per phase. Telemetry replaces an existing 15-minute appliance slot instead of accumulating duplicates. Testing retains at most 20 records per team.

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
