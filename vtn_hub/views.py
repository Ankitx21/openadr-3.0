from __future__ import annotations

import csv
import json
import time
from datetime import datetime, timedelta, timezone
from io import StringIO
from typing import Any, Dict, List, Optional, Tuple

from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt

from .models import Team, TelemetryRecord
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
# Telemetry limits
last_appliance_submission: Dict[Tuple[str, str], float] = {}
team_telemetry_minute_window: Dict[str, List[float]] = {}
team_telemetry_hour_window: Dict[str, List[float]] = {}

MIN_APPLIANCE_INTERVAL_SEC = 2.0
MAX_TEAM_REPORTS_PER_MINUTE = 15
MAX_TEAM_REPORTS_PER_HOUR = 120

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
            return f"Rate limit exceeded: Cooldown {MIN_APPLIANCE_INTERVAL_SEC}s required between reports for {app}. Please wait {wait_s}s."

    # 2. Per-minute limit (15 reports/min)
    m_window = [ts for ts in team_telemetry_minute_window.get(team_name, []) if now - ts < 60.0]
    team_telemetry_minute_window[team_name] = m_window
    if len(m_window) + len(appliance_names) > MAX_TEAM_REPORTS_PER_MINUTE:
        return f"Rate limit exceeded: Max {MAX_TEAM_REPORTS_PER_MINUTE} reports per minute allowed for {team_name}. Add time.sleep() delay to your loop."

    # 3. Per-hour limit (120 reports/hr)
    h_window = [ts for ts in team_telemetry_hour_window.get(team_name, []) if now - ts < 3600.0]
    team_telemetry_hour_window[team_name] = h_window
    if len(h_window) + len(appliance_names) > MAX_TEAM_REPORTS_PER_HOUR:
        return f"Rate limit exceeded: Max {MAX_TEAM_REPORTS_PER_HOUR} reports per hour allowed for {team_name}."

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
# Organizer Dashboard View
# -----------------------------------------------------------------------------
def dashboard_view(request):
    teams = Team.objects.all().order_by("team_name")
    team_rows = []
    total_active_appliances = 0

    for t in teams:
        apps = t.get_latest_appliances()
        total_active_appliances += len(apps)

        team_rows.append({
            "team_name": t.team_name,
            "appliances": apps,
            "total_reports": t.telemetry_records.count(),
        })

    recent_telemetry = TelemetryRecord.objects.select_related("team").order_by("-id")[:15]
    live_ist_now = datetime.now(IST).strftime("%d %b %Y, %H:%M:%S IST")
    total_telemetry_reports = TelemetryRecord.objects.count()

    context = {
        "teams": team_rows,
        "recent_telemetry": recent_telemetry,
        "total_teams_cnt": teams.count(),
        "total_active_appliances": total_active_appliances,
        "total_telemetry_reports": total_telemetry_reports,
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


@csrf_exempt
def api_reports(request):
    if request.method == "GET":
        team = authenticate_team(request)
        limit = int(request.GET.get("limit", 100))
        qs = TelemetryRecord.objects.select_related("team").order_by("-id")
        if team:
            qs = qs.filter(team=team)
        reports = [
            {
                "id": r.id,
                "timestamp": r.timestamp.isoformat(),
                "team_name": r.team.team_name,
                "appliance": r.appliance,
                "outdoor_temperature": r.outdoor_temperature,
                "energy": r.energy,
                "notes": r.notes,
            }
            for r in qs[:limit]
        ]
        return JsonResponse({"status": "success", "count": len(reports), "reports": reports})

    elif request.method == "POST":
        team = authenticate_team(request)
        if not team:
            return JsonResponse({"status": "error", "message": "Invalid token."}, status=401)

        try:
            body = json.loads(request.body.decode("utf-8"))
        except Exception:
            return JsonResponse({"status": "error", "message": "Invalid JSON body"}, status=400)

        notes = body.get("notes", "")
        raw_str = json.dumps(body)

        if "appliances" in body and isinstance(body["appliances"], dict):
            apps_to_report = list(body["appliances"].keys())
        elif "appliances" in body and isinstance(body["appliances"], list):
            apps_to_report = [item.get("appliance", "Appliance") for item in body["appliances"] if isinstance(item, dict)]
        else:
            apps_to_report = [body.get("appliance", "Appliance")]

        rate_err = verify_telemetry_rate_limit(team.team_name, apps_to_report)
        if rate_err:
            return JsonResponse({"status": "error", "message": rate_err}, status=429)

        # Parse timestamp or time if provided, else use current IST
        ts_val = body.get("timestamp") or body.get("time")
        if ts_val:
            try:
                now_dt = datetime.fromisoformat(str(ts_val).replace("Z", "+00:00"))
            except Exception:
                now_dt = datetime.now(IST)
        else:
            now_dt = datetime.now(IST)

        outdoor_temp = body.get("outdoor_temperature")
        if outdoor_temp is not None:
            try:
                outdoor_temp = float(outdoor_temp)
            except (ValueError, TypeError):
                outdoor_temp = None

        records_saved = []

        if "appliances" in body and isinstance(body["appliances"], list):
            for item in body["appliances"]:
                if isinstance(item, dict):
                    app_name = item.get("appliance", "Appliance")
                    enrg = float(item.get("energy", item.get("energy_kwh", item.get("power_kw", 0.0))))
                    item_temp = item.get("outdoor_temperature", outdoor_temp)
                    if item_temp is not None:
                        try:
                            item_temp = float(item_temp)
                        except (ValueError, TypeError):
                            item_temp = outdoor_temp
                    rec = TelemetryRecord.objects.create(
                        team=team,
                        appliance=app_name,
                        outdoor_temperature=item_temp,
                        energy=enrg,
                        notes=notes,
                        raw_payload=raw_str,
                        timestamp=now_dt,
                    )
                    records_saved.append({
                        "id": rec.id,
                        "appliance": app_name,
                        "energy": enrg,
                        "outdoor_temperature": item_temp,
                        "timestamp": now_dt.isoformat(),
                    })
        elif "appliances" in body and isinstance(body["appliances"], dict):
            for app_name, val in body["appliances"].items():
                if isinstance(val, dict):
                    enrg = float(val.get("energy", val.get("energy_kwh", val.get("power_kw", 0.0))))
                    item_temp = val.get("outdoor_temperature", outdoor_temp)
                    if item_temp is not None:
                        try:
                            item_temp = float(item_temp)
                        except (ValueError, TypeError):
                            item_temp = outdoor_temp
                else:
                    enrg = float(val)
                    item_temp = outdoor_temp
                rec = TelemetryRecord.objects.create(
                    team=team,
                    appliance=app_name,
                    outdoor_temperature=item_temp,
                    energy=enrg,
                    notes=notes,
                    raw_payload=raw_str,
                    timestamp=now_dt,
                )
                records_saved.append({
                    "id": rec.id,
                    "appliance": app_name,
                    "energy": enrg,
                    "outdoor_temperature": item_temp,
                    "timestamp": now_dt.isoformat(),
                })
        else:
            appliance_name = body.get("appliance", "Appliance")
            enrg = float(body.get("energy", body.get("energy_kwh", body.get("power_kw", 0.0))))
            rec = TelemetryRecord.objects.create(
                team=team,
                appliance=appliance_name,
                outdoor_temperature=outdoor_temp,
                energy=enrg,
                notes=notes,
                raw_payload=raw_str,
                timestamp=now_dt,
            )
            records_saved.append({
                "id": rec.id,
                "appliance": appliance_name,
                "energy": enrg,
                "outdoor_temperature": outdoor_temp,
                "timestamp": now_dt.isoformat(),
            })

        return JsonResponse({
            "status": "success",
            "message": f"Recorded {len(records_saved)} telemetry entries for {team.team_name}.",
            "team_name": team.team_name,
            "records": records_saved,
        }, status=201)

    return JsonResponse({"status": "error", "message": "Method not allowed"}, status=405)


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
        apps = t.get_latest_appliances()
        lb.append({
            "team_name": t.team_name,
            "reports_count": cnt,
            "active_appliances_count": len(apps),
        })
    lb.sort(key=lambda x: x["reports_count"], reverse=True)
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
    writer.writerow(["id", "timestamp_ist", "team_name", "appliance", "outdoor_temperature_c", "energy", "notes"])
    records = TelemetryRecord.objects.select_related("team").order_by("id")
    for r in records:
        writer.writerow([
            r.id,
            r.timestamp.strftime("%Y-%m-%d %H:%M:%S IST"),
            r.team.team_name,
            r.appliance,
            r.outdoor_temperature if r.outdoor_temperature is not None else "",
            r.energy,
            r.notes,
        ])
    response = HttpResponse(out.getvalue(), content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="competition_telemetry_all_teams.csv"'
    return response
