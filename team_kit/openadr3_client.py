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
        server_url: str = "http://127.0.0.1:7860",
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

    def report_power(
        self,
        power_kw: float,
        energy_kwh: Optional[float] = None,
        appliance: Optional[str] = None,
        status: str = "RUNNING",
        notes: str = "",
    ) -> Dict[str, Any]:
        """Submit single appliance power reading to VTN."""
        app_name = appliance or self.appliance_type
        payload = {
            "appliance": app_name,
            "power_kw": float(power_kw),
            "energy_kwh": float(energy_kwh) if energy_kwh is not None else round(float(power_kw) * 1.0, 4),
            "status": status,
            "notes": notes,
        }
        res = self._http_post("/api/v3/reports", payload)
        print(f"[REPORTED] {app_name}: {power_kw:.2f} kW | Status: {status}")
        return res

    def report_multi_appliances(
        self,
        appliances_dict: Dict[str, float],
        status: str = "RUNNING",
        notes: str = "",
    ) -> Dict[str, Any]:
        """Submit multi-appliance breakdown simultaneously to VTN.

        Example: {'Smart_AC': 1.8, 'EV_Charger': 3.3, 'Water_Heater': 0.9}
        """
        payload = {
            "appliances": appliances_dict,
            "status": status,
            "notes": notes,
        }
        res = self._http_post("/api/v3/reports", payload)
        print(f"[MULTI-REPORTED] Submitted {len(appliances_dict)} appliance readings to VTN.")
        return res

    def get_registered_appliances(self) -> List[Dict[str, Any]]:
        """Fetch all appliances previously reported/registered by this team."""
        try:
            res = self._http_get("/api/v3/appliances")
            return res.get("appliances", [])
        except Exception as ex:
            if "404" in str(ex):
                print("[NOTE] Server was started before this feature was added. Please restart server to sync registered devices.")
            return []

    def get_leaderboard(self) -> Dict[str, Any]:
        """Fetch live competition rankings from VTN."""
        return self._http_get("/api/v3/leaderboard")

    def verify_token(self) -> Dict[str, Any]:
        """Verify team key with VTN by requesting tariff metadata."""
        res = self._http_get("/api/v3/tariffs.json")
        return {"team_name": res.get("team_authenticated", "Team")}


