from __future__ import annotations

import csv
import json
import time
from datetime import datetime, timedelta, timezone
from io import StringIO
from typing import Any, Dict, List, Optional, Tuple

from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.csrf import csrf_exempt

from .models import Team, TelemetryRecord, snap_to_15min_interval
from .tariffs import (
    IST,
    get_2day_ahead_tariffs,
    generate_dynamic_tariffs,
    save_published_tariffs,
    get_or_create_published_tariffs,
)

# -----------------------------------------------------------------------------
# Rate Limiting Engine (Multi-Tier: Cooldown, Per-Minute, Per-Hour)
# -----------------------------------------------------------------------------
# Telemetry limits (smart bounds for 15-minute reporting intervals)
last_appliance_submission: Dict[Tuple[str, str], float] = {}
team_telemetry_minute_window: Dict[str, List[float]] = {}
team_telemetry_hour_window: Dict[str, List[float]] = {}

MIN_APPLIANCE_INTERVAL_SEC = 2.0
MAX_TEAM_REPORTS_PER_MINUTE = 20
MAX_TEAM_REPORTS_PER_HOUR = 60

# Tariff download limits
last_tariff_download: Dict[str, float] = {}
team_tariff_minute_window: Dict[str, List[float]] = {}
team_tariff_hour_window: Dict[str, List[float]] = {}

MIN_TARIFF_INTERVAL_SEC = 5.0
MAX_TARIFF_DOWNLOADS_PER_MINUTE = 6
MAX_TARIFF_DOWNLOADS_PER_HOUR = 30


def verify_telemetry_rate_limit(team_name: str, appliance_names: List[str]) -> Optional[str]:
    now = time.time()

    # 1. Per-appliance cooldown (2 seconds)
    for app in appliance_names:
        key = (team_name, app)
        last_t = last_appliance_submission.get(key, 0.0)
        if now - last_t < MIN_APPLIANCE_INTERVAL_SEC:
            wait_s = round(MIN_APPLIANCE_INTERVAL_SEC - (now - last_t), 1)
            return f"Rate limit exceeded: Cooldown {MIN_APPLIANCE_INTERVAL_SEC}s required between submissions for {app}. Please wait {wait_s}s."

    # 2. Per-minute limit (15 submissions/min)
    m_window = [ts for ts in team_telemetry_minute_window.get(team_name, []) if now - ts < 60.0]
    team_telemetry_minute_window[team_name] = m_window
    if len(m_window) + len(appliance_names) > MAX_TEAM_REPORTS_PER_MINUTE:
        return f"Rate limit exceeded: Max {MAX_TEAM_REPORTS_PER_MINUTE} submissions per minute allowed for {team_name}. Add time.sleep() delay to your loop."

    # 3. Per-hour limit (120 submissions/hr)
    h_window = [ts for ts in team_telemetry_hour_window.get(team_name, []) if now - ts < 3600.0]
    team_telemetry_hour_window[team_name] = h_window
    if len(h_window) + len(appliance_names) > MAX_TEAM_REPORTS_PER_HOUR:
        return f"Rate limit exceeded: Max {MAX_TEAM_REPORTS_PER_HOUR} submissions per hour allowed for {team_name}."

    # Record timestamps
    for app in appliance_names:
        last_appliance_submission[(team_name, app)] = now
    team_telemetry_minute_window[team_name].extend([now] * len(appliance_names))
    team_telemetry_hour_window[team_name].extend([now] * len(appliance_names))
    return None


