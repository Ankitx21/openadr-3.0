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
from .tariffs import IST, get_2day_ahead_tariffs

# -----------------------------------------------------------------------------
# Rate Limiting Engine
# -----------------------------------------------------------------------------
last_appliance_submission: Dict[Tuple[str, str], float] = {}
team_sliding_window: Dict[str, List[float]] = {}

MIN_APPLIANCE_INTERVAL_SEC = 2.0
MAX_TEAM_REPORTS_PER_MINUTE = 60


def verify_rate_limit(team_name: str, appliance_names: List[str]) -> Optional[str]:
    now = time.time()
    window = [ts for ts in team_sliding_window.get(team_name, []) if now - ts < 60.0]
    team_sliding_window[team_name] = window

    if len(window) + len(appliance_names) > MAX_TEAM_REPORTS_PER_MINUTE:
        return f"Rate limit exceeded: Max {MAX_TEAM_REPORTS_PER_MINUTE} reports per minute per team. Add delay to microcontroller loop."

    for app in appliance_names:
        key = (team_name, app)
        last_t = last_appliance_submission.get(key, 0.0)
        if now - last_t < MIN_APPLIANCE_INTERVAL_SEC:
            return f"Rate limit exceeded: Minimum {MIN_APPLIANCE_INTERVAL_SEC}s required between reports for {app}."

    for app in appliance_names:
        last_appliance_submission[(team_name, app)] = now
    team_sliding_window[team_name].extend([now] * len(appliance_names))
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
        if not team.first_connected_at:
            team.first_connected_at = now_dt
        team.last_connected_at = now_dt
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
    total_fleet_kw = 0.0
    connected_teams_cnt = 0
    total_active_appliances = 0

    for t in teams:
        apps = t.get_latest_appliances()
        total_team_kw = sum(a["power_kw"] for a in apps.values())
        total_fleet_kw += total_team_kw
        total_active_appliances += len(apps)
        if t.is_connected:
            connected_teams_cnt += 1

        team_rows.append({
            "team_name": t.team_name,
            "is_connected": t.is_connected,
            "appliances": apps,
            "total_kw": total_team_kw,
            "total_reports": t.telemetry_records.count(),
            "last_active": t.last_connected_at.strftime("%H:%M:%S IST") if t.last_connected_at else "Never",
        })

    recent_telemetry = TelemetryRecord.objects.select_related("team").order_by("-id")[:10]
    live_ist_now = datetime.now(IST).strftime("%d %b %Y, %H:%M:%S IST")
    total_telemetry_reports = TelemetryRecord.objects.count()

    context = {
        "teams": team_rows,
        "recent_telemetry": recent_telemetry,
        "total_fleet_kw": total_fleet_kw,
        "connected_teams_cnt": connected_teams_cnt,
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

    tariffs = get_2day_ahead_tariffs()
    return JsonResponse({
        "status": "success",
        "market": "India Day-Ahead Dynamic Electricity Tariff",
        "team_authenticated": team.team_name,
        "currency": "INR",
        "horizon_days": 2,
        "total_intervals": len(tariffs),
        "tariffs": tariffs,
    })


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
                "power_kw": r.power_kw,
                "energy_kwh": r.energy_kwh,
                "status": r.status,
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
        status_str = body.get("status", "RUNNING")
        raw_str = json.dumps(body)

        if "appliances" in body and isinstance(body["appliances"], dict):
            apps_to_report = list(body["appliances"].keys())
        else:
            apps_to_report = [body.get("appliance", "Appliance")]

        rate_err = verify_rate_limit(team.team_name, apps_to_report)
        if rate_err:
            return JsonResponse({"status": "error", "message": rate_err}, status=429)

        now_dt = datetime.now(IST)
        records_saved = []

        if "appliances" in body and isinstance(body["appliances"], dict):
            for app_name, pwr in body["appliances"].items():
                pwr_float = float(pwr)
                enrg = round(pwr_float * 1.0, 4)
                rec = TelemetryRecord.objects.create(
                    team=team,
                    appliance=app_name,
                    power_kw=pwr_float,
                    energy_kwh=enrg,
                    status=status_str,
                    notes=notes,
                    raw_payload=raw_str,
                    timestamp=now_dt,
                )
                records_saved.append({"id": rec.id, "appliance": app_name, "power_kw": pwr_float})
        else:
            appliance_name = body.get("appliance", "Appliance")
            pwr = float(body.get("power_kw", 0.0))
            enrg = float(body.get("energy_kwh", round(pwr * 1.0, 4)))
            rec = TelemetryRecord.objects.create(
                team=team,
                appliance=appliance_name,
                power_kw=pwr,
                energy_kwh=enrg,
                status=status_str,
                notes=notes,
                raw_payload=raw_str,
                timestamp=now_dt,
            )
            records_saved.append({"id": rec.id, "appliance": appliance_name, "power_kw": pwr})

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
            "power_kw": data["power_kw"],
            "energy_kwh": data["energy_kwh"],
            "status": data["status"],
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
        avg_kw = sum(a["power_kw"] for a in apps.values())
        lb.append({
            "team_name": t.team_name,
            "reports_count": cnt,
            "avg_power_kw": avg_kw,
            "last_active": t.last_connected_at.strftime("%H:%M:%S IST") if t.last_connected_at else "Never",
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
    writer.writerow(["id", "timestamp_ist", "team_name", "appliance", "power_kw", "energy_kwh", "status", "notes"])
    records = TelemetryRecord.objects.select_related("team").order_by("id")
    for r in records:
        writer.writerow([
            r.id,
            r.timestamp.strftime("%Y-%m-%d %H:%M:%S IST"),
            r.team.team_name,
            r.appliance,
            r.power_kw,
            r.energy_kwh,
            r.status,
            r.notes,
        ])
    response = HttpResponse(out.getvalue(), content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="competition_telemetry_all_teams.csv"'
    return response
