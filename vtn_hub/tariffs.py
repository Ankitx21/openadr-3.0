from __future__ import annotations

import random
import math
from datetime import datetime, time as dt_time, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from django.db import transaction
from django.core.cache import cache
from .models import TariffInterval

IST = timezone(timedelta(hours=5, minutes=30), name="IST")


def generate_dynamic_tariffs(
    seed: Optional[int] = None,
    min_price: float = 8.00,
    max_price: float = 20.00,
) -> Tuple[List[Dict[str, Any]], int]:
    """Generate dynamic 48-hour Time-of-Use tariffs within [min_price, max_price].

    Uses random.Random(seed) so generation is completely deterministic if a seed is provided,
    or unpredictable if seed is omitted.
    """
    if not all(math.isfinite(x) for x in (min_price, max_price)) or not 0 <= min_price <= max_price <= 1000:
        raise ValueError("Prices must be finite and satisfy 0 <= min <= max <= 1000.")
    if seed is None:
        seed = random.randint(10000, 999999)

    rng = random.Random(seed)

    now = datetime.now(IST).replace(microsecond=0)
    today_start = datetime.combine(now.date(), dt_time.min, tzinfo=IST)
    points: List[Dict[str, Any]] = []

    for day_idx, (day_label, day_offset) in enumerate([("Today", 0), ("Tomorrow_DayAhead", 1)]):
        base_day_start = today_start + timedelta(days=day_offset)

        # Unpredictable day-level macro shift (+- 0.8 INR)
        day_drift = rng.uniform(-0.8, 0.8)

        # Pick 1 random surprise spike hour and 1 random surprise dip hour per day
        spike_hour = rng.randint(15, 21)
        dip_hour = rng.randint(1, 14)

        for h in range(24):
            dt = base_day_start + timedelta(hours=h)
            hour_index = day_offset * 24 + h

            # Realistic Indian Time-of-Use baseline with organic noise
            if 0 <= h < 6:
                # Night off-peak (tends toward lower end: ~8.5 - 10.5)
                base = 9.2 + rng.uniform(-1.0, 1.2) + day_drift
            elif 6 <= h < 9:
                # Morning wake/prep ramp (mid-tier: ~11.5 - 14.5)
                base = 12.8 + rng.uniform(-1.2, 1.6) + day_drift
            elif 9 <= h < 12:
                # Morning commercial peak (high-tier: ~14.0 - 17.0)
                base = 15.2 + rng.uniform(-1.2, 1.8) + day_drift
            elif 12 <= h < 16:
                # Midday solar generation surge dip (~9.5 - 12.8)
                base = 11.0 + rng.uniform(-1.5, 1.6) + day_drift
            elif 16 <= h < 22:
                # Evening system peak (~16.5 - 19.5)
                base = 17.8 + rng.uniform(-1.0, 1.8) + day_drift
            else:
                # Late night ramp down (~10.0 - 13.0)
                base = 11.5 + rng.uniform(-1.2, 1.4) + day_drift

            # Apply surprise shock for grid events
            if h == spike_hour:
                base += rng.uniform(1.8, 3.2)
            elif h == dip_hour:
                base -= rng.uniform(1.5, 2.5)

            # Strictly clip within [min_price, max_price]
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


def save_published_tariffs(intervals: List[Dict[str, Any]], seed: Optional[int] = None) -> None:
    """Save/publish 48 dynamic tariff intervals to database."""
    with transaction.atomic():
        TariffInterval.objects.all().delete()
        records = [
            TariffInterval(
                hour_index=item["hour"],
                day_label=item["day"],
                start_time=datetime.fromisoformat(item["start_time"]),
                start_time_ist=item["start_time_ist"],
                duration_hours=item.get("duration_hours", 1),
                price_per_kwh=item["price_per_kwh"],
                currency=item.get("currency", "INR"),
                seed_used=seed,
            )
            for item in intervals
        ]
        TariffInterval.objects.bulk_create(records)
        transaction.on_commit(lambda: cache.delete("published-tariffs"))


def get_or_create_published_tariffs(
    min_price: float = 8.0,
    max_price: float = 20.0,
) -> Tuple[List[Dict[str, Any]], Optional[int]]:
    """Retrieve currently published tariffs, or dynamically generate and save if none exist."""
    today = datetime.now(IST).date()
    cached = cache.get("published-tariffs")
    if cached and datetime.fromisoformat(cached[0][0]["start_time"]).astimezone(IST).date() == today:
        return cached
    existing = list(TariffInterval.objects.all().order_by("hour_index"))
    if len(existing) == 48 and existing[0].start_time.astimezone(IST).date() == today:
        seed = existing[0].seed_used
        points = [
            {
                "hour": r.hour_index,
                "hour_of_day": r.hour_index % 24,
                "day": r.day_label,
                "start_time": r.start_time.isoformat(),
                "start_time_ist": r.start_time_ist,
                "duration_hours": r.duration_hours,
                "price_per_kwh": r.price_per_kwh,
                "currency": r.currency,
                "seed_used": r.seed_used,
            }
            for r in existing
        ]
        cache.set("published-tariffs", (points, seed), 60)
        return points, seed

    # No tariffs published yet -> generate and publish automatically
    if not cache.add("tariff-rollover-lock", True, 15):
        from .security import ServiceBusy
        raise ServiceBusy("Tariffs are being refreshed. Retry shortly.")
    try:
        points, seed = generate_dynamic_tariffs(min_price=min_price, max_price=max_price)
        announced = {r.start_time.astimezone(IST): r for r in existing}
        for point in points:
            previous = announced.get(datetime.fromisoformat(point["start_time"]))
            if previous:
                point["price_per_kwh"] = previous.price_per_kwh
        save_published_tariffs(points, seed=seed)
        cache.set("published-tariffs", (points, seed), 60)
        return points, seed
    finally:
        cache.delete("tariff-rollover-lock")


def get_2day_ahead_tariffs() -> List[Dict[str, Any]]:
    """Backward-compatible helper returning 48 tariff intervals."""
    tariffs, _ = get_or_create_published_tariffs()
    return tariffs