def verify_tariff_rate_limit(team_name: str) -> Optional[Tuple[str, int]]:
    now = time.time()

    # 1. Cooldown between consecutive fetches (5 seconds)
    last_t = last_tariff_download.get(team_name, 0.0)
    if now - last_t < MIN_TARIFF_INTERVAL_SEC:
        wait_s = int(MIN_TARIFF_INTERVAL_SEC - (now - last_t)) + 1
        return (
            f"Rate limit exceeded: 48-hour tariffs do not change every second. Minimum cooldown is {MIN_TARIFF_INTERVAL_SEC:.0f}s. Please wait {wait_s}s.",
            wait_s,
        )

    # 2. Per-minute limit (6 fetches/min)
    m_window = [ts for ts in team_tariff_minute_window.get(team_name, []) if now - ts < 60.0]
    team_tariff_minute_window[team_name] = m_window
    if len(m_window) >= MAX_TARIFF_DOWNLOADS_PER_MINUTE:
        return (
            f"Rate limit exceeded: Max {MAX_TARIFF_DOWNLOADS_PER_MINUTE} tariff downloads per minute allowed for {team_name}. Please cache tariffs in memory.",
            60,
        )

    # 3. Per-hour limit (30 fetches/hr)
    h_window = [ts for ts in team_tariff_hour_window.get(team_name, []) if now - ts < 3600.0]
    team_tariff_hour_window[team_name] = h_window
    if len(h_window) >= MAX_TARIFF_DOWNLOADS_PER_HOUR:
        return (
            f"Rate limit exceeded: Max {MAX_TARIFF_DOWNLOADS_PER_HOUR} tariff downloads per hour allowed for {team_name}. Please store and reuse the downloaded tariffs.",
            300,
        )

    last_tariff_download[team_name] = now
    team_tariff_minute_window[team_name].append(now)
    team_tariff_hour_window[team_name].append(now)
    return None


def extract_token(request) -> Optional[str]:
    auth_header = request.headers.get("Authorization", "")
    if auth_header:
        parts = auth_header.split()
        if len(parts) == 2 and parts[0].lower() == "bearer":
            return parts[1].strip()
        return auth_header.strip()
    return request.GET.get("token") or request.POST.get("token")


def authenticate_team(request) -> Optional[Team]:
    raw_token = extract_token(request)
    if not raw_token:
        return None
    clean_token = raw_token.strip().replace("Bearer ", "").replace("bearer ", "")
    try:
        team = Team.objects.get(token=clean_token)
        now_dt = datetime.now(IST)
        needs_save = False
        if not team.first_connected_at:
            team.first_connected_at = now_dt
            needs_save = True
        if not team.last_connected_at or (now_dt - team.last_connected_at).total_seconds() > 60:
            team.last_connected_at = now_dt
            needs_save = True
        if needs_save:
            team.save(update_fields=["first_connected_at", "last_connected_at"])
        return team
    except Team.DoesNotExist:
        return None


# -----------------------------------------------------------------------------
# Organizer Portal Authentication Views
# -----------------------------------------------------------------------------
def login_view(request):
    """Organizer Portal Admin Login View."""
    next_url = request.POST.get("next") or request.GET.get("next") or "/"
    if not next_url.startswith("/"):
        next_url = "/"

    if request.user.is_authenticated:
        return redirect(next_url)

    error = None
    if request.method == "POST":
        uname = request.POST.get("username", "").strip()
        pwd = request.POST.get("password", "")
        user = authenticate(request, username=uname, password=pwd)
        if user is not None:
            login(request, user)
            return redirect(next_url)
        else:
            error = "Invalid username or password. Please try again."

    return render(request, "vtn_app/login.html", {
        "error": error,
        "next": next_url,
        "username": request.POST.get("username", ""),
    })


def logout_view(request):
    """Organizer Portal Logout View."""
    logout(request)
    return redirect("/login/")


