"""Security and workflow tests use Django's isolated test database."""
import json
from datetime import datetime, timedelta
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from .models import Team, TelemetryRecord, TariffInterval, snap_to_15min_interval
from .security import admit
from .tariffs import IST, get_or_create_published_tariffs, generate_dynamic_tariffs, save_published_tariffs


@override_settings(ALLOWED_HOSTS=['testserver'], SECURE_SSL_REDIRECT=False,
                   CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})
class HubTests(TestCase):
    def setUp(self):
        cache.clear()
        self.team = Team.objects.create(team_name='Team-01', token='a-private-token')
        self.other = Team.objects.create(team_name='Team-02', token='another-private-token')
        self.admin = get_user_model().objects.create_user('organizer', password='a-complex-test-password', is_staff=True)
        self.headers = {'HTTP_AUTHORIZATION': f'Bearer {self.team.token}'}

    def post(self, body, path='/api/v3/baseline', **headers):
        return self.client.post(path, json.dumps(body), content_type='application/json', **(headers or self.headers))

    def test_organizer_portal_requires_login(self):
        self.assertEqual(self.client.get('/').status_code, 302)
        self.client.force_login(self.admin)
        for path in ['/', '/analytics/']:
            self.assertEqual(self.client.get(path).status_code, 200)
        dashboard = self.client.get('/')
        self.assertContains(dashboard, 'OpenADR 3.0')
        self.assertContains(dashboard, 'tariff-chart')
        for removed in ['Gridroom', 'gridroom', '/student/', 'publishForm']:
            self.assertNotContains(dashboard, removed)
        self.assertEqual(self.client.get('/student/').status_code, 404)
        analytics = self.client.get('/analytics/').content.decode()
        self.assertLess(analytics.index('value="testing"'), analytics.index('value="baseline"'))
        self.assertLess(analytics.index('value="baseline"'), analytics.index('value="prototype"'))

    def test_anonymous_data_access_blocked(self):
        for path in ['/api/v3/baseline', '/api/v3/testing', '/api/v3/prototype', '/api/v3/auth/verify', '/api/v3/tariffs', '/api/v3/telemetry/intervals', '/download/telemetry.csv', '/download/tariffs.csv']:
            self.assertIn(self.client.get(path).status_code, [401, 403])

    def test_team_cannot_publish_or_read_private_standings(self):
        self.assertEqual(self.client.get('/download/telemetry.csv', **self.headers).status_code, 200)
        self.assertEqual(self.post({}, '/api/v3/tariffs/publish').status_code, 403)
        self.assertEqual(self.client.get('/api/v3/leaderboard', **self.headers).status_code, 403)

    def test_readings_are_team_scoped(self):
        TelemetryRecord.objects.create(team=self.other, appliance='Private_AC', energy=1, timestamp=timezone.now())
        response = self.client.get('/api/v3/baseline', **self.headers)
        self.assertEqual(response.json()['count'], 0)
        self.assertEqual(self.client.get('/api/v3/telemetry/intervals?team=Team-02', **self.headers).status_code, 403)

    def test_batch_and_idempotent_slot(self):
        stamp = timezone.now().replace(second=0, microsecond=0).isoformat()
        payload = {'timestamp': stamp, 'appliances': {'AC': {'energy': 0.25, 'outdoor_temperature': 0}, 'Heater': 0.5}}
        response = self.post(payload)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(TelemetryRecord.objects.count(), 2)
        cache.delete(f'submission:{self.team.pk}')
        payload['appliances']['AC']['energy'] = 0.3
        self.assertEqual(self.post(payload).status_code, 201)
        self.assertEqual(TelemetryRecord.objects.count(), 2)
        self.assertEqual(TelemetryRecord.objects.get(appliance='AC').energy, 0.3)
        self.assertEqual(TelemetryRecord.objects.get(appliance='AC').outdoor_temperature, 0)

    def test_database_uniqueness(self):
        ts = timezone.now()
        TelemetryRecord.objects.create(team=self.team, phase='baseline', appliance='AC', energy=1, timestamp=ts)
        with self.assertRaises(IntegrityError), transaction.atomic():
            TelemetryRecord.objects.create(team=self.team, phase='baseline', appliance='AC', energy=1, timestamp=ts)

    def test_bursts_rejected_in_all_phases(self):
        for phase in ['testing', 'baseline', 'prototype']:
            cache.clear()
            self.assertEqual(self.post({'energy': 0.2}, '/api/v3/' + phase).status_code, 201)
            response = self.post({'energy': 0.2}, '/api/v3/' + phase)
            self.assertEqual(response.status_code, 429)
            self.assertIn('Retry-After', response)

    def test_shared_fixed_window_budget(self):
        with patch('vtn_hub.security.time.time', return_value=120):
            self.assertTrue(admit('team', 'test', 2, 60)[0])
            self.assertTrue(admit('team', 'test', 2, 60)[0])
            self.assertFalse(admit('team', 'test', 2, 60)[0])
        with patch('vtn_hub.security.time.time', return_value=180):
            self.assertTrue(admit('team', 'test', 2, 60)[0])

    def test_bad_payloads_never_write(self):
        payloads = [[], None, {'energy': 'abc'}, {'energy': -1}, {'energy': True}, {'energy': 0.2, 'timestamp': 'bad'},
            {'energy': 0.2, 'timestamp': '2026-10-26T10:00:00'}, {'energy': 0.2, 'appliance': ''},
            {'appliances': []}, {'appliances': [4]}, {'appliances': [{'appliance':'A','energy':1}, {'appliance':'A','energy':2}]},
            {'energy': 1, 'notes': []}, {'power_kw': 1}, {'energy': 1, 'outdoor_temperature': 99},
            {'appliances': {str(i): 1 for i in range(13)}}]
        for payload in payloads:
            with self.subTest(payload=payload):
                self.assertEqual(self.post(payload).status_code, 400)
        self.assertEqual(TelemetryRecord.objects.count(), 0)

    def test_nonfinite_numbers(self):
        for value in ['NaN', 'Infinity', '-Infinity']:
            response = self.client.post('/api/v3/baseline', '{"energy":' + value + '}', content_type='application/json', **self.headers)
            self.assertEqual(response.status_code, 400)

    def test_wrong_content_type(self):
        self.assertEqual(self.client.post('/api/v3/baseline', {'energy': 1}, **self.headers).status_code, 400)

    def test_payload_size(self):
        self.assertEqual(self.post({'notes': 'x' * 70000}).status_code, 413)

    def test_pagination_validation(self):
        for value in ['-1', 'oops', '100000']:
            self.assertEqual(self.client.get('/api/v3/baseline?limit=' + value, **self.headers).status_code, 400)

    def test_login_throttle_and_safe_redirect(self):
        for _ in range(10):
            self.assertEqual(self.client.post('/login/', {'username': 'wrong', 'password': 'wrong'}).status_code, 200)
        self.assertEqual(self.client.post('/login/', {'username': 'wrong', 'password': 'wrong'}).status_code, 429)
        cache.clear()
        response = self.client.post('/login/', {'username': self.admin.username, 'password': 'a-complex-test-password', 'next': '//evil.example'})
        self.assertEqual(response.url, '/')

    def test_logout_is_post_only(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get('/logout/').status_code, 405)
        self.assertRedirects(self.client.post('/logout/'), '/login/')

    def test_dashboard_and_chart_data_are_no_longer_public(self):
        self.assertEqual(self.client.get('/').status_code, 302)
        self.assertEqual(self.client.get('/analytics/').status_code, 302)
        for path in ['/api/v3/telemetry/intervals?team=Team-02', '/download/tariffs.csv', '/download/telemetry.csv']:
            self.assertEqual(self.client.get(path).status_code, 401)
        self.assertEqual(self.client.post('/api/v3/testing', '{"energy":1}', content_type='application/json').status_code, 401)
        self.assertEqual(self.client.post('/api/v3/tariffs/publish', '{}', content_type='application/json').status_code, 403)
        self.assertEqual(self.client.get('/admin/').status_code, 404)

    def test_publish_requires_csrf(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.admin)
        response = client.post('/api/v3/tariffs/publish', '{}', content_type='application/json')
        self.assertEqual(response.status_code, 403)
        client.get('/')
        token = client.cookies['csrftoken'].value
        response = client.post('/api/v3/tariffs/publish', '{"min_price":8,"max_price":12}', content_type='application/json', HTTP_X_CSRFTOKEN=token)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(TariffInterval.objects.count(), 48)

    @override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
    def test_seed_preserves_credentials_without_private_configuration(self):
        self.admin.set_password('preserved-password')
        self.admin.save()
        with patch.dict('os.environ', {'DASHBOARD_PASSWORDS_JSON': ''}):
            call_command('seed_teams', stdout=StringIO())
        self.admin.refresh_from_db()
        self.team.refresh_from_db()
        self.assertTrue(self.admin.check_password('preserved-password'))
        self.assertEqual(self.team.token, 'a-private-token')
        self.assertGreater(len(Team.objects.get(team_name='Team-03').token), 30)

    def test_tariffs_rollover_preserves_announced_prices(self):
        now = datetime.now(IST)
        class Yesterday(datetime):
            @classmethod
            def now(cls, tz=None):
                return now - timedelta(days=1)
        with patch('vtn_hub.tariffs.datetime', Yesterday):
            points, seed = generate_dynamic_tariffs(seed=42)
        save_published_tariffs(points, seed)
        expected = [p['price_per_kwh'] for p in points[24:]]
        fresh, _ = get_or_create_published_tariffs()
        self.assertEqual(len(fresh), 48)
        self.assertEqual([p['price_per_kwh'] for p in fresh[:24]], expected)
        self.assertEqual(datetime.fromisoformat(fresh[0]['start_time']).date(), now.date())

    def test_analytics_zero_temperature_and_missing_data(self):
        now = timezone.now()
        for app, energy, temp in [('AC', 0.25, 0), ('Heater', 0.5, None)]:
            TelemetryRecord.objects.create(team=self.team, appliance=app, phase='baseline', energy=energy, outdoor_temperature=temp, timestamp=now)
        data = self.client.get('/api/v3/telemetry/intervals', **self.headers).json()
        self.assertEqual(data['stats']['sum_energy_kwh'], 0.75)
        self.assertEqual(data['stats']['avg_temp_c'], 0)
        self.assertEqual(data['stats']['interval_count'], 1)

    def test_csv_injection_is_escaped_and_phase_included(self):
        TelemetryRecord.objects.create(team=self.team, appliance='=CMD()', notes=' @danger', energy=1, timestamp=timezone.now())
        self.client.force_login(self.admin)
        response = self.client.get('/download/telemetry.csv')
        content = b''.join(response.streaming_content).decode()
        self.assertIn('phase', content)
        self.assertIn("'=CMD()", content)
        self.assertIn("' @danger", content)

    def test_team_token_works_while_organizer_logged_in(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.post({'energy': 0.25}, '/api/v3/testing').status_code, 201)

    def test_response_security_headers(self):
        response = self.client.get('/api/v3/auth/verify', **self.headers)
        self.assertIn('no-store', response['Cache-Control'])
        self.assertIn("script-src 'self'", response['Content-Security-Policy'])
        self.assertEqual(response['X-Frame-Options'], 'DENY')

    def test_minute_hour_day_rollover(self):
        for dt, expected in [
            (datetime(2026,10,26,10,7,tzinfo=IST), datetime(2026,10,26,10,15,tzinfo=IST)),
            (datetime(2026,10,26,23,59,tzinfo=IST), datetime(2026,10,27,0,0,tzinfo=IST)),
            (datetime(2026,10,26,10,15,tzinfo=IST), datetime(2026,10,26,10,15,tzinfo=IST))]:
            self.assertEqual(snap_to_15min_interval(dt), expected)

    def test_appliance_contract_and_team_status(self):
        self.post({'appliance': 'AC', 'energy': 0.25})
        data = self.client.get('/api/v3/appliances', **self.headers).json()
        self.assertEqual(data['appliances'][0]['appliance'], 'AC')
        self.assertEqual(data['appliances'][0]['energy'], 0.25)
        status = self.client.get('/api/v3/team/status', **self.headers).json()
        self.assertEqual(status['team_name'], self.team.team_name)
        self.assertEqual(status['counts']['baseline'], 1)

    def test_cache_outage_fails_closed(self):
        with patch('vtn_hub.security.cache.add', side_effect=ConnectionError('offline')):
            self.assertEqual(self.post({'energy': 1}).status_code, 503)
        self.assertFalse(TelemetryRecord.objects.exists())

    def test_credential_audit(self):
        from django.core.management.base import CommandError
        self.team.token = 'KEY-TEAM01-A7F9'
        self.team.save()
        with self.assertRaises(CommandError):
            call_command('audit_credentials', stdout=StringIO())
        call_command('rotate_team_token', self.team.team_name, stdout=StringIO())
        self.team.refresh_from_db()
        self.assertNotEqual(self.team.token, 'KEY-TEAM01-A7F9')
        call_command('audit_credentials', stdout=StringIO())

    def test_testing_retention(self):
        now = timezone.now()
        for index in range(25):
            cache.clear()  # Exercise retention across reporting windows, independently of burst budgets.
            response = self.post({'energy': 0.1, 'timestamp': (now-timedelta(minutes=15*index)).isoformat()}, '/api/v3/testing')
            self.assertEqual(response.status_code, 201)
        self.assertEqual(TelemetryRecord.objects.filter(phase='testing').count(), 20)

    def test_appliance_cap_across_requests(self):
        for i in range(12):
            TelemetryRecord.objects.create(team=self.team, appliance=f'AC-{i}', energy=1, timestamp=timezone.now())
        response = self.post({'energy': 1, 'appliance': 'extra'})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(TelemetryRecord.objects.count(), 12)

    def test_invalid_tariff_bounds(self):
        self.client.force_login(self.admin)
        for body in [{'min_price': 20, 'max_price': 8}, {'min_price': -1}, {'seed': 3.5}, {'tariffs': []}]:
            self.assertEqual(self.post(body, '/api/v3/tariffs/publish', HTTP_X_REQUESTED_WITH='test').status_code, 400)

    def test_microseconds_snap_forward(self):
        stamp = datetime(2026, 10, 26, 10, 15, 0, 1, tzinfo=IST)
        self.assertEqual(snap_to_15min_interval(stamp), datetime(2026, 10, 26, 10, 30, tzinfo=IST))


