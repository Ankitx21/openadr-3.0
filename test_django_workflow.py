import asyncio
import os
import sys
import urllib.request
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "team_kit"))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "openadr_django.settings")
import django
django.setup()

from django.test.runner import DiscoverRunner
from vtn_hub.models import Team, TelemetryRecord
from team_kit.openadr3_client import TeamVen

def run_django_tests():
    print("[TEST] Running Django VTN Tests...")
    
    # 1. Verify 20 teams seeded
    teams_cnt = Team.objects.count()
    assert teams_cnt == 20, f"Expected 20 teams, got {teams_cnt}"
    print(f"[PASS] 1. 20 pre-authorized teams confirmed in Django ORM.")

    team1 = Team.objects.get(team_name="Team-01")
    assert team1.token == "KEY-TEAM01-A7F9"

    # 2. Test Telemetry Ingestion via ORM and Model methods
    rec1 = TelemetryRecord.objects.create(
        team=team1,
        appliance="Smart_AC",
        outdoor_temperature=32.5,
        energy=1.85,
        notes="Pre-cooling test",
        timestamp=django.utils.timezone.now()
    )
    rec2 = TelemetryRecord.objects.create(
        team=team1,
        appliance="Geyser",
        outdoor_temperature=31.0,
        energy=2.10,
        notes="Water heating test",
        timestamp=django.utils.timezone.now()
    )
    assert TelemetryRecord.objects.count() >= 2
    print("[PASS] 2. Telemetry records successfully stored via Django models.")

    # 3. Test get_latest_appliances()
    apps = team1.get_latest_appliances()
    assert "Smart_AC" in apps
    assert "Geyser" in apps
    assert apps["Smart_AC"]["energy"] == 1.85
    assert apps["Smart_AC"]["outdoor_temperature"] == 32.5
    assert apps["Geyser"]["energy"] == 2.10
    print(f"[PASS] 3. Team latest appliances retrieved: {list(apps.keys())}.")

    # 4. Test Organizer Authentication & Dashboard Access via Django test Client
    from django.test import Client
    from django.contrib.auth import get_user_model

    client = Client()

    # A. Unauthenticated access redirects to /login/?next=/
    resp_unauth = client.get("/")
    assert resp_unauth.status_code == 302
    assert "/login/" in resp_unauth.url

    # B. Login page renders successfully
    resp_login_page = client.get("/login/")
    assert resp_login_page.status_code == 200
    assert "Organizer Portal" in resp_login_page.content.decode("utf-8")

    # C. Invalid credentials return error
    resp_fail = client.post("/login/", {"username": "admin", "password": "wrongpassword"})
    assert resp_fail.status_code == 200
    assert "Invalid username or password" in resp_fail.content.decode("utf-8")

    # D. Ensure admin / admin123 exists and authenticate
    User = get_user_model()
    admin_user, _ = User.objects.get_or_create(username="admin", defaults={"email": "admin@openadr.local"})
    admin_user.set_password("admin123")
    admin_user.save()

    login_success = client.login(username="admin", password="admin123")
    assert login_success is True

    # E. Authenticated Dashboard GET returns 200
    resp = client.get("/")
    assert resp.status_code == 200
    content = resp.content.decode("utf-8")
    assert "Teams & Competition Progress" in content
    assert "Smart_AC" in content
    assert "Geyser" in content
    print("[PASS] 4. Organizer login authentication and Dashboard view verified.")

    # Tariffs API GET
    resp_tariffs = client.get(f"/api/v3/tariffs.json?token={team1.token}")
    assert resp_tariffs.status_code == 200
    tariffs_data = resp_tariffs.json()
    assert len(tariffs_data["tariffs"]) == 48
    print("[PASS] 5. 48-Hour IST Tariffs API verified.")

    # Appliances API GET
    resp_apps = client.get(f"/api/v3/appliances?token={team1.token}")
    assert resp_apps.status_code == 200
    apps_data = resp_apps.json()
    assert apps_data["count"] >= 2
    print("[PASS] 6. Appliances API endpoint verified.")

    # Telemetry Reporting POST
    post_payload = {
        "appliances": {
            "EV_Charger": 3.3,
            "Water_Heater": 1.2
        },
        "outdoor_temperature": 29.4,
        "notes": "Evening charge"
    }
    resp_post = client.post(
        f"/api/v3/reports?token={team1.token}",
        data=json.dumps(post_payload),
        content_type="application/json"
    )
    assert resp_post.status_code == 201
    assert resp_post.json()["status"] == "success"
    print("[PASS] 7. Multi-appliance telemetry ingestion verified.")

    # CSV Downloads
    resp_csv = client.get("/download/telemetry.csv")
    assert resp_csv.status_code == 200
    assert "outdoor_temperature_c" in resp_csv.content.decode("utf-8")
    assert "interval_15min_ist" in resp_csv.content.decode("utf-8")
    print("[PASS] 8. Telemetry CSV download verified.")

    # 9. Verify 15-minute Interval Capping & Hour Rollover
    from vtn_hub.models import snap_to_15min_interval
    from datetime import datetime, timezone, timedelta
    ist = timezone(timedelta(hours=5, minutes=30))
    
    # 7th minute -> 15th minute
    dt_7 = datetime(2026, 10, 26, 10, 7, 0, tzinfo=ist)
    snapped_7 = snap_to_15min_interval(dt_7)
    assert snapped_7.hour == 10 and snapped_7.minute == 15, f"Expected 10:15, got {snapped_7}"

    # 52nd minute -> 11:00 (safely rolls over 60 minutes into next hour)
    dt_52 = datetime(2026, 10, 26, 10, 52, 0, tzinfo=ist)
    snapped_52 = snap_to_15min_interval(dt_52)
    assert snapped_52.hour == 11 and snapped_52.minute == 0, f"Expected 11:00, got {snapped_52}"
    print("[PASS] 9. 15-minute interval capping & 60th minute rollover verified.")

    # 10. Verify Analytics Panel HTML View
    resp_analytics = client.get("/analytics/")
    assert resp_analytics.status_code == 200
    assert "OpenADR 3.0 Baseline & Prototype Data Analytics" in resp_analytics.content.decode("utf-8")
    assert "unifiedChart" in resp_analytics.content.decode("utf-8")
    print("[PASS] 10. Dedicated Analytics & Charts Panel view verified.")

    # 11. Verify 15-minute Interval Aggregation API for Charts (/api/v3/baseline/intervals)
    resp_intervals = client.get(f"/api/v3/baseline/intervals?team={team1.team_name}")
    assert resp_intervals.status_code == 200
    intervals_data = resp_intervals.json()
    assert intervals_data["status"] == "success"
    assert "total_energy_series" in intervals_data
    assert "series_by_appliance" in intervals_data
    print("[PASS] 11. 15-Minute interval charts API (/api/v3/baseline/intervals) verified.")

    # 12. Verify Phase 1 Testing API
    test_payload = {
        "appliances": {"Test_AC": 1.25},
        "outdoor_temperature": 31.5,
        "notes": "Student testing check"
    }
    resp_testing = client.post(
        f"/api/v3/testing?token={team1.token}",
        data=json.dumps(test_payload),
        content_type="application/json"
    )
    assert resp_testing.status_code == 201
    assert resp_testing.json()["status"] == "success"
    assert resp_testing.json()["phase"] == "testing"
    assert TelemetryRecord.objects.filter(team=team1, phase="testing").exists()
    print("[PASS] 12. Phase 1 Testing API verified (Recorded with phase='testing').")

    # 13. Verify Phase 2 Baseline API
    baseline_payload = {
        "appliances": {"Baseline_AC": 2.10},
        "outdoor_temperature": 32.0,
        "notes": "2-week baseline recording"
    }
    resp_base = client.post(
        f"/api/v3/baseline?token={team1.token}",
        data=json.dumps(baseline_payload),
        content_type="application/json"
    )
    assert resp_base.status_code == 201
    assert resp_base.json()["status"] == "success"
    assert resp_base.json()["phase"] == "baseline"
    assert TelemetryRecord.objects.filter(team=team1, phase="baseline", appliance="Baseline_AC").exists()
    print("[PASS] 13. Phase 2 Baseline API verified (Recorded with phase='baseline').")

    # 14. Verify Phase 3 Prototype API
    prototype_payload = {
        "appliances": {"Smart_Shifted_AC": 0.85},
        "outdoor_temperature": 32.0,
        "notes": "Tariff-optimized product load"
    }
    resp_proto = client.post(
        f"/api/v3/prototype?token={team1.token}",
        data=json.dumps(prototype_payload),
        content_type="application/json"
    )
    assert resp_proto.status_code == 201
    assert resp_proto.json()["status"] == "success"
    assert resp_proto.json()["phase"] == "prototype"
    assert TelemetryRecord.objects.filter(team=team1, phase="prototype", appliance="Smart_Shifted_AC").exists()
    print("[PASS] 14. Phase 3 Prototype API verified (Recorded with phase='prototype').")

    # 15. Verify Tariffs Fetching and Organizer Receipt Tracking
    from vtn_hub import views as vtn_views
    vtn_views.last_tariff_download.clear()
    resp_tariffs_clean = client.get(f"/api/v3/tariffs?token={team1.token}")
    assert resp_tariffs_clean.status_code == 200
    team1.refresh_from_db()
    assert team1.last_tariff_fetched_at is not None
    print("[PASS] 15. Rolling Tariffs API & team tariff receipt tracking verified.")

    # Clean up test artifacts so database remains completely clean (zero dummy records)
    TelemetryRecord.objects.all().delete()
    Team.objects.all().update(first_connected_at=None, last_connected_at=None, last_tariff_fetched_at=None)
    print("[CLEAN] Database cleanly restored to 0 telemetry records.")

    print("\n=======================================================")
    print(" ALL 15 DJANGO VTN TEST STEPS PASSED SUCCESSFULLY! ")
    print("=======================================================")

if __name__ == "__main__":
    run_django_tests()