# -----------------------------------------------------------------------------
# Organizer Dashboard View
# -----------------------------------------------------------------------------
@login_required(login_url="/login/")
def dashboard_view(request):
    teams = Team.objects.all().order_by("team_name")
    team_rows = []
    total_active_appliances = 0

    for t in teams:
        apps = t.get_latest_appliances(phase="baseline")
        total_active_appliances += len(apps)

        test_cnt = t.telemetry_records.filter(phase="testing").count()
        base_cnt = t.telemetry_records.filter(phase="baseline").count()
        proto_cnt = t.telemetry_records.filter(phase="prototype").count()

        team_rows.append({
            "team_name": t.team_name,
            "appliances": apps,
            "testing_count": test_cnt,
            "baseline_count": base_cnt,
            "prototype_count": proto_cnt,
            "baseline_reports": base_cnt,  # backward compatibility
            "prototype_reports": proto_cnt,  # backward compatibility
            "has_tested": test_cnt > 0,
            "last_tariff_fetched": (
                t.last_tariff_fetched_at.astimezone(IST).strftime("%d %b, %H:%M IST")
                if t.last_tariff_fetched_at
                else None
            ),
        })

    recent_telemetry = TelemetryRecord.objects.select_related("team").order_by("-id")[:15]
    live_ist_now = datetime.now(IST).strftime("%d %b %Y, %H:%M:%S IST")
    total_baseline_entries = TelemetryRecord.objects.filter(phase="baseline").count()
    total_data_entries = TelemetryRecord.objects.count()

    context = {
        "teams": team_rows,
        "recent_telemetry": recent_telemetry,
        "total_teams_cnt": teams.count(),
        "total_active_appliances": total_active_appliances,
        "total_baseline_entries": total_baseline_entries,
        "total_data_entries": total_data_entries,
        "total_telemetry_reports": total_data_entries,  # backward compatibility
        "live_ist_now": live_ist_now,
    }
    return render(request, "vtn_app/dashboard.html", context)


# -----------------------------------------------------------------------------
# REST API Endpoints
# -----------------------------------------------------------------------------
def api_verify_token(request):
    team = authenticate_team(request)
    if not team:
        return JsonResponse({"status": "error", "message": "Invalid token."}, status=401)
    return JsonResponse({
        "status": "success",
        "authenticated": True,
        "team_name": team.team_name,
    })


def api_tariffs_json(request):
    team = authenticate_team(request)
    if not team:
        return JsonResponse({"status": "error", "message": "Invalid token."}, status=401)

    tariff_err = verify_tariff_rate_limit(team.team_name)
    if tariff_err:
        msg, retry_after = tariff_err
        resp = JsonResponse({
            "status": "error",
            "message": msg,
            "retry_after_seconds": retry_after,
        }, status=429)
        resp["Retry-After"] = str(retry_after)
        return resp

    # Track tariff receipt timestamp for this team
    team.last_tariff_fetched_at = datetime.now(IST)
    team.save(update_fields=["last_tariff_fetched_at"])

    tariffs, seed_used = get_or_create_published_tariffs()
    prices = [t["price_per_kwh"] for t in tariffs]
    resp = JsonResponse({
        "status": "success",
        "market": "India Dynamic Time-of-Use Electricity Tariff",
        "team_authenticated": team.team_name,
        "currency": "INR",
        "horizon_days": 2,
        "total_intervals": len(tariffs),
        "seed_used": seed_used,
        "min_price": min(prices) if prices else 8.0,
        "max_price": max(prices) if prices else 20.0,
        "tariffs": tariffs,
    })
    resp["Cache-Control"] = "public, max-age=60"
    return resp


@csrf_exempt
def api_publish_tariffs(request):
    if request.method != "POST":
        return JsonResponse({"status": "error", "message": "Method not allowed"}, status=405)

    team = authenticate_team(request)
    if not team:
        return JsonResponse({"status": "error", "message": "Invalid token."}, status=401)

    try:
        body = json.loads(request.body.decode("utf-8")) if request.body else {}
    except Exception:
        body = {}

    seed = body.get("seed")
    if seed is not None:
        try:
            seed = int(seed)
        except (ValueError, TypeError):
            seed = None

    min_p = float(body.get("min_price", 8.0))
    max_p = float(body.get("max_price", 20.0))

    custom_tariffs = body.get("tariffs")
    if custom_tariffs and isinstance(custom_tariffs, list) and len(custom_tariffs) == 48:
        points = custom_tariffs
        save_published_tariffs(points, seed=seed)
    else:
        points, seed = generate_dynamic_tariffs(seed=seed, min_price=min_p, max_price=max_p)
        save_published_tariffs(points, seed=seed)

    prices = [p["price_per_kwh"] for p in points]
    return JsonResponse({
        "status": "success",
        "message": f"Successfully published 48-hour dynamic Time-of-Use tariffs (Seed: {seed}).",
        "published_by": team.team_name,
        "seed_used": seed,
        "min_price": min(prices),
        "max_price": max(prices),
        "total_intervals": len(points),
        "tariffs": points,
    }, status=201)