from unittest import TestCase as UnitTestCase
from unittest.mock import Mock
from team_kit.openadr3_client import TeamVen


class ClientTests(UnitTestCase):
    def test_authentication_uses_header_and_tariffs_are_cached(self):
        response = Mock(status_code=200, ok=True)
        response.json.return_value = {'tariffs': [{'price_per_kwh': 10}]}
        with patch('team_kit.openadr3_client.requests.get', return_value=response) as get:
            client = TeamVen('secret-token', 'https://example.test')
            client.verify_token()
            client.get_tariffs(save_local=False)
            client.get_tariffs(save_local=False)
            self.assertEqual(get.call_count, 2)
            for call in get.call_args_list:
                self.assertNotIn('secret-token', call.args[0])
                self.assertEqual(call.kwargs['headers']['Authorization'], 'Bearer secret-token')

    def test_post_does_not_retry_and_surfaces_server_cooldown(self):
        response = Mock(status_code=429, headers={'Retry-After': '17'})
        with patch('team_kit.openadr3_client.requests.post', return_value=response) as post:
            with self.assertRaisesRegex(RuntimeError, '17 seconds'):
                TeamVen('secret', 'https://example.test').send_testing(energy=1)
            self.assertEqual(post.call_count, 1)
            self.assertNotIn('secret', post.call_args.args[0])

    def test_measurement_timestamp_and_standard_library_fallback(self):
        import urllib.error
        stamp = '2026-10-26T10:15:00+05:30'
        client = TeamVen('secret', 'https://example.test')
        with patch.object(client, '_http_post', return_value={}) as post:
            client.report_telemetry(energy=0.25, timestamp=stamp)
            self.assertEqual(post.call_args.args[1]['timestamp'], stamp)
        failure = urllib.error.HTTPError('https://example.test', 429, 'limited', {'Retry-After':'23'}, None)
        with patch('team_kit.openadr3_client.HAS_REQUESTS', False), patch('urllib.request.urlopen', side_effect=failure) as open_url:
            with self.assertRaisesRegex(RuntimeError, '23 seconds'):
                client.verify_token()
            self.assertEqual(open_url.call_count, 1)
            self.assertNotIn('secret', open_url.call_args.args[0].full_url)


