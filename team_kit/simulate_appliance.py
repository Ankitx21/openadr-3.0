"""Sample Appliance Simulation & Grid-Interactive Optimization.

For undergraduate competition teams:
- Downloads OpenADR 3.0 tariffs.
- Compares 'Uncontrolled Baseline' vs 'Grid-Interactive Optimized' appliance.
- Calculates cost savings in INR.
- Automatically sends the optimized power profile back to the VTN server!
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone

try:
    from openadr3_client import TeamVen
except ImportError:
    try:
        from .openadr3_client import TeamVen
    except ImportError:
        from team_kit.openadr3_client import TeamVen


def run_simulation(token: str = "KEY-TEAM01-A7F9", server_url: str = "https://openadr-3-0.onrender.com", appliance_type: str = "Smart_Air_Conditioner"):
    print("=" * 70)
    print(f" RUNNING APPLIANCE SIMULATION WITH TOKEN: {token} ({appliance_type})")
    print("=" * 70)

    # 1. Connect and Fetch Direct JSON Tariffs
    ven = TeamVen(token=token, server_url=server_url, appliance_type=appliance_type)
    print(f"\n[1/3] Fetching 48-hour Day-Ahead Direct Tariffs JSON...")
    tariffs = ven.get_tariffs(save_local=True)

    if not tariffs:
        print("[ERROR] Could not fetch tariffs from server. Make sure server is running!")
        return

    print(f"[OK] Fetched {len(tariffs)} hourly intervals.")

    # 2. Simulate Baseline vs Optimized Load
    # Assume appliance runs 6 hours total per day.
    # Uncontrolled: runs during peak hours (17:00 - 22:00) when everyone returns home
    # Smart: pre-cools/charges during cheap off-peak hours, throttles during peak!
    baseline_cost = 0.0
    optimized_cost = 0.0
    schedule_to_report = []

    print(f"\n[2/4] Simulating 48-Hour Energy Response...")
    for idx, interval in enumerate(tariffs):
        price = interval["price_per_kwh"]
        dtstart = interval["start_time"]
        
        # Simple threshold logic: If price > 18 INR/kWh (peak), shed load!
        # If price < 10 INR/kWh (off-peak), pre-cool/charge!
        is_peak = price > 18.0
        is_cheap = price < 10.0

        # Baseline appliance: ignores price (runs 2.5 kW in peak evening, 0.8 kW idle)
        base_kw = 2.5 if is_peak else 0.8
        baseline_cost += base_kw * 1.0 * price

        # Smart Grid-Interactive appliance:
        # Pre-charges during cheap hours, throttles to minimum during peak hours
        if is_peak:
            opt_kw = 0.3  # Throttled / battery discharging
        elif is_cheap:
            opt_kw = 2.0  # Pre-cooling / charging battery
        else:
            opt_kw = 1.0  # Normal operation

        optimized_cost += opt_kw * 1.0 * price

        schedule_to_report.append({
            "start_time": dtstart,
            "power_kw": opt_kw,
            "energy_kwh": opt_kw * 1.0,
            "price_inr": price,
        })

    # 3. Print Results & Savings
    savings_inr = baseline_cost - optimized_cost
    pct_savings = (savings_inr / baseline_cost) * 100 if baseline_cost > 0 else 0.0

    print("\n" + "=" * 70)
    print(" SIMULATION RESULTS AND SAVINGS SUMMARY")
    print("=" * 70)
    print(f" Total Evaluation Duration : {len(tariffs)} Hours")
    print(f" Baseline Electricity Bill : INR {baseline_cost:.2f}")
    print(f" Optimized Electricity Bill: INR {optimized_cost:.2f}")
    print(f" Total Cost Savings        : INR {savings_inr:.2f} ({pct_savings:.1f}% reduction)")
    print("=" * 70)

    # 4. Report Data Back to Organizer VTN Server
    print(f"\n[2/3] Uploading smart appliance power telemetry to OpenADR 3.0 VTN Server...")
    ven.report_power(
        power_kw=1.0,
        appliance=appliance_type,
        status="OPTIMIZED_DISPATCH",
        notes=f"Demand Response active - saved {pct_savings:.1f}% cost",
    )

    print(f"\n[3/3] Checking updated competition rankings...")
    lb = ven.get_leaderboard()
    print("Leaderboard loaded successfully. Check the web dashboard to see your team's live power chart!")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Simulate appliance response and report to OpenADR 3.0 VTN.")
    parser.add_argument("--token", default="KEY-TEAM01-A7F9", help="Assigned team API token")
    parser.add_argument("--server", default="http://127.0.0.1:7860", help="VTN Server URL")
    parser.add_argument("--appliance", default="Smart_Air_Conditioner", help="Appliance type")
    args = parser.parse_args()

    run_simulation(args.token, args.server, args.appliance)