# -----------------------------------------------------------------------------
# Unified Student Telemetry Submission Handler (Testing, Baseline, Prototype)
# -----------------------------------------------------------------------------
def handle_submission(request, phase: str):
    """Processes incoming telemetry for Phase 1 (testing), Phase 2 (baseline), or Phase 3 (prototype)."""
    if request.method == "GET":
        team = authenticate_team(request)
        limit = int(request.GET.get("limit", 100))
        qs = TelemetryRecord.objects.select_related("team").filter(phase=phase).order_by("-id")
        if team:
            qs = qs.filter(team=team)
        records = [
            {
                "id": r.id,
                "phase": r.phase,
                "timestamp": r.timestamp.isoformat(),
                "interval_timestamp": r.interval_timestamp.isoformat() if r.interval_timestamp else None,
                "team_name": r.team.team_name,
                "appliance": r.appliance,
                "outdoor_temperature": r.outdoor_temperature,
                "energy": r.energy,
                "notes": r.notes,
            }
            for r in qs[:limit]
        ]
        return JsonResponse({"status": "success", "phase": phase, "count": len(records), "data": records})

    elif request.method == "POST":
        team = authenticate_team(request)
        if not team:
            return JsonResponse({"status": "error", "phase": phase, "message": "Invalid or missing team token."}, status=401)

        try:
            body = json.loads(request.body.decode("utf-8")) if request.body else {}
        except Exception:
            return JsonResponse({"status": "error", "phase": phase, "message": "Invalid JSON body"}, status=400)

        notes = body.get("notes", "")
        raw_str = json.dumps(body)

        if "appliances" in body and isinstance(body["appliances"], dict):
            apps_to_report = list(body["appliances"].keys())
        elif "appliances" in body and isinstance(body["appliances"], list):
            apps_to_report = [item.get("appliance", "Appliance") for item in body["appliances"] if isinstance(item, dict)]
        else:
            apps_to_report = [body.get("appliance", "Generic_Load")]

        # For baseline and prototype, enforce rate limits
        if phase in ["baseline", "prototype"]:
            rate_err = verify_telemetry_rate_limit(team.team_name, apps_to_report)
            if rate_err:
                return JsonResponse({"status": "error", "phase": phase, "message": rate_err}, status=429)

        # Parse timestamp or use current IST
        ts_val = body.get("timestamp") or body.get("time")
        if ts_val:
            try:
                now_dt = datetime.fromisoformat(str(ts_val).replace("Z", "+00:00"))
            except Exception:
                now_dt = datetime.now(IST)
        else:
            now_dt = datetime.now(IST)

        interval_dt = snap_to_15min_interval(now_dt)

        outdoor_temp = body.get("outdoor_temperature")
        if outdoor_temp is not None:
            try:
                outdoor_temp = float(outdoor_temp)
            except (ValueError, TypeError):
                outdoor_temp = None

        parsed_items = []
        if "appliances" in body and isinstance(body["appliances"], list):
            for item in body["appliances"]:
                if isinstance(item, dict):
                    app_name = item.get("appliance", "Generic_Load")
                    enrg = float(item.get("energy", item.get("energy_kwh", item.get("power_kw", 0.0))))
                    item_temp = item.get("outdoor_temperature", outdoor_temp)
                    parsed_items.append((app_name, enrg, item_temp))
        elif "appliances" in body and isinstance(body["appliances"], dict):
            for app_name, val in body["appliances"].items():
                if isinstance(val, dict):
                    enrg = float(val.get("energy", val.get("energy_kwh", val.get("power_kw", 0.0))))
                    item_temp = val.get("outdoor_temperature", outdoor_temp)
                else:
                    enrg = float(val)
                    item_temp = outdoor_temp
                parsed_items.append((app_name, enrg, item_temp))
        else:
            app_name = body.get("appliance", "Generic_Load")
            enrg = float(body.get("energy", body.get("energy_kwh", body.get("power_kw", 1.5))))
            parsed_items.append((app_name, enrg, outdoor_temp))

        records_saved = []
        for app_name, enrg, item_temp in parsed_items:
            if item_temp is not None:
                try:
                    item_temp = float(item_temp)
                except (ValueError, TypeError):
                    item_temp = outdoor_temp

            if phase == "testing":
                # For testing phase: keep recent test records in database for organizer reference
                rec = TelemetryRecord.objects.create(
                    team=team,
                    appliance=app_name,
                    phase="testing",
                    outdoor_temperature=item_temp,
                    energy=enrg,
                    notes=notes or "Phase 1 Testing Verification",
                    raw_payload=raw_str,
                    timestamp=now_dt,
                    interval_timestamp=interval_dt,
                )
                # Retain up to 20 recent test records per team for reference
                old_test_ids = list(
                    TelemetryRecord.objects.filter(team=team, phase="testing")
                    .order_by("-id")
                    .values_list("id", flat=True)[20:]
                )
                if old_test_ids:
                    TelemetryRecord.objects.filter(id__in=old_test_ids).delete()
            else:
                # For baseline (Phase 2) and prototype (Phase 3): 15-minute interval binned
                rec, _ = TelemetryRecord.objects.update_or_create(
                    team=team,
                    appliance=app_name,
                    phase=phase,
                    interval_timestamp=interval_dt,
                    defaults={
                        "outdoor_temperature": item_temp,
                        "energy": enrg,
                        "notes": notes,
                        "raw_payload": raw_str,
                        "timestamp": now_dt,
                    },
                )

            records_saved.append({
                "id": rec.id,
                "phase": phase,
                "appliance": app_name,
                "energy": enrg,
                "outdoor_temperature": item_temp,
                "timestamp": now_dt.isoformat(),
                "interval_timestamp": interval_dt.isoformat(),
            })

        return JsonResponse({
            "status": "success",
            "phase": phase,
            "team_name": team.team_name,
            "interval_ist": interval_dt.strftime("%Y-%m-%d %H:%M IST"),
            "message": f"Successfully received {len(records_saved)} {phase} entries for {team.team_name}.",
            "records": records_saved,
        }, status=201)

    return JsonResponse({"status": "error", "message": "Method not allowed"}, status=405)