from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class LegacyMigrationTests(TransactionTestCase):
    def test_existing_duplicate_slots_keep_latest_saved_reading(self):
        from django.db import connection
        previous = ('vtn_hub', '0005_team_last_tariff_fetched_at_telemetryrecord_phase_and_more')
        current = ('vtn_hub', '0009_remove_dashboardaccount_must_change_password')
        executor = MigrationExecutor(connection)
        executor.migrate([previous])
        try:
            apps = executor.loader.project_state([previous]).apps
            TeamBefore = apps.get_model('vtn_hub', 'Team')
            RecordBefore = apps.get_model('vtn_hub', 'TelemetryRecord')
            team = TeamBefore.objects.create(team_name='Migrated', token='original-token')
            stamp = timezone.now()
            for energy in [1, 2]:
                RecordBefore.objects.create(team=team, appliance='AC', phase='baseline', energy=energy,
                                            timestamp=stamp, interval_timestamp=stamp)
            executor = MigrationExecutor(connection)
            executor.migrate([current])
            self.assertEqual(TelemetryRecord.objects.count(), 1)
            self.assertEqual(TelemetryRecord.objects.get().energy, 2)
            self.assertEqual(Team.objects.get().token, 'original-token')
        finally:
            MigrationExecutor(connection).migrate([current])

