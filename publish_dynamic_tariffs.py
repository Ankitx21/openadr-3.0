"""Dynamic Time-of-Use Tariff Generator & Publisher.

Generates 48-hour (Today + Tomorrow) dynamic Indian electricity tariffs
using a random seed with prices bounded between INR 8.00 and 20.00 / kWh.
Pushes the generated schedule to the OpenADR 3.0 VTN server so participants
can query and download it.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import random
import sys
import urllib.parse
import urllib.request
from datetime import datetime, time as dt_time, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

IST = timezone(timedelta(hours=5, minutes=30), name="IST")


def generate_tariffs(
    seed: Optional[int] = None,
    min_price: float = 8.00,
    max_price: float = 20.00,
) -> Tuple[List[Dict[str, Any]], int]:
    """Generate unpredictable 48-hour Time-of-Use tariffs within [min_price, max_price]."""
    if seed is None:
        seed = random.randint(10000, 999999)

    rng = random.Random(seed)

    now = datetime.now(IST).replace(microsecond=0)
    today_start = datetime.combine(now.date(), dt_time.min, tzinfo=IST)
    points: List[Dict[str, Any]] = []

    for day_label, day_offset in [("Today", 0), ("Tomorrow_DayAhead", 1)]:
        base_day_start = today_start + timedelta(days=day_offset)

        # Unpredictable day-level drift (+- 0.8 INR)
        day_drift = rng.uniform(-0.8, 0.8)

        # 1 random surprise spike hour (evening grid stress) and 1 dip hour (solar/wind surge)
        spike_hour = rng.randint(16, 21)
        dip_hour = rng.randint(2, 14)

        for h in range(24):
            dt = base_day_start + timedelta(hours=h)
            hour_index = day_offset * 24 + h

            # Time-of-Use base contours with organic random jitter
            if 0 <= h < 6:
                # Night off-peak (~8.5 - 10.5)
                base = 9.2 + rng.uniform(-1.0, 1.2) + day_drift
            elif 6 <= h < 9:
                # Morning wake ramp (~11.5 - 14.5)
                base = 12.8 + rng.uniform(-1.2, 1.6) + day_drift
            elif 9 <= h < 12:
                # Morning commercial peak (~14.0 - 17.0)
                base = 15.2 + rng.uniform(-1.2, 1.8) + day_drift
            elif 12 <= h < 16:
                # Midday solar surge dip (~9.5 - 12.8)
                base = 11.0 + rng.uniform(-1.5, 1.6) + day_drift
            elif 16 <= h < 22:
                # Evening super peak (~16.5 - 19.5)
                base = 17.8 + rng.uniform(-1.0, 1.8) + day_drift
            else:
                # Late night ramp down (~10.0 - 13.0)
                base = 11.5 + rng.uniform(-1.2, 1.4) + day_drift

            # Grid shocks
            if h == spike_hour:
                base += rng.uniform(1.8, 3.2)
            elif h == dip_hour:
                base -= rng.uniform(1.5, 2.5)

            # Strictly enforce the [min_price, max_price] range
            price = round(max(min_price, min(max_price, base)), 2)

            points.append({
                "hour": hour_index,
                "hour_of_day": h,
                "day": day_label,
                "start_time": dt.isoformat(),
                "start_time_ist": dt.strftime("%Y-%m-%d %H:%M IST"),
                "duration_hours": 1,
                "price_per_kwh": price,
                "currency": "INR",
                "seed_used": seed,
            })

    return points, seed


def push_to_vtn_server(
    server_url: str,
    token: str,
    tariffs: List[Dict[str, Any]],
    seed: int,
    min_price: float,
    max_price: float,
) -> Dict[str, Any]:
    """Push generated tariff schedule to the VTN server endpoint."""
    endpoint = f"{server_url.rstrip('/')}/api/v3/tariffs/publish?token={urllib.parse.quote(token)}"
    payload = {
        "seed": seed,
        "min_price": min_price,
        "max_price": max_price,
        "tariffs": tariffs,
    }
    data_bytes = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        endpoint,
        data=data_bytes,
        headers={"Content-Type": "application/json", "User-Agent": "OpenADR3-TariffPublisher/1.0"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def save_local_files(tariffs: List[Dict[str, Any]], seed: int) -> None:
    """Save backup JSON and CSV files locally."""
    json_path = "published_tariffs.json"
    csv_path = "published_tariffs.csv"

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump({
            "published_at": datetime.now(IST).isoformat(),
            "seed_used": seed,
            "total_intervals": len(tariffs),
            "tariffs": tariffs,
        }, f, indent=2)

    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["hour", "day", "start_time_ist", "duration_hours", "price_per_kwh", "currency"])
        for t in tariffs:
            writer.writerow([
                t["hour"],
                t["day"],
                t["start_time_ist"],
                t["duration_hours"],
                f"{t['price_per_kwh']:.2f}",
                t["currency"],
            ])

    print(f"[SAVED] Local copies created:")
    print(f" -> CSV : {csv_path}")
    print(f" -> JSON: {json_path}")


def main():
    parser = argparse.ArgumentParser(description="Generate & Push Dynamic Time-of-Use Tariffs (OpenADR 3.0)")
    parser.add_argument("--seed", type=int, default=None, help="Integer random seed (if omitted, a random seed is selected)")
    parser.add_argument("--min-price", type=float, default=8.0, help="Minimum price in INR/kWh (default: 8.0)")
    parser.add_argument("--max-price", type=float, default=20.0, help="Maximum price in INR/kWh (default: 20.0)")
    parser.add_argument("--server", default="https://openadr-3-0.onrender.com", help="VTN server URL (e.g. http://127.0.0.1:8000)")
    parser.add_argument("--token", default="KEY-TEAM01-A7F9", help="Authorized API token to publish tariffs")
    parser.add_argument("--dry-run", action="store_true", help="Generate and inspect locally without pushing to VTN")
    args = parser.parse_args()

    print("=" * 65)
    print(" OPENADR 3.0 DYNAMIC TIME-OF-USE TARIFF PUBLISHER")
    print("=" * 65)

    tariffs, seed_used = generate_tariffs(
        seed=args.seed,
        min_price=args.min_price,
        max_price=args.max_price,
    )

    prices = [t["price_per_kwh"] for t in tariffs]
    min_p = min(prices)
    max_p = max(prices)
    avg_p = sum(prices) / len(prices)

    print(f" Random Seed Used : {seed_used}")
    print(f" Total Intervals  : {len(tariffs)} Hours (24h Today + 24h Tomorrow)")
    print(f" Price Bounds     : INR {min_p:.2f} - INR {max_p:.2f} / kWh (Target: [{args.min_price:.2f}, {args.max_price:.2f}])")
    print(f" Average Price    : INR {avg_p:.2f} / kWh")
    print("-" * 65)

    # Display 6-hour interval preview
    print(" SAMPLE INTERVAL PREVIEW:")
    sample_indices = [0, 4, 8, 13, 18, 21, 24, 28, 32, 37, 42, 45]
    for idx in sample_indices:
        t = tariffs[idx]
        print(f"  Hour {t['hour']:<2} ({t['day']:<19}) {t['start_time_ist']} -> INR {t['price_per_kwh']:>5.2f} / kWh")
    print("-" * 65)

    save_local_files(tariffs, seed_used)

    if args.dry_run:
        print("\n[DRY RUN] Tariffs generated and saved locally. Skipping server push.")
        return

    print(f"\n[PUSHING] Uploading 48-hour schedule to VTN: {args.server} ...")
    try:
        resp = push_to_vtn_server(
            server_url=args.server,
            token=args.token,
            tariffs=tariffs,
            seed=seed_used,
            min_price=args.min_price,
            max_price=args.max_price,
        )
        print(f"[SUCCESS] Server response: {resp.get('message', 'Published')}")
        print(f"All participant teams will now receive this dynamic tariff when querying /api/v3/tariffs.json")
    except Exception as ex:
        print(f"[WARNING] Could not push to server ({ex}).")
        print("Note: If the server is local, ensure 'python manage.py runserver' is running.")


if __name__ == "__main__":
    main()