@csrf_exempt
def api_testing(request):
    """Phase 1 Testing Endpoint: POST /api/v3/testing."""
    return handle_submission(request, phase="testing")


@csrf_exempt
def api_baseline(request):
    """Phase 2 Baseline Endpoint: POST /api/v3/baseline."""
    return handle_submission(request, phase="baseline")


@csrf_exempt
def api_prototype(request):
    """Phase 3 Prototype Endpoint: POST /api/v3/prototype."""
    return handle_submission(request, phase="prototype")


@csrf_exempt
def api_reports(request):
    """Legacy alias for Phase 2 Baseline."""
    return handle_submission(request, phase="baseline")


@csrf_exempt
def api_sandbox_test(request):
    """Legacy alias for Phase 1 Testing."""
    return handle_submission(request, phase="testing")


def api_appliances(request):
    team = authenticate_team(request)
    if not team:
        return JsonResponse({"status": "error", "message": "Invalid token."}, status=401)

    apps = team.get_latest_appliances()
    app_list = [
        {
            "appliance": name,
            "energy": data["energy"],
            "outdoor_temperature": data["outdoor_temperature"],
            "timestamp": data["timestamp"],
        }
        for name, data in sorted(apps.items())
    ]
    return JsonResponse({
        "status": "success",
        "team_name": team.team_name,
        "count": len(app_list),
        "appliances": app_list,
    })


def api_leaderboard(request):
    teams = Team.objects.all().order_by("team_name")
    lb = []
    for t in teams:
        cnt = t.telemetry_records.count()
        base_cnt = t.telemetry_records.filter(phase="baseline").count()
        proto_cnt = t.telemetry_records.filter(phase="prototype").count()
        apps = t.get_latest_appliances()
        lb.append({
            "team_name": t.team_name,
            "baseline_count": base_cnt,
            "prototype_count": proto_cnt,
            "data_count": cnt,
            "reports_count": cnt,  # backward compatibility
            "active_appliances_count": len(apps),
        })
    lb.sort(key=lambda x: x["baseline_count"], reverse=True)
    return JsonResponse({"status": "success", "leaderboard": lb})