class RenderTestConfigTests(UnitTestCase):
    def test_existing_multiworker_start_command_uses_one_worker_without_redis(self):
        import runpy
        from pathlib import Path
        config = runpy.run_path(str(Path(__file__).resolve().parent.parent / 'gunicorn.conf.py'))
        server = Mock(num_workers=2)
        with patch.dict('os.environ', {}, clear=True):
            config['on_starting'](server)
        self.assertEqual(server.num_workers, 1)
        server.cfg.set.assert_called_once_with('workers', 1)

    def test_redis_deployment_can_keep_requested_worker_count(self):
        import runpy
        from pathlib import Path
        config = runpy.run_path(str(Path(__file__).resolve().parent.parent / 'gunicorn.conf.py'))
        server = Mock(num_workers=2)
        with patch.dict('os.environ', {'REDIS_URL': 'redis://localhost:6379/0'}):
            config['on_starting'](server)
        self.assertEqual(server.num_workers, 2)
        server.cfg.set.assert_not_called()

@override_settings(ALLOWED_HOSTS=['testserver'], SECURE_SSL_REDIRECT=False,
                   PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'],
                   CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})
class DashboardRoleTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        from .models import DashboardAccount
        cls.team1 = Team.objects.create(team_name='Team-01', token='device-token-one')
        cls.team2 = Team.objects.create(team_name='Team-02', token='device-token-two')
        cls.staff = get_user_model().objects.create_user('organizer', password='Initial!TestPassword72', is_staff=True)
        cls.jury = get_user_model().objects.create_user('jury', password='Initial!TestPassword72')
        cls.student = get_user_model().objects.create_user('team01', password='Initial!TestPassword72')
        cls.unassigned = get_user_model().objects.create_user('unassigned', password='Initial!TestPassword72')
        DashboardAccount.objects.create(user=cls.jury, role='jury')
        DashboardAccount.objects.create(user=cls.student, role='team', team=cls.team1)
        for team, app in [(cls.team1, 'Own_AC'), (cls.team2, 'Other_AC')]:
            for phase in ['testing', 'baseline', 'prototype']:
                TelemetryRecord.objects.create(team=team, phase=phase, appliance=app, energy=0.25,
                                               notes='Internal '+team.team_name, timestamp=timezone.now())

    def setUp(self):
        cache.clear()

    def test_role_specific_login_destinations(self):
        for username, destination in [('organizer', '/'), ('jury', '/'), ('team01', '/analytics/')]:
            client = Client()
            response = client.post('/login/', {'username': username, 'password': 'Initial!TestPassword72', 'next': '//evil.example'})
            self.assertEqual(response.status_code, 302)
            self.assertEqual(response.url, destination)

    def test_team_page_contains_only_own_team_and_fixed_selector(self):
        self.client.force_login(self.student)
        self.assertRedirects(self.client.get('/'), '/analytics/')
        page = self.client.get('/analytics/')
        self.assertContains(page, 'Team-01')
        self.assertContains(page, 'type="hidden" value="Team-01"')
        for forbidden in ['Team-02', '/accounts/', '/admin/', 'Other_AC', self.team1.token, self.team2.token]:
            self.assertNotContains(page, forbidden)
        self.assertEqual(self.client.get('/analytics/?team=Team-02').status_code, 403)

    def test_team_reads_scoped_across_all_aliases(self):
        self.client.force_login(self.student)
        paths = ['/api/v3/testing', '/api/v3/testing-data', '/api/v3/testing/data',
                 '/api/v3/baseline', '/api/v3/baseline-data', '/api/v3/baseline/data', '/api/v3/reports',
                 '/api/v3/prototype', '/api/v3/prototype-data', '/api/v3/prototype/data', '/api/v3/sandbox/test']
        for path in paths:
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200)
            self.assertEqual({r['team_name'] for r in response.json()['data']}, {'Team-01'})
            self.assertEqual(self.client.get(path+'?team=Team-02').status_code, 403)
        self.assertEqual(self.client.get('/api/v3/appliances').json()['appliances'][0]['appliance'], 'Own_AC')
        self.assertEqual(self.client.get('/api/v3/team/status').json()['team_name'], 'Team-01')

    def test_team_cannot_change_chart_or_download_scope(self):
        self.client.force_login(self.student)
        for path in ['/api/v3/telemetry/intervals', '/api/v3/baseline/intervals', '/download/telemetry.csv', '/download/baseline-data.csv']:
            self.assertEqual(self.client.get(path+'?team=Team-02').status_code, 403)
        data = self.client.get('/api/v3/telemetry/intervals').json()
        self.assertEqual(data['team_name'], 'Team-01')
        self.assertNotIn('Other_AC', data['appliances'])
        response = self.client.get('/download/telemetry.csv')
        content = b''.join(response.streaming_content).decode()
        self.assertIn('Own_AC', content)
        self.assertNotIn('Other_AC', content)
        self.assertNotIn(self.team1.token, content)
        self.assertEqual(self.client.get('/api/v3/baseline', HTTP_AUTHORIZATION='Bearer '+self.team2.token).status_code, 403)

    def test_team_and_jury_sessions_are_read_only(self):
        count = TelemetryRecord.objects.count()
        for user in [self.student, self.jury]:
            self.client.force_login(user)
            for phase in ['testing', 'baseline', 'prototype']:
                response = self.client.post('/api/v3/'+phase, '{"energy":1}', content_type='application/json', HTTP_AUTHORIZATION='Bearer '+self.team1.token)
                self.assertEqual(response.status_code, 403)
            self.assertEqual(self.client.post('/api/v3/tariffs/publish', '{}', content_type='application/json').status_code, 403)
            self.assertEqual(self.client.get('/accounts/').status_code, 404)
            self.assertEqual(self.client.post('/accounts/', {'action':'create','username':'elevated','role':'organizer'}).status_code, 404)
            self.assertEqual(self.client.get('/admin/').status_code, 404)
        self.assertEqual(TelemetryRecord.objects.count(), count)

    def test_jury_can_review_all_teams_and_export_without_edit_controls(self):
        self.client.force_login(self.jury)
        overview = self.client.get('/')
        self.assertContains(overview, 'Team-01')
        self.assertContains(overview, 'Team-02')
        self.assertNotContains(overview, '/accounts/')
        self.assertNotContains(overview, '/admin/')
        self.assertEqual(self.client.get('/api/v3/telemetry/intervals?team=Team-02').json()['team_name'], 'Team-02')
        response = self.client.get('/download/telemetry.csv')
        content = b''.join(response.streaming_content).decode()
        self.assertIn('Own_AC', content)
        self.assertIn('Other_AC', content)

    def test_anonymous_and_unassigned_users_cannot_read_any_dashboard_data(self):
        for user in [None, self.unassigned]:
            client = Client()
            if user:
                client.force_login(user)
            for path in ['/api/v3/telemetry/intervals', '/download/telemetry.csv', '/download/tariffs.csv', '/api/v3/testing']:
                self.assertEqual(client.get(path).status_code, 401)
            self.assertIn(client.get('/analytics/').status_code, [302, 403])
        response = self.client.post('/login/', {'username': 'unassigned', 'password': 'Initial!TestPassword72'})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('_auth_user_id', self.client.session)





    def test_csrf_required_for_login_and_account_management(self):
        client = Client(enforce_csrf_checks=True)
        self.assertEqual(client.post('/login/', {'username':'team01','password':'Initial!TestPassword72'}).status_code, 403)
        client.force_login(self.staff)
        self.assertEqual(client.post('/accounts/', {'action':'create'}).status_code, 404)
        self.assertEqual(client.post('/logout/').status_code, 403)

    def test_model_rejects_staff_team_and_missing_team_roles(self):
        from .models import DashboardAccount
        from django.core.exceptions import ValidationError
        with self.assertRaises(ValidationError):
            DashboardAccount(user=self.staff, role='team', team=self.team2).full_clean()
        with self.assertRaises(ValidationError):
            DashboardAccount(user=self.unassigned, role='team').full_clean()


