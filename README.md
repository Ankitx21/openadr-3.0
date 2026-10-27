# OpenADR 3.0

Organizer/jury dashboard and isolated team analytics. The competition owner assigns dashboard credentials through server configuration. There are no web pages for creating accounts, listing dashboard accounts, resetting/changing passwords, or accessing Django admin. These URLs return 404 even to an organizer or superuser.

## Predefined login IDs

The Render build already runs `python manage.py seed_teams`, which also reads the private `DASHBOARD_PASSWORDS_JSON` environment variable to provision dashboard logins. No passwords are embedded in application source.

| Login ID | Access |
| --- | --- |
| `admin` | Organizer: all teams and phases |
| `organizer` | Organizer: all teams and phases |
| `jury` | Read-only across all teams and phases |
| `team01` | Team-01 only |
| `team02` | Team-02 only |
| ... | ... |
| `team20` | Team-20 only |

Open `/login/`, enter the assigned ID and privately assigned password, and proceed directly to the dashboard. No first-login password change is required. The login page does not list passwords or provide reset links. Team logins land on their own analytics without a team selector. Each team has at most one dashboard account; old UI-created aliases for a predefined team are retired when the owner provisions its fixed login.

Dashboard passwords are unrelated to device API tokens and are stored as Django hashes in SQLite. Team/jury dashboard sessions cannot submit telemetry or modify tariffs. Devices continue using their independent bearer tokens without a dashboard-session cookie. API tokens and telemetry are not modified by dashboard provisioning.

## Owner-only password assignment

Only someone with access to the Render environment or service shell can change the assignment. There is no credential-management HTTP endpoint, including under `/admin/`.

To assign or replace a password, add a Render environment variable named `DASHBOARD_PASSWORDS_JSON`. Its value is a JSON object mapping login IDs to passwords, for example:

```json
{"team01":"replace-with-your-private-password"}
```

Select **Save, rebuild, and deploy**. Only listed logins are created or updated. Without the variable, no credentials are created or changed and existing users keep their passwords. Values must be 8-128 characters and must differ from the team's API token. Do not commit the actual variable value to GitHub or share it in application logs. Passwords are never printed by the seed command.

The private environment variable is the source of truth: each build reapplies only the assignments it contains. Changing an assigned password invalidates the user's existing sessions. If you use `changepassword` in a private shell, also update the environment override so a later build does not revert it. Historical DJANGO_SUPERUSER_* bootstrap variables no longer control these predefined credentials.

Owner commands:

```sh
python manage.py seed_teams
python manage.py seed_dashboard_logins
python manage.py rotate_team_token Team-01
```

The first creates missing teams and applies logins; the second applies logins to existing teams only. Token rotation is a separate, deliberate owner action. `audit_credentials` remains a manual diagnostic for weak test credentials; it is not part of deployment.

## Existing Render SQLite test deployment

The repository itself is the service root: leave Root Directory blank. No PostgreSQL or Redis service is required. Keep SECRET_KEY and DEBUG=False. The Render hostname is allowed automatically; custom hostnames go in ALLOWED_HOSTS.

Build:

```sh
pip install -r requirements.txt && python manage.py collectstatic --noinput && python manage.py migrate && python manage.py seed_teams
```

Start:

```sh
gunicorn openadr_django.wsgi:application --bind 0.0.0.0:$PORT --workers 1 --threads 4 --timeout 60
```

`gunicorn.conf.py` also selects one worker when Redis is absent, including when an existing Render start command still requests two workers. Threads share in-memory request limits, which reset on worker restart. Use one service instance for this test setup. REDIS_URL is optional for future shared-cache deployments.

The blueprint uses the existing free plan and adds no database/cache services. The current SQLite filesystem is temporary: runtime telemetry can disappear after a restart/redeploy. Accounts listed in the private environment variable are recreated from the owner-controlled setup on each build. No local database or telemetry exports are uploaded by a source push. Durable storage can be configured later. See [Render filesystem documentation](https://render.com/docs/disks).

## Local run

From the repository directory, with your Python environment activated:

```powershell
$env:DEBUG = 'True'
Remove-Item Env:REDIS_URL -ErrorAction SilentlyContinue
python -m pip install -r requirements.txt
python manage.py migrate
python manage.py seed_teams
python manage.py runserver
```

Open http://127.0.0.1:8000/login/ and use a privately assigned login. Configure DASHBOARD_PASSWORDS_JSON locally first if provisioning new accounts. With the shared workspace environment, replace `python` with `D:\openADR\.venv\Scripts\python.exe`.

## Data protection and tariffs

Pages require a dashboard login, and APIs require a dashboard session or team token. Role and team identity come from the server. Changing a URL, query parameter, or token in a team dashboard session cannot expose another team's data. Team exports include only the assigned team; jury/organizer exports can include all teams. CSV text values are escaped against spreadsheet formula injection. Login, requests, downloads, and submissions are rate limited. JSON bodies are capped at 64 KB. Teams have at most 12 appliance names per phase, with one saved reading per appliance/phase/15-minute slot; testing retains 20 rows per team. Analytics queries cover at most 31 days, and exports stream in chunks.

The 48-hour tariff graph and downloads remain visible after login. There are no dashboard publishing controls. Publish using a trusted service shell or future scheduled job:

```sh
python manage.py publish_tariffs --min-price 8 --max-price 20
```

`publish_dynamic_tariffs.py` calls the same command. This update does not install a scheduler. The first tariff request after midnight IST rolls prices forward, preserving already announced tomorrow prices; manual publishing replaces all 48 prices.

## Verification

```sh
python manage.py collectstatic --noinput
python manage.py test vtn_hub
python manage.py makemigrations --check --dry-run
```

Tests use an isolated database and cover scoped reads, readonly jury/team sessions, private owner-assigned credentials, and removal of all credential-management web routes. Migration 0009 removes the obsolete forced-password-change flag. The existing team identifiers, device tokens, and telemetry are preserved. Browser visual testing needs an available browser connection.
