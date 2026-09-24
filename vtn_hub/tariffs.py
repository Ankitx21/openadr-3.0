from __future__ import annotations

from datetime import datetime, time as dt_time, timedelta, timezone
from typing import Any, Dict, List

IST = timezone(timedelta(hours=5, minutes=30), name="IST")


def get_2day_ahead_tariffs() -> List[Dict[str, Any]]:
    now = datetime.now(IST).replace(microsecond=0)
    today_start = datetime.combine(now.date(), dt_time.min, tzinfo=IST)
    points = []

    for h in range(24):
        dt = today_start + timedelta(hours=h)
        if 0 <= h < 6:
            price = 8.50
        elif 17 <= h < 21:
            price = 22.00
        elif 9 <= h < 14:
            price = 14.50
        else:
            price = 11.00
        points.append({
            "hour": h,
            "day": "Today",
            "start_time": dt.isoformat(),
            "start_time_ist": dt.strftime("%Y-%m-%d %H:%M IST"),
            "duration_hours": 1,
            "price_per_kwh": price,
            "currency": "INR",
        })

    tomorrow_start = today_start + timedelta(days=1)
    for h in range(24):
        dt = tomorrow_start + timedelta(hours=h)
        if 0 <= h < 6:
            price = 8.00
        elif 17 <= h < 21:
            price = 24.50
        elif 9 <= h < 14:
            price = 13.50
        else:
            price = 11.50
        points.append({
            "hour": 24 + h,
            "day": "Tomorrow_DayAhead",
            "start_time": dt.isoformat(),
            "start_time_ist": dt.strftime("%Y-%m-%d %H:%M IST"),
            "duration_hours": 1,
            "price_per_kwh": price,
            "currency": "INR",
        })

    return points
