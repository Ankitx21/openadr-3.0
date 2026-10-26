"""OpenADR 3.0 Team VEN Client for Student Participants.

Features:
- Access Control: Authenticates using team's assigned API token.
- Direct JSON Tariffs: Fetches full 48-hour Day-Ahead electricity tariffs in JSON format.
- Multi-Appliance Support: Submits telemetry for single or multiple appliances.
- Zero-Crash: Uses 'requests' if available, otherwise seamlessly uses built-in Python 'urllib'.
- Two Modes: Interactive numbered menu (zero code) or importable Python class.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import urllib.parse
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

IST = timezone(timedelta(hours=5, minutes=30), name="IST")

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False
    import urllib.error
    import urllib.request


class TeamVen:
    """Participant Virtual End Node (VEN) connecting to Organizer VTN Server."""

    def __init__(
        self,
        token: str,
        server_url: str = "https://openadr-3-0.onrender.com",
        appliance_type: str = "Generic_Load",
    ):
        self.token = token.strip()
        self.server_url = server_url.rstrip("/")
        self.appliance_type = appliance_type
        self.headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Bearer {self.token}",
            "X-API-Key": self.token,
            "User-Agent": "OpenADR3-TeamVen/1.0",
        }

    def _http_get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """HTTP GET request with token query fallback."""
        all_params = {"token": self.token}
        if params:
            all_params.update(params)

        query = "&".join(f"{k}={urllib.parse.quote(str(v))}" for k, v in all_params.items())
        url = f"{self.server_url}{path}?{query}"

        if HAS_REQUESTS:
            resp = requests.get(url, headers=self.headers, timeout=15)
            if resp.status_code == 401:
                raise PermissionError("Authentication failed: Invalid team API token.")
            resp.raise_for_status()
            return resp.json()
        else:
            req = urllib.request.Request(url, headers=self.headers, method="GET")
            try:
                with urllib.request.urlopen(req, timeout=15) as r:
                    return json.loads(r.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                if e.code == 401:
                    raise PermissionError("Authentication failed: Invalid team API token.")
                raise

    def _http_post(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """HTTP POST request with token."""
        url = f"{self.server_url}{path}?token={urllib.parse.quote(self.token)}"
        data_bytes = json.dumps(payload).encode("utf-8")

        if HAS_REQUESTS:
            resp = requests.post(url, json=payload, headers=self.headers, timeout=15)
            if resp.status_code == 401:
                raise PermissionError("Authentication failed: Invalid team API token.")
            resp.raise_for_status()
            return resp.json()
        else:
            req = urllib.request.Request(url, data=data_bytes, headers=self.headers, method="POST")
            try:
                with urllib.request.urlopen(req, timeout=15) as r:
                    return json.loads(r.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                if e.code == 401:
                    raise PermissionError("Authentication failed: Invalid team API token.")
                raise

    def get_tariffs(self, save_local: bool = True) -> List[Dict[str, Any]]:
        """Fetch direct JSON 48-hour Day-Ahead electricity tariffs from VTN."""
        res = self._http_get("/api/v3/tariffs.json")
        tariffs = res.get("tariffs", [])

        if save_local and tariffs:
            self._save_tariffs(tariffs)

        return tariffs

    def _save_tariffs(self, tariffs: List[Dict[str, Any]], base_name: str = "tariffs_received") -> None:
        json_file = f"{base_name}.json"
        csv_file = f"{base_name}.csv"

        with open(json_file, "w", encoding="utf-8") as f:
            json.dump({
                "fetched_at": datetime.now(IST).isoformat(),
                "time_standard": "Asia/Kolkata (IST)",
                "total_intervals": len(tariffs),
                "tariffs": tariffs,
            }, f, indent=2)

        with open(csv_file, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["hour_index", "day", "start_time_ist", "duration_hours", "price_per_kwh", "currency"])
            for r in tariffs:
                writer.writerow([
                    r.get("hour_index", r.get("hour")),
                    r.get("day"),
                    r.get("start_time_ist", r.get("start_time")),
                    r.get("duration_hours"),
                    r.get("price_per_kwh"),
                    r.get("currency"),
                ])

        print(f"[SAVED] Direct tariffs (IST) saved:")
        print(f" -> CSV : {csv_file} (Open in Excel or Pandas)")
        print(f" -> JSON: {json_file}")

    def send_testing(
        self,
        energy: float = 1.5,
        outdoor_temperature: Optional[float] = 30.0,
        appliance: Optional[str] = None,
        notes: str = "Phase 1 Testing Data",
    ) -> Dict[str, Any]:
        """Phase 1 Testing: Submit verification data to /api/v3/testing."""
        app_name = appliance or self.appliance_type
        payload: Dict[str, Any] = {
            "appliance": app_name,
            "energy": float(energy),
            "notes": notes,
        }
        if outdoor_temperature is not None:
            payload["outdoor_temperature"] = float(outdoor_temperature)
        res = self._http_post("/api/v3/testing", payload)
        temp_str = f"{outdoor_temperature:.1f}°C" if outdoor_temperature is not None else "N/A"
        print(f"[PHASE 1 TESTING VERIFIED] {app_name}: {energy:.2f} kWh | Temp: {temp_str} -> Recorded for reference.")
        return res

    def send_baseline(
        self,
        energy: float,
        outdoor_temperature: Optional[float] = None,
        appliance: Optional[str] = None,
        notes: str = "",
    ) -> Dict[str, Any]:
        """Phase 2 Baseline: Submit 15-minute interval competition telemetry to /api/v3/baseline."""
        app_name = appliance or self.appliance_type
        payload: Dict[str, Any] = {
            "appliance": app_name,
            "energy": float(energy),
            "notes": notes,
        }
        if outdoor_temperature is not None:
            payload["outdoor_temperature"] = float(outdoor_temperature)
        res = self._http_post("/api/v3/baseline", payload)
        temp_str = f"{outdoor_temperature:.1f}°C" if outdoor_temperature is not None else "N/A"
        slot_str = f" [15-Min Slot: {res.get('interval_ist')}]" if res.get("interval_ist") else ""
        print(f"[PHASE 2 BASELINE RECORDED] {app_name}: {energy:.2f} kWh | Temp: {temp_str}{slot_str}")
        return res

    def send_prototype(
        self,
        energy: float,
        outdoor_temperature: Optional[float] = None,
        appliance: Optional[str] = None,
        notes: str = "Phase 3 Smart Tariff Optimization",
    ) -> Dict[str, Any]:
        """Phase 3 Prototype: Submit smart tariff-optimized load data to /api/v3/prototype."""
        app_name = appliance or self.appliance_type
        payload: Dict[str, Any] = {
            "appliance": app_name,
            "energy": float(energy),
            "notes": notes,
        }
        if outdoor_temperature is not None:
            payload["outdoor_temperature"] = float(outdoor_temperature)
        res = self._http_post("/api/v3/prototype", payload)
        temp_str = f"{outdoor_temperature:.1f}°C" if outdoor_temperature is not None else "N/A"
        slot_str = f" [15-Min Slot: {res.get('interval_ist')}]" if res.get("interval_ist") else ""
        print(f"[PHASE 3 PROTOTYPE RECORDED] {app_name}: {energy:.2f} kWh | Temp: {temp_str}{slot_str}")
        return res

    def send_baseline_multi(
        self,
        appliances: Dict[str, float],
        outdoor_temperature: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Phase 2: Submit multi-appliance baseline dictionary to /api/v3/baseline."""
        payload: Dict[str, Any] = {"appliances": appliances}
        if outdoor_temperature is not None:
            payload["outdoor_temperature"] = float(outdoor_temperature)
        res = self._http_post("/api/v3/baseline", payload)
        slot_str = f" [15-Min Slot: {res.get('interval_ist')}]" if res.get("interval_ist") else ""
        print(f"[PHASE 2 BASELINE RECORDED] {len(appliances)} appliances updated{slot_str}")
        return res

    def report_multi_appliances(
        self,
        appliances: Dict[str, float],
        outdoor_temperature: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Backward-compatible alias: forwards to send_baseline_multi."""
        return self.send_baseline_multi(appliances=appliances, outdoor_temperature=outdoor_temperature)

    def report_telemetry(
        self,
        energy: float,
        outdoor_temperature: Optional[float] = None,
        appliance: Optional[str] = None,
        timestamp: Optional[str] = None,
        notes: str = "",
    ) -> Dict[str, Any]:
        """Backward-compatible alias: forwards to send_baseline."""
        return self.send_baseline(
            energy=energy,
            outdoor_temperature=outdoor_temperature,
            appliance=appliance,
            notes=notes,
        )

    def test_connection(
        self,
        appliance: str = "Test_Load",
        energy: float = 1.5,
        outdoor_temperature: float = 30.0,
    ) -> Dict[str, Any]:
        """Alias for send_testing."""
        return self.send_testing(
            energy=energy,
            outdoor_temperature=outdoor_temperature,
            appliance=appliance,
        )


def interactive_menu(server_url: str):
    print("=" * 65)
    print(" OPENADR 3.0 PARTICIPANT CLIENT")
    print("=" * 65)

    token = input("Enter your assigned Team API Token (e.g. KEY-TEAM01-A7F9): ").strip()
    if not token:
        print("Invalid token.")
        return

    ven = TeamVen(token=token, server_url=server_url)

    # Pre-flight token verification against VTN server
    try:
        auth_info = ven.verify_token()
        team_name = auth_info.get("team_name", "Team")
    except Exception:
        print("Invalid token.")
        return

    # ONLY IF AUTHENTICATED: Display options and registered devices
    while True:
        reg_apps = ven.get_registered_appliances()
        print("\n" + "=" * 55)
        print(f"  TEAM PORTAL: {team_name}")
        if reg_apps:
            print("  REGISTERED APPLIANCES:")
            for a in reg_apps:
                temp_val = a.get("outdoor_temperature")
                temp_disp = f"{temp_val}°C" if temp_val is not None else "N/A"
                print(f"    - {a['appliance']:<20} -> {a.get('energy', 0.0):.2f} kWh (Temp: {temp_disp})")
        else:
            print("  REGISTERED APPLIANCES: None yet (Send your first baseline entry below)")
        print("-" * 55)
        print("  SELECT AN ACTION:")
        print("  1. Fetch Dynamic Tariffs (Continuous 48-Hour Day-Ahead Prices - JSON & CSV)")
        print("  2. Phase 1: Send Testing Data (Verify Connection & Data Sending)")
        print("  3. Phase 2: Send Baseline Data (2-Week Active Period - 15-Min Intervals)")
        print("  4. Phase 3: Post Prototype / Product Data (Optimized Product Load)")
        print("  5. Send Multi-Appliance Baseline Data")
        print("  6. View Live Standings")
        print("  7. Exit")
        print("=" * 55)

        choice = input("Enter choice (1-7): ").strip()

        if choice == "1":
            try:
                tariffs = ven.get_tariffs(save_local=True)
                print(f"\n[SUCCESS] Retrieved {len(tariffs)} hourly intervals.")
            except Exception as ex:
                print(f"[ERROR] {ex}")

        elif choice == "2":
            app = input("Enter test appliance name [Default: Test_Appliance]: ").strip() or "Test_Appliance"
            enrg_str = input("Enter test energy in kWh [Default: 1.5]: ").strip() or "1.5"
            temp_str = input("Enter test outdoor temperature in °C [Default: 30.0]: ").strip() or "30.0"
            try:
                ven.send_testing(energy=float(enrg_str), outdoor_temperature=float(temp_str), appliance=app)
            except Exception as ex:
                print(f"[ERROR] {ex}")

        elif choice == "3":
            reg_apps = ven.get_registered_appliances()
            app = ""
            if reg_apps:
                print("\nYour Registered Appliances:")
                for idx, a in enumerate(reg_apps, start=1):
                    print(f"  [{idx}] {a['appliance']} (Current: {a.get('energy', 0.0):.2f} kWh)")
                new_idx = len(reg_apps) + 1
                print(f"  [{new_idx}] Register a new appliance...")
                sel = input(f"Select appliance [1-{new_idx}]: ").strip()
                try:
                    sel_num = int(sel)
                    if 1 <= sel_num <= len(reg_apps):
                        app = reg_apps[sel_num - 1]["appliance"]
                    elif sel_num == new_idx:
                        app = input("Enter new Appliance Name (e.g. EV_Charger, Geyser): ").strip()
                except ValueError:
                    print("Invalid selection.")
                    continue
            else:
                app = input("Enter Appliance Name (e.g. Smart_AC, Geyser, EV) [Default: Smart_AC]: ").strip() or "Smart_AC"

            if not app:
                print("Appliance name cannot be empty.")
                continue

            enrg_str = input(f"Enter energy consumption for {app} in kWh: ").strip()
            try:
                enrg = float(enrg_str)
                temp_str = input("Enter outdoor temperature in °C (optional, press Enter to skip): ").strip()
                outdoor_temp = float(temp_str) if temp_str else None
                notes = input("Optional note (press Enter to skip): ").strip()
                ven.send_baseline(energy=enrg, outdoor_temperature=outdoor_temp, appliance=app, notes=notes)
            except ValueError:
                print("[ERROR] Invalid number format.")
            except Exception as ex:
                print(f"[ERROR] {ex}")

        elif choice == "4":
            app = input("Enter Prototype / Product appliance name [Default: Smart_AC]: ").strip() or "Smart_AC"
            enrg_str = input("Enter optimized energy load in kWh: ").strip()
            try:
                enrg = float(enrg_str)
                temp_str = input("Enter outdoor temperature in °C (optional, press Enter to skip): ").strip()
                outdoor_temp = float(temp_str) if temp_str else None
                notes = input("Optimization note (e.g. Shifted load off peak) [Default: Smart Optimization]: ").strip() or "Smart Optimization"
                ven.send_prototype(energy=enrg, outdoor_temperature=outdoor_temp, appliance=app, notes=notes)
            except ValueError:
                print("[ERROR] Invalid number format.")
            except Exception as ex:
                print(f"[ERROR] {ex}")

        elif choice == "5":
            reg_apps = ven.get_registered_appliances()
            apps = {}
            if reg_apps:
                use_existing = input(f"Update all {len(reg_apps)} registered appliances? (Y/n): ").strip().lower()
                if use_existing != "n":
                    for a in reg_apps:
                        curr_name = a["appliance"]
                        curr_enrg = a.get("energy", 0.0)
                        p_str = input(f"Energy for {curr_name} (kWh) [Enter to keep {curr_enrg:.2f}]: ").strip()
                        if p_str:
                            try:
                                apps[curr_name] = float(p_str)
                            except ValueError:
                                print(f"Invalid input, keeping {curr_enrg:.2f} kWh.")
                                apps[curr_name] = curr_enrg
                        else:
                            apps[curr_name] = curr_enrg

            while True:
                prompt_text = "Add another appliance name (or press Enter to submit): " if apps else "Appliance name (or 'done'): "
                name = input(prompt_text).strip()
                if not name or name.lower() == "done":
                    break
                p_str = input(f"Energy for {name} (kWh): ").strip()
                try:
                    apps[name] = float(p_str)
                except ValueError:
                    print("Invalid energy reading, skipping.")

            if apps:
                temp_str = input("Enter outdoor temperature in °C (optional, press Enter to skip): ").strip()
                outdoor_temp = float(temp_str) if temp_str else None
                try:
                    ven.send_baseline_multi(apps, outdoor_temperature=outdoor_temp)
                except Exception as ex:
                    print(f"[ERROR] {ex}")
            else:
                print("No readings entered.")

        elif choice == "6":
            try:
                lb = ven.get_leaderboard()
                print("\nLIVE COMPETITION STANDINGS:")
                print(f"{'Rank':<6}{'Team Name':<22}{'Baseline Data':<18}{'Prototype Data':<18}{'Appliances':<12}")
                print("-" * 76)
                for idx, t in enumerate(lb.get("leaderboard", []), start=1):
                    b_cnt = t.get("baseline_count", t.get("reports_count", 0))
                    p_cnt = t.get("prototype_count", 0)
                    a_cnt = t.get("active_appliances_count", 0)
                    print(f"{idx:<6}{t.get('team_name'):<22}{b_cnt:<18}{p_cnt:<18}{a_cnt:<12}")
            except Exception as ex:
                print(f"[ERROR] {ex}")

        elif choice == "7":
            print("\nExiting. Good luck with your grid-interactive research!")
            break


def main():
    parser = argparse.ArgumentParser(description="OpenADR 3.0 VEN Client")
    parser.add_argument("--token", default="", help="Your assigned team API token (e.g. KEY-TEAM01-A7F9)")
    parser.add_argument("--server", default="https://openadr-3-0.onrender.com", help="Organizer VTN server URL")
    parser.add_argument("--fetch-tariffs", action="store_true", help="Download 48-Hour Day-Ahead Dynamic Tariffs (JSON & CSV)")
    parser.add_argument("--test", "--test-connection", dest="test_mode", action="store_true", help="Phase 1: Send testing data to /api/v3/testing")
    parser.add_argument("--baseline", type=float, default=None, help="Phase 2: Send baseline energy reading in kWh to /api/v3/baseline")
    parser.add_argument("--prototype", type=float, default=None, help="Phase 3: Post prototype load reading in kWh to /api/v3/prototype")
    parser.add_argument("--energy", type=float, default=None, help="Energy reading in kWh (defaults to Phase 2 baseline)")
    parser.add_argument("--outdoor-temp", type=float, default=None, help="Outdoor temperature in °C")
    parser.add_argument("--appliance", default="Smart_AC", help="Appliance name")
    parser.add_argument("--list-appliances", action="store_true", help="List registered appliances for this team")
    args = parser.parse_args()

    if (
        not args.token
        and args.baseline is None
        and args.prototype is None
        and args.energy is None
        and not args.fetch_tariffs
        and not args.list_appliances
        and not args.test_mode
    ):
        interactive_menu(args.server)
        return

    if not args.token:
        print("Invalid token.")
        return

    ven = TeamVen(token=args.token, server_url=args.server, appliance_type=args.appliance)

    try:
        ven.verify_token()
    except PermissionError:
        print("Invalid token.")
        return
    except Exception as ex:
        print(f"Connection error: {ex}")
        return

    if args.test_mode:
        enrg = args.energy if args.energy is not None else 1.5
        ven.send_testing(energy=enrg, outdoor_temperature=args.outdoor_temp or 30.0, appliance=args.appliance)
        return

    if args.fetch_tariffs:
        ven.get_tariffs(save_local=True)

    if args.list_appliances:
        apps = ven.get_registered_appliances()
        if apps:
            print("\nRegistered Appliances:")
            for a in apps:
                temp_str = f"{a.get('outdoor_temperature')}°C" if a.get('outdoor_temperature') is not None else "N/A"
                print(f"  - {a['appliance']:<20}: {a.get('energy', 0.0):.2f} kWh (Temp: {temp_str})")
        else:
            print("No appliances reported yet.")

    if args.baseline is not None:
        ven.send_baseline(energy=args.baseline, outdoor_temperature=args.outdoor_temp, appliance=args.appliance)
    elif args.prototype is not None:
        ven.send_prototype(energy=args.prototype, outdoor_temperature=args.outdoor_temp, appliance=args.appliance)
    elif args.energy is not None:
        ven.send_baseline(energy=args.energy, outdoor_temperature=args.outdoor_temp, appliance=args.appliance)


if __name__ == "__main__":
    main()
