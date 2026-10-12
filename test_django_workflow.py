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

    # 4. Test View Render via Django test Client
    from django.test import Client
    client = Client()

    # Dashboard GET
    resp = client.get("/")
    assert resp.status_code == 200
    content = resp.content.decode("utf-8")
    assert "Teams & Registered Appliances" in content
    assert "Smart_AC" in content
    assert "Geyser" in content
    print("[PASS] 4. Dashboard rendered successfully with updated schema.")

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
    print("[PASS] 8. Telemetry CSV download verified.")

    print("\n=======================================================")
    print(" ALL 8 DJANGO VTN TEST STEPS PASSED SUCCESSFULLY! ")
    print("=======================================================")

if __name__ == "__main__":
    run_django_tests()