@override_settings(ALLOWED_HOSTS=['testserver'], SECURE_SSL_REDIRECT=False,
                   PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'],
                   CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})
class OwnerManagedLoginTests(TestCase):
    def setUp(self):
        cache.clear()
        import os
        # Synthetic test fixtures, not the owner's actual deployment credentials.
        self.fixture_passwords = {'admin':'Fixture!AdminPassword927', 'organizer':'Fixture!AdminPassword927',
                                  'jury':'Fixture!AdminPassword927', 'team01':'Fixture!TeamOnePassword927',
                                  'team02':'Fixture!TeamTwoPassword927'}
        environment = patch.dict(os.environ, {'DASHBOARD_PASSWORDS_JSON': json.dumps(self.fixture_passwords)})
        environment.start()
        self.addCleanup(environment.stop)
        call_command('seed_teams', count=2, stdout=StringIO())

    def test_owner_assigned_credentials_login_without_password_change(self):
        cases = [(name, password, '/analytics/' if name.startswith('team') else '/') for name, password in self.fixture_passwords.items()]
        for username, password, target in cases:
            client = Client()
            response = client.post('/login/', {'username':username, 'password':password})
            self.assertEqual(response.status_code, 302)
            self.assertEqual(response.url, target)
            page = client.get(target)
            self.assertEqual(page.status_code, 200)
            for forbidden in ['/accounts/', '/password/', '/admin/', 'Change password', 'Dashboard accounts']:
                self.assertNotContains(page, forbidden)
        page = Client().get('/login/')
        for password in self.fixture_passwords.values():
            self.assertNotContains(page, password)

    def test_no_web_credential_routes_for_any_role(self):
        for username in ['admin','organizer','jury','team01']:
            client = Client()
            client.force_login(get_user_model().objects.get(username=username))
            for path in ['/accounts/', '/password/change/', '/password/reset/', '/admin/', '/admin/auth/user/']:
                self.assertEqual(client.get(path).status_code, 404)
                self.assertEqual(client.post(path, {'action':'reset','temporary_password':'attacker-password'}).status_code, 404)

    def test_only_server_environment_can_override_assigned_passwords(self):
        import os
        team = Team.objects.get(team_name='Team-01')
        token = team.token
        with patch.dict(os.environ, {'DASHBOARD_PASSWORDS_JSON': json.dumps({'team01':'OwnerOnly!Password938'})}):
            call_command('seed_dashboard_logins', count=2, stdout=StringIO())
        user = get_user_model().objects.get(username='team01')
        self.assertTrue(user.check_password('OwnerOnly!Password938'))
        self.assertFalse(user.check_password(self.fixture_passwords['team01']))
        team.refresh_from_db()
        self.assertEqual(team.token, token)
        client = Client()
        response = client.post('/login/', {'username':'team01','password':'OwnerOnly!Password938', 'password1':'InjectedPassword823'})
        self.assertEqual(response.url, '/analytics/')
        user.refresh_from_db()
        self.assertTrue(user.check_password('OwnerOnly!Password938'))

    def test_seed_replaces_previous_team_alias_without_changing_data(self):
        from .models import DashboardAccount
        team = Team.objects.get(team_name='Team-01')
        DashboardAccount.objects.filter(team=team).delete()
        old = get_user_model().objects.create_user('old-team-login', password='Old!Password936')
        DashboardAccount.objects.create(user=old, role='team', team=team)
        TelemetryRecord.objects.create(team=team, appliance='AC', energy=0.25, timestamp=timezone.now())
        call_command('seed_dashboard_logins', count=2, stdout=StringIO())
        self.assertEqual(DashboardAccount.objects.get(team=team).user.username, 'team01')
        old.refresh_from_db()
        self.assertFalse(old.is_active)
        self.assertEqual(TelemetryRecord.objects.filter(team=team).count(), 1)