# -----------------------------------------------------------------------------
# File Downloads
# -----------------------------------------------------------------------------
def download_tariffs_csv(request):
    tariffs = get_2day_ahead_tariffs()
    out = StringIO()
    writer = csv.writer(out)
    writer.writerow(["hour", "day", "start_time_ist", "duration_hours", "price_per_kwh", "currency"])
    for t in tariffs:
        writer.writerow([t["hour"], t["day"], t["start_time_ist"], t["duration_hours"], f"{t['price_per_kwh']:.2f}", t["currency"]])
    response = HttpResponse(out.getvalue(), content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="2day_ahead_tariffs_ist.csv"'
    return response


def download_telemetry_csv(request):
    out = StringIO()
    writer = csv.writer(out)
    writer.writerow([
        "id",
        "timestamp_ist",
        "interval_15min_ist",
        "team_name",
        "appliance",
        "outdoor_temperature_c",
        "energy_kwh",
        "notes",
    ])
    records = TelemetryRecord.objects.select_related("team").order_by("id")
    for r in records:
        interval_str = (
            r.interval_timestamp.astimezone(IST).strftime("%Y-%m-%d %H:%M IST")
            if r.interval_timestamp
            else ""
        )
        writer.writerow([
            r.id,
            r.timestamp.astimezone(IST).strftime("%Y-%m-%d %H:%M:%S IST"),
            interval_str,
            r.team.team_name,
            r.appliance,
            r.outdoor_temperature if r.outdoor_temperature is not None else "",
            r.energy,
            r.notes,
        ])
    response = HttpResponse(out.getvalue(), content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="competition_telemetry_all_teams.csv"'
    return response


# -----------------------------------------------------------------------------
# Dedicated Analytics & Charts Panel Views
# -----------------------------------------------------------------------------
@login_required(login_url="/login/")
def analytics_view(request):
    """Renders the dedicated OpenADR 3.0 Team Analytics & 15-Minute Interval Charts Panel."""
    teams = Team.objects.all().order_by("team_name")
    selected_team_name = request.GET.get("team") or (teams[0].team_name if teams.exists() else "")
    
    appliance_qs = TelemetryRecord.objects.values_list("appliance", flat=True).distinct()
    all_appliances = sorted(list({a for a in appliance_qs if a}))

    context = {
        "teams": teams,
        "selected_team_name": selected_team_name,
        "all_appliances": all_appliances,
        "live_ist_now": datetime.now(IST).strftime("%d %b %Y, %H:%M:%S IST"),
    }
    return render(request, "vtn_app/analytics.html", context)


def api_telemetry_intervals(request):
    """JSON API endpoint returning 15-minute interval aggregated telemetry for Chart.js rendering."""
    team_name = request.GET.get("team", "").strip()
    appliance_filter = request.GET.get("appliance", "").strip()
    time_range = request.GET.get("range", "all").strip().lower()
    phase = request.GET.get("phase", "baseline").strip().lower()

    if not team_name:
        first_team = Team.objects.first()
        if not first_team:
            return JsonResponse({"status": "error", "message": "No teams found"}, status=404)
        team = first_team
    else:
        try:
            team = Team.objects.get(team_name=team_name)
        except Team.DoesNotExist:
            return JsonResponse({"status": "error", "message": f"Team '{team_name}' not found"}, status=404)

    qs = TelemetryRecord.objects.filter(team=team, phase=phase, interval_timestamp__isnull=False)
    if appliance_filter and appliance_filter.lower() != "all":
        qs = qs.filter(appliance=appliance_filter)

    records = list(qs.order_by("interval_timestamp", "appliance"))
    team_appliances = sorted(list({r.appliance for r in records}))

    # Time-horizon filter for graph (allows zooming into 6h, 24h, 3d, 7d, or full 2 weeks)
    if records and time_range != "all":
        max_dt = records[-1].interval_timestamp
        if time_range == "6h":
            cutoff = max_dt - timedelta(hours=6)
        elif time_range == "24h":
            cutoff = max_dt - timedelta(hours=24)
        elif time_range == "3d":
            cutoff = max_dt - timedelta(days=3)
        elif time_range == "7d":
            cutoff = max_dt - timedelta(days=7)
        else:
            cutoff = None

        if cutoff:
            records_for_chart = [r for r in records if r.interval_timestamp >= cutoff]
        else:
            records_for_chart = records
    else:
        records_for_chart = records

    # Aggregate by 15-minute interval timestamp for chart
    interval_map: Dict[str, Dict[str, Any]] = {}
    for r in records_for_chart:
        ist_dt = r.interval_timestamp.astimezone(IST)
        key = ist_dt.strftime("%Y-%m-%d %H:%M")
        if key not in interval_map:
            interval_map[key] = {
                "interval_time_ist": key,
                "interval_label": ist_dt.strftime("%d %b %H:%M"),
                "appliances": {},
                "total_energy": 0.0,
                "temperatures": [],
            }
        # Latest reading for this appliance in this interval
        interval_map[key]["appliances"][r.appliance] = {
            "energy": r.energy,
            "outdoor_temperature": r.outdoor_temperature,
        }
        interval_map[key]["total_energy"] = round(interval_map[key]["total_energy"] + r.energy, 3)
        if r.outdoor_temperature is not None:
            interval_map[key]["temperatures"].append(r.outdoor_temperature)

    sorted_intervals = sorted(interval_map.values(), key=lambda x: x["interval_time_ist"])

    # Build per-appliance series and total curves
    series_by_appliance: Dict[str, List[float]] = {app: [] for app in team_appliances}
    total_energy_series = []
    temp_series = []
    labels = []

    for item in sorted_intervals:
        labels.append(item["interval_label"])
        total_energy_series.append(item["total_energy"])
        avg_temp = (
            round(sum(item["temperatures"]) / len(item["temperatures"]), 1)
            if item["temperatures"]
            else None
        )
        temp_series.append(avg_temp)

        for app in team_appliances:
            app_data = item["appliances"].get(app)
            series_by_appliance[app].append(app_data["energy"] if app_data else 0.0)

    all_energies = [item["total_energy"] for item in sorted_intervals]
    peak_energy = max(all_energies) if all_energies else 0.0
    sum_energy = round(sum(all_energies), 2)
    all_temps = [t for item in sorted_intervals for t in item["temperatures"]]
    avg_temp = round(sum(all_temps) / len(all_temps), 1) if all_temps else None

    # Table breakdown rows: strictly last 6 hours, deduplicated per appliance slot
    table_rows = []
    if records:
        latest_dt = records[-1].interval_timestamp
        six_hours_cutoff = latest_dt - timedelta(hours=6)
        recent_records = [r for r in records if r.interval_timestamp >= six_hours_cutoff]
        seen_slots = set()
        for r in reversed(recent_records):
            slot_key = (r.appliance, r.interval_timestamp)
            if slot_key in seen_slots:
                continue
            seen_slots.add(slot_key)
            r_ist = r.interval_timestamp.astimezone(IST)
            table_rows.append({
                "id": r.id,
                "interval_ist": r_ist.strftime("%d %b %Y, %H:%M IST"),
                "appliance": r.appliance,
                "energy": r.energy,
                "outdoor_temperature": r.outdoor_temperature,
                "recorded_at": r.timestamp.astimezone(IST).strftime("%H:%M:%S IST"),
            })

    return JsonResponse({
        "status": "success",
        "team_name": team.team_name,
        "total_records": len(records),
        "total_intervals": len(sorted_intervals),
        "appliances": team_appliances,
        "labels": labels,
        "series_by_appliance": series_by_appliance,
        "total_energy_series": total_energy_series,
        "temperature_series": temp_series,
        "stats": {
            "sum_energy_kwh": sum_energy,
            "peak_interval_kwh": peak_energy,
            "avg_temp_c": avg_temp,
            "interval_count": len(sorted_intervals),
        },
        "table_rows": table_rows,
    })
