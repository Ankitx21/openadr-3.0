from __future__ import annotations

import csv
import json
import math
from datetime import datetime, timedelta
from functools import wraps
from io import StringIO

from django.core.cache import cache
from django.db import transaction
from django.db.models import Count, Max, Q
from django.http import JsonResponse, HttpResponse, StreamingHttpResponse
from django.shortcuts import render, redirect
from django.contrib.auth import login, logout
from django.contrib.auth.forms import AuthenticationForm
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from .models import Team, TelemetryRecord, snap_to_15min_interval
from .tariffs import IST, get_or_create_published_tariffs, generate_dynamic_tariffs, save_published_tariffs
from .security import error, throttle
from .access import request_access, access_for

PHASES = ('testing', 'baseline', 'prototype')
MAX_APPLIANCES = 12


def extract_token(request):
    header = request.headers.get('Authorization', '')
    if header:
        parts = header.split()
        return parts[1] if len(parts) == 2 and parts[0].lower() == 'bearer' else header.strip()
    return request.GET.get('token') or request.POST.get('token')


def authenticate_team(request):
    token = extract_token(request)
    if not token or len(token) > 128:
        return None
    return Team.objects.filter(token=token).first()


def organizer(user):
    return access_for(user)[0] == 'organizer'


def api_access(organizer_only=False):
    def decorator(view):
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            request.team = None
            role, linked_team, account = request_access(request)
            if role in ('team', 'jury') and request.method not in ('GET', 'HEAD'):
                return error('Dashboard accounts have read-only API access. Devices must use a team API token without a dashboard session.', 403)
            if organizer_only:
                if role != 'organizer' and not (role == 'jury' and request.method == 'GET'):
                    return error('Organizer access required.', 403)
            token = extract_token(request)
            if token and not organizer_only:
                request.team = authenticate_team(request)
                if not request.team:
                    return error('Invalid or missing team token.', 401)
                if linked_team and request.team.pk != linked_team.pk:
                    return error('You can only access your own team.', 403)
                identity = f'team:{request.team.pk}'
            elif role in ('organizer', 'jury', 'team'):
                request.team = linked_team
                identity = f'user:{request.user.pk}'
            else:
                return error('Sign in or provide a valid team token.', 401)
            if request.team and request.GET.get('team') not in (None, '', request.team.team_name):
                return error('You can only access your own team.', 403)
            blocked = throttle(identity, 'api', [(120, 60), (2000, 3600)])
            if blocked is not None:
                return blocked
            return view(request, *args, **kwargs)
        return wrapped
    return decorator