def interactive_menu(server_url: str):
    print("=" * 65)
    print(" OPENADR 3.0 COMPETITION PARTICIPANT CLIENT (VEN)")
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

    # ONLY IF AUTHENTICATED: Display competition options and registered devices
    while True:
        reg_apps = ven.get_registered_appliances()
        print("\n" + "=" * 50)
        print(f"  TEAM PORTAL: {team_name}")
        if reg_apps:
            print("  REGISTERED APPLIANCES:")
            for a in reg_apps:
                print(f"    - {a['appliance']:<20} -> {a['power_kw']:.2f} kW ({a.get('status', 'RUNNING')})")
        else:
            print("  REGISTERED APPLIANCES: None yet (Report your first device below)")
        print("-" * 50)
        print("  SELECT AN ACTION:")
        print("  1. Download 48-Hour Day-Ahead Tariffs (Direct JSON & CSV)")
        print("  2. Update / Report Single Appliance Power Reading (kW)")
        print("  3. Update / Report Multi-Appliance Breakdown")
        print("  4. View Live Competition Leaderboard")
        print("  5. Exit")
        print("=" * 50)

        choice = input("Enter choice (1-5): ").strip()

        if choice == "1":
            try:
                tariffs = ven.get_tariffs(save_local=True)
                print(f"\n[SUCCESS] Retrieved {len(tariffs)} hourly intervals.")
            except Exception as ex:
                print(f"[ERROR] {ex}")

        elif choice == "2":
            reg_apps = ven.get_registered_appliances()
            app = ""
            if reg_apps:
                print("\nYour Registered Appliances:")
                for idx, a in enumerate(reg_apps, start=1):
                    print(f"  [{idx}] {a['appliance']} (Current: {a['power_kw']:.2f} kW)")
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

            pwr_str = input(f"Enter instantaneous power consumption for {app} in kW: ").strip()
            try:
                pwr = float(pwr_str)
                notes = input("Optional note (e.g. Peak shaving active): ").strip()
                ven.report_power(power_kw=pwr, appliance=app, notes=notes)
            except ValueError:
                print("[ERROR] Invalid number for power.")
            except Exception as ex:
                print(f"[ERROR] {ex}")

        elif choice == "3":
            reg_apps = ven.get_registered_appliances()
            apps = {}
            if reg_apps:
                use_existing = input(f"Update all {len(reg_apps)} registered appliances? (Y/n): ").strip().lower()
                if use_existing != "n":
                    for a in reg_apps:
                        curr_name = a["appliance"]
                        curr_pwr = a["power_kw"]
                        p_str = input(f"Power for {curr_name} (kW) [Enter to keep {curr_pwr:.2f}]: ").strip()
                        if p_str:
                            try:
                                apps[curr_name] = float(p_str)
                            except ValueError:
                                print(f"Invalid input, keeping {curr_pwr:.2f} kW.")
                                apps[curr_name] = curr_pwr
                        else:
                            apps[curr_name] = curr_pwr

            # Option to add more appliances
            while True:
                prompt_text = "Add another appliance name (or press Enter to submit): " if apps else "Appliance name (or 'done'): "
                name = input(prompt_text).strip()
                if not name or name.lower() == "done":
                    break
                p_str = input(f"Power for {name} (kW): ").strip()
                try:
                    apps[name] = float(p_str)
                except ValueError:
                    print("Invalid power reading, skipping.")

            if apps:
                try:
                    ven.report_multi_appliances(apps)
                except Exception as ex:
                    print(f"[ERROR] {ex}")
            else:
                print("No readings entered.")

        elif choice == "4":
            try:
                lb = ven.get_leaderboard()
                print("\nLIVE LEADERBOARD STANDINGS:")
                print(f"{'Rank':<6}{'Team Name':<22}{'Reports':<10}{'Avg kW':<10}")
                print("-" * 50)
                for idx, t in enumerate(lb.get("leaderboard", []), start=1):
                    print(f"{idx:<6}{t.get('team_name'):<22}{t.get('reports_count'):<10}{t.get('avg_power_kw', 0.0):<10.2f}")
            except Exception as ex:
                print(f"[ERROR] {ex}")

        elif choice == "5":
            print("\nExiting. Good luck with your grid-interactive research!")
            break


def main():
    parser = argparse.ArgumentParser(description="OpenADR 3.0 Team VEN Client")
    parser.add_argument("--token", default="", help="Your assigned team API token (e.g. KEY-TEAM01-A7F9)")
    parser.add_argument("--server", default="http://127.0.0.1:7860", help="Organizer VTN server URL")
    parser.add_argument("--fetch-tariffs", action="store_true", help="Download direct JSON and CSV tariffs")
    parser.add_argument("--report-power", type=float, default=None, help="Report power reading in kW")
    parser.add_argument("--appliance", default="Smart_AC", help="Appliance name")
    parser.add_argument("--list-appliances", action="store_true", help="List all registered appliances for this team")
    args = parser.parse_args()

    if not args.token and args.report_power is None and not args.fetch_tariffs and not args.list_appliances:
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

    if args.list_appliances:
        apps = ven.get_registered_appliances()
        if apps:
            print(f"\nRegistered Appliances for Team:")
            for a in apps:
                print(f"  - {a['appliance']:<20}: {a['power_kw']:.2f} kW ({a.get('status', 'RUNNING')}) [Last: {a.get('timestamp')}]")
        else:
            print("No appliances reported yet.")

    if args.fetch_tariffs:
        ven.get_tariffs(save_local=True)

    if args.report_power is not None:
        ven.report_power(power_kw=args.report_power, appliance=args.appliance)


if __name__ == "__main__":
    main()