def portal(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('/login/?next=' + request.path)
        role, team, account = request_access(request)
        if not role:
            return HttpResponse('No dashboard role assigned. Contact the organizer.', status=403)
        blocked = throttle(request.user.pk, 'portal', [(30, 60)])
        if blocked is not None:
            return blocked
        return view(request, *args, **kwargs)
    return wrapped


def login_view(request):
    role, team, account = request_access(request)
    if role:
        return redirect('analytics' if role == 'team' else 'dashboard')
    form = AuthenticationForm(request, data=request.POST if request.method == 'POST' else None)
    form.fields['username'].widget.attrs['placeholder'] = 'team01 / organizer / jury'
    if request.method == 'POST' and form.is_valid():
        user = form.get_user()
        role, team, account = access_for(user)
        if role:
            login(request, user)
            next_url = request.POST.get('next') or '/'
            if role == 'team':
                next_url = '/analytics/'
            if not url_has_allowed_host_and_scheme(next_url, {request.get_host()}, require_https=request.is_secure()):
                next_url = '/'
            return redirect(next_url)
        form.add_error(None, 'This account has no dashboard access. Contact the organizer.')
    return render(request, 'vtn_app/login.html', {'form': form, 'next': request.GET.get('next', '/')})


@require_POST
def logout_view(request):
    logout(request)
    return redirect('login')


def touch_team(team, tariff=False):
    now = timezone.now()
    try:
        if cache.add(f'touch:{team.pk}', True, 60):
            Team.objects.filter(pk=team.pk, first_connected_at__isnull=True).update(first_connected_at=now)
            Team.objects.filter(pk=team.pk).update(last_connected_at=now)
        if tariff and cache.add(f'tariff-touch:{team.pk}', True, 60):
            Team.objects.filter(pk=team.pk).update(last_tariff_fetched_at=now)
    except Exception:
        pass  # Receipt tracking must not turn a committed report into a failure.


@require_GET
@portal
def dashboard_view(request):
    if request_access(request)[0] == 'team':
        return redirect('analytics')
    teams = cache.get('dashboard-team-summary')
    if teams is None:
        teams = list(Team.objects.annotate(
            testing_count=Count('telemetry_records', filter=Q(telemetry_records__phase='testing')),
            baseline_count=Count('telemetry_records', filter=Q(telemetry_records__phase='baseline')),
            prototype_count=Count('telemetry_records', filter=Q(telemetry_records__phase='prototype')),
            appliance_count=Count('telemetry_records__appliance', distinct=True),
            last_report=Max('telemetry_records__timestamp'),
        ).order_by('team_name'))
        cache.set('dashboard-team-summary', teams, 15)
    now = timezone.now()
    for team in teams:
        team.online = bool(team.last_connected_at and now - team.last_connected_at < timedelta(minutes=30))
    tariffs, seed = get_or_create_published_tariffs()
    current = next((t for t in tariffs if datetime.fromisoformat(t['start_time']) <= now < datetime.fromisoformat(t['start_time']) + timedelta(hours=1)), None)
    return render(request, 'vtn_app/dashboard.html', {
        'teams': teams, 'active_teams': sum(t.online for t in teams),
        'baseline_total': sum(t.baseline_count for t in teams),
        'prototype_total': sum(t.prototype_count for t in teams),
        'recent_telemetry': TelemetryRecord.objects.select_related('team').order_by('-id')[:12],
        'tariffs': tariffs, 'current_tariff': current, 'now': now,
        'awaiting_teams': sum(not t.testing_count for t in teams),
    })


@require_GET
@portal
def analytics_view(request):
    role, team, _ = request_access(request)
    if team and request.GET.get('team') not in (None, '', team.team_name):
        return HttpResponse('You can only access your own team.', status=403)
    teams = Team.objects.all() if role in ('organizer', 'jury') else Team.objects.filter(pk=team.pk)
    tariffs, _ = get_or_create_published_tariffs() if team else ([], None)
    return render(request, 'vtn_app/analytics.html', {'teams': teams, 'tariffs': tariffs, 'selected_team_name': team.team_name if team else request.GET.get('team', '')})


@require_GET
@api_access()
def api_verify_token(request):
    if request.team:
        touch_team(request.team)
    return JsonResponse({'status': 'success', 'authenticated': True, 'team_name': request.team.team_name if request.team else (request_access(request)[0] or 'organizer').title()})


@require_GET
@api_access()
def api_tariffs_json(request):
    identity = request.team.pk if request.team else f'admin:{request.user.pk}'
    blocked = throttle(identity, 'tariffs', [(1, 5), (6, 60), (30, 3600)])
    if blocked is not None:
        return blocked
    tariffs, seed = get_or_create_published_tariffs()
    if request.team:
        touch_team(request.team, tariff=True)
    prices = [t['price_per_kwh'] for t in tariffs]
    return JsonResponse({'status': 'success', 'currency': 'INR', 'horizon_days': 2,
        'total_intervals': len(tariffs), 'seed_used': seed, 'min_price': min(prices), 'max_price': max(prices),
        'tariffs': tariffs, 'recommended_refresh_seconds': 3600})


def parse_body(request):
    if request.content_type != 'application/json':
        raise ValueError('Use Content-Type: application/json.')
    try:
        body = json.loads(request.body, parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Numbers must be finite.')))
    except (json.JSONDecodeError, UnicodeDecodeError, RecursionError):
        raise ValueError('Invalid JSON body.')
    if not isinstance(body, dict):
        raise ValueError('JSON body must be an object.')
    return body


def number(value, name, low, high, optional=False):
    if value is None and optional:
        return None
    if isinstance(value, bool):
        raise ValueError(f'{name} must be a number.')
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError(f'{name} must be a number.')
    if not math.isfinite(result) or not low <= result <= high:
        raise ValueError(f'{name} must be between {low} and {high}.')
    return result


@csrf_exempt
@require_POST
@api_access(organizer_only=True)
def api_publish_tariffs(request):
    # Session-authenticated mutations still require CSRF despite token API exemptions.
    from django.middleware.csrf import CsrfViewMiddleware
    rejection = CsrfViewMiddleware(lambda r: None).process_view(request, lambda r: None, (), {})
    if rejection:
        return rejection
    try:
        body = parse_body(request)
        min_p = number(body.get('min_price', 8), 'Minimum price', 0, 1000)
        max_p = number(body.get('max_price', 20), 'Maximum price', min_p, 1000)
        seed = body.get('seed')
        if seed is not None and (isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed <= 2147483647):
            raise ValueError('Seed must be an integer between 0 and 2147483647.')
        # Custom imports need a separate validated workflow; never accept unvalidated tariff objects.
        if 'tariffs' in body:
            raise ValueError('Use price bounds and an optional seed to publish tariffs.')
        points, seed = generate_dynamic_tariffs(seed, min_p, max_p)
        blocked = throttle(request.user.pk, 'publish', [(1, 60)])
        if blocked is not None:
            return blocked
        if not cache.add('tariff-rollover-lock', True, 15):
            return error('Another tariff update is in progress. Retry shortly.', 503, 5)
        try:
            save_published_tariffs(points, seed)
        finally:
            cache.delete('tariff-rollover-lock')
        return JsonResponse({'status': 'success', 'message': '48 hourly tariffs published.', 'tariffs': points, 'seed_used': seed}, status=201)
    except ValueError as exc:
        return error(str(exc))


def validate_submission(body):
    notes = body.get('notes', '')
    if not isinstance(notes, str) or len(notes) > 1000:
        raise ValueError('Notes must be text of at most 1000 characters.')
    raw_ts = body.get('timestamp') or body.get('time')
    if raw_ts:
        try:
            ts = datetime.fromisoformat(str(raw_ts).replace('Z', '+00:00'))
        except ValueError:
            raise ValueError('Timestamp must be ISO 8601 with a timezone, for example 2026-10-26T10:15:00+05:30.')
        if timezone.is_naive(ts):
            raise ValueError('Timestamp must include a timezone offset.')
        if ts > timezone.now() + timedelta(minutes=5) or ts < timezone.now() - timedelta(days=31):
            raise ValueError('Timestamp must be within the last 31 days and no more than 5 minutes in the future.')
    else:
        ts = timezone.now()
    outside = number(body.get('outdoor_temperature'), 'Outdoor temperature', -60, 70, True)
    apps = body.get('appliances')
    if apps is None:
        apps = [body]
    elif isinstance(apps, dict):
        apps = [dict(value, appliance=name) if isinstance(value, dict) else {'appliance': name, 'energy': value} for name, value in apps.items()]
    if not isinstance(apps, list) or not 1 <= len(apps) <= MAX_APPLIANCES:
        raise ValueError(f'Send between 1 and {MAX_APPLIANCES} appliances per request.')
    parsed = []
    names = set()
    for item in apps:
        if not isinstance(item, dict):
            raise ValueError('Each appliance must be an object.')
        name = item.get('appliance', 'Generic_Load')
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 128 or any(ord(c) < 32 for c in name):
            raise ValueError('Appliance names must contain 1–128 printable characters.')
        name = name.strip()
        if name in names:
            raise ValueError('Appliance names must be unique within a request.')
        names.add(name)
        # power_kw is a rate, not interval energy; do not silently treat it as kWh.
        energy = number(item.get('energy', item.get('energy_kwh')), 'Energy (kWh)', 0, 10000)
        temp = number(item.get('outdoor_temperature', outside), 'Outdoor temperature', -60, 70, True)
        parsed.append((name, energy, temp))
    return ts, notes, parsed


@api_access()
def handle_submission(request, phase):
    if request.method == 'GET':
        try:
            limit = int(request.GET.get('limit', 100))
            if not 1 <= limit <= 500:
                raise ValueError
        except ValueError:
            return error('Limit must be an integer between 1 and 500.')
        qs = TelemetryRecord.objects.select_related('team').filter(phase=phase)
        if request.team:
            qs = qs.filter(team=request.team)
        records = [{'id': r.pk, 'team_name': r.team.team_name, 'phase': r.phase, 'appliance': r.appliance,
            'energy': r.energy, 'outdoor_temperature': r.outdoor_temperature, 'timestamp': r.timestamp.isoformat(),
            'interval_timestamp': r.interval_timestamp.isoformat() if r.interval_timestamp else None} for r in qs.order_by('-id')[:limit]]
        return JsonResponse({'status': 'success', 'phase': phase, 'count': len(records), 'data': records})
    if request.method != 'POST':
        return error('Method not allowed.', 405)
    if not request.team or not extract_token(request):
        return error('Use a team API token to submit telemetry.', 403)
    try:
        body = parse_body(request)
        ts, notes, items = validate_submission(body)
    except ValueError as exc:
        return error(str(exc))
    blocked = throttle(request.team.pk, 'telemetry', [(24, 60), (120, 3600)], cost=len(items))
    if blocked is not None:
        return blocked
    # A shared team cooldown serializes concurrent bursts before they hit the database.
    if not cache.add(f'submission:{request.team.pk}', True, 2):
        return error('Wait 2 seconds between submissions. Batch appliances together.', 429, 2)
    slot = snap_to_15min_interval(ts)
    saved = []
    with transaction.atomic():
        # Lock the team to enforce the per-team appliance cap across concurrent workers.
        Team.objects.select_for_update().get(pk=request.team.pk)
        known = set(TelemetryRecord.objects.filter(team=request.team, phase=phase).values_list('appliance', flat=True).distinct())
        if len(known | {name for name, _, _ in items}) > MAX_APPLIANCES:
            return error(f'A team can report at most {MAX_APPLIANCES} distinct appliances per phase.')
        for name, energy, temp in items:
            values = {'energy': energy, 'outdoor_temperature': temp, 'notes': notes, 'timestamp': ts, 'raw_payload': ''}
            rec, created = TelemetryRecord.objects.update_or_create(team=request.team, phase=phase,
                appliance=name, interval_timestamp=slot, defaults=values)
            saved.append({'id': rec.pk, 'appliance': name, 'energy': energy, 'outdoor_temperature': temp,
                'timestamp': ts.isoformat(), 'interval_timestamp': slot.isoformat(), 'updated': not created})
        if phase == 'testing':
            old = list(TelemetryRecord.objects.filter(team=request.team, phase=phase).order_by('-id').values_list('id', flat=True)[20:])
            TelemetryRecord.objects.filter(pk__in=old).delete()
    touch_team(request.team)
    return JsonResponse({'status': 'success', 'phase': phase, 'team_name': request.team.team_name,
        'message': f'{len(saved)} readings saved. Existing readings in the same 15-minute slot are replaced.',
        'interval_ist': slot.astimezone(IST).strftime('%Y-%m-%d %H:%M IST'), 'records': saved}, status=201)


@csrf_exempt
def api_testing(request):
    return handle_submission(request, 'testing')


@csrf_exempt
def api_baseline(request):
    return handle_submission(request, 'baseline')


@csrf_exempt
def api_prototype(request):
    return handle_submission(request, 'prototype')


api_reports = api_baseline
api_sandbox_test = api_testing


@require_GET
@api_access()
def api_appliances(request):
    qs = TelemetryRecord.objects.filter(phase='baseline')
    if request.team:
        qs = qs.filter(team=request.team)
    names = list(qs.values_list('appliance', flat=True).distinct().order_by('appliance')[:MAX_APPLIANCES])
    apps = []
    for name in names:
        latest = qs.filter(appliance=name).order_by('-timestamp', '-id').first()
        apps.append({'appliance': name, 'energy': latest.energy, 'outdoor_temperature': latest.outdoor_temperature,
                     'timestamp': latest.timestamp.isoformat()})
    return JsonResponse({'status': 'success', 'count': len(apps), 'appliances': apps})


@require_GET
@api_access()
def api_team_status(request):
    if request.team is None:
        return error('Use a team token to view team status.', 400)
    counts = dict(TelemetryRecord.objects.filter(team=request.team).values('phase').annotate(total=Count('id')).values_list('phase', 'total'))
    return JsonResponse({'status': 'success', 'team_name': request.team.team_name,
                         'counts': {phase: counts.get(phase, 0) for phase in PHASES},
                         'last_tariff_fetched_at': request.team.last_tariff_fetched_at})


@require_GET
@api_access(organizer_only=True)
def api_leaderboard(request):
    rows = list(Team.objects.annotate(baseline_count=Count('telemetry_records', filter=Q(telemetry_records__phase='baseline')),
            prototype_count=Count('telemetry_records', filter=Q(telemetry_records__phase='prototype'))).values('team_name', 'baseline_count', 'prototype_count'))
    return JsonResponse({'status': 'success', 'leaderboard': sorted(rows, key=lambda r: r['baseline_count'], reverse=True)})


@require_GET
@api_access()
def api_telemetry_intervals(request):
    phase = request.GET.get('phase', 'baseline')
    period = request.GET.get('range', '24h')
    if phase not in PHASES or period not in ('6h', '24h', '3d', '7d', 'all'):
        return error('Choose a valid phase and time range.')
    if request.team:
        team = request.team
        if request.GET.get('team') not in (None, '', team.team_name):
            return error('You can only access your own team.', 403)
    else:
        team = Team.objects.filter(team_name=request.GET.get('team')).first() if request.GET.get('team') else Team.objects.first()
    if team is None:
        return error('Team not found.', 404)
    hours = {'6h': 6, '24h': 24, '3d': 72, '7d': 168, 'all': 24 * 31}[period]
    cutoff = timezone.now() - timedelta(hours=hours)
    qs = TelemetryRecord.objects.filter(team=team, phase=phase, interval_timestamp__gte=cutoff).order_by('interval_timestamp', 'id')
    appliances = list(TelemetryRecord.objects.filter(team=team, phase=phase).values_list('appliance', flat=True).distinct().order_by('appliance')[:MAX_APPLIANCES])
    app = request.GET.get('appliance', '')
    if app and app != 'all':
        qs = qs.filter(appliance=app)
    # 31 days x 96 intervals x 12 appliances is a strict bound even on a long-running competition.
    records = list(qs[:35712])
    slots = {}
    for rec in records:
        key = rec.interval_timestamp.astimezone(IST).isoformat()
        slots.setdefault(key, {})[rec.appliance] = rec
    selected = [app] if app and app != 'all' else appliances
    series = {name: [] for name in selected}
    labels, totals, temps, timestamps = [], [], [], []
    for key, group in sorted(slots.items()):
        timestamps.append(key)
        labels.append(datetime.fromisoformat(key).strftime('%d %b %H:%M'))
        totals.append(round(sum(r.energy for r in group.values()), 4))
        values = [r.outdoor_temperature for r in group.values() if r.outdoor_temperature is not None]
        temps.append(round(sum(values) / len(values), 1) if values else None)
        for name in selected:
            series[name].append(group[name].energy if name in group else None)
    table = [{'id': r.pk, 'interval_ist': r.interval_timestamp.astimezone(IST).strftime('%d %b %Y, %H:%M'),
        'appliance': r.appliance, 'energy': r.energy, 'outdoor_temperature': r.outdoor_temperature,
        'recorded_at': r.timestamp.astimezone(IST).strftime('%H:%M:%S')} for r in reversed(records[-100:])]
    all_temps = [r.outdoor_temperature for r in records if r.outdoor_temperature is not None]
    return JsonResponse({'status': 'success', 'team_name': team.team_name, 'appliances': appliances,
        'labels': labels, 'timestamps': timestamps, 'series_by_appliance': series, 'total_energy_series': totals,
        'temperature_series': temps, 'total_records': len(records), 'total_intervals': len(slots), 'table_rows': table,
        'stats': {'sum_energy_kwh': round(sum(totals), 3), 'peak_interval_kwh': max(totals, default=0),
        'avg_temp_c': round(sum(all_temps)/len(all_temps), 1) if all_temps else None, 'interval_count': len(slots)}})


@require_GET
@api_access()
def download_tariffs_csv(request):
    tariffs, _ = get_or_create_published_tariffs()
    out = StringIO()
    writer = csv.writer(out)
    writer.writerow(['hour', 'day', 'start_time_ist', 'duration_hours', 'price_per_kwh', 'currency'])
    for t in tariffs:
        writer.writerow([t[k] for k in ('hour', 'day', 'start_time_ist', 'duration_hours', 'price_per_kwh', 'currency')])
    response = HttpResponse(out.getvalue(), content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="tariffs_ist.csv"'
    return response


@require_GET
@api_access()
def download_telemetry_csv(request):
    identity = 'team:' + str(request.team.pk) if request.team else request.META.get('REMOTE_ADDR', 'unknown')
    blocked = throttle(identity, 'export', [(2, 60)])
    if blocked is not None:
        return blocked
    phase = request.GET.get('phase', '')
    if phase and phase not in PHASES:
        return error('Invalid phase.')
    qs = TelemetryRecord.objects.select_related('team').order_by('id')
    if phase:
        qs = qs.filter(phase=phase)
    if request.GET.get('team'):
        qs = qs.filter(team__team_name=request.GET['team'])
    if request.team:
        qs = qs.filter(team=request.team)
    # Stream in bounded chunks instead of loading an entire competition into memory.
    class Echo:
        def write(self, value):
            return value
    writer = csv.writer(Echo())
    def safe(value):
        return "'" + value if isinstance(value, str) and value.lstrip().startswith(('=', '+', '-', '@')) else value
    def rows():
        yield writer.writerow(['id', 'phase', 'timestamp_ist', 'interval_15min_ist', 'team_name', 'appliance', 'outdoor_temperature_c', 'energy_kwh', 'notes'])
        for r in qs.iterator(chunk_size=500):
            yield writer.writerow([safe(v) for v in [r.pk, r.phase, r.timestamp.astimezone(IST).isoformat(),
                r.interval_timestamp.astimezone(IST).isoformat() if r.interval_timestamp else '', r.team.team_name,
                r.appliance, r.outdoor_temperature, r.energy, r.notes]])
    response = StreamingHttpResponse(rows(), content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="competition_telemetry.csv"'
    return response
