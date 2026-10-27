from __future__ import annotations

from datetime import datetime, timedelta, timezone
import secrets
from django.db import models

IST = timezone(timedelta(hours=5, minutes=30), name="IST")


def generate_team_token():
    return secrets.token_urlsafe(32)


class Team(models.Model):
    team_name = models.CharField(max_length=64, unique=True)
    token = models.CharField(max_length=64, unique=True, default=generate_team_token)
    first_connected_at = models.DateTimeField(null=True, blank=True)
    last_connected_at = models.DateTimeField(null=True, blank=True)
    last_tariff_fetched_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["team_name"]

    def __str__(self) -> str:
        return self.team_name

    @property
    def is_connected(self) -> bool:
        return bool(self.first_connected_at)

    def get_latest_appliances(self, phase: str = "baseline"):
        """Return dict of latest reading per appliance for this team and phase."""
        app_dict = {}
        for r in self.telemetry_records.filter(phase=phase).order_by("-id"):
            if r.appliance not in app_dict:
                app_dict[r.appliance] = {
                    "energy": r.energy,
                    "outdoor_temperature": r.outdoor_temperature,
                    "timestamp": r.timestamp.isoformat(),
                    "phase": r.phase,
                }
        return app_dict


def snap_to_15min_interval(dt: datetime) -> datetime:
    """Snaps a timestamp forward to the nearest 15-minute interval end boundary.
    Safely rolls over 60 minutes into the next hour/day via timedelta arithmetic.
    """
    remainder = timedelta(minutes=dt.minute % 15, seconds=dt.second, microseconds=dt.microsecond)
    if remainder == timedelta(0):
        return dt
    return dt + timedelta(minutes=15) - remainder


class TelemetryRecord(models.Model):
    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="telemetry_records")
    phase = models.CharField(max_length=32, default="baseline", db_index=True)
    appliance = models.CharField(max_length=128, default="Default_Appliance")
    outdoor_temperature = models.FloatField(null=True, blank=True)
    energy = models.FloatField(default=0.0)
    notes = models.TextField(blank=True, default="")
    raw_payload = models.TextField(blank=True, default="")
    timestamp = models.DateTimeField()
    interval_timestamp = models.DateTimeField(null=True, blank=True, db_index=True)

    class Meta:
        ordering = ["-id"]
        constraints = [models.UniqueConstraint(fields=["team", "phase", "appliance", "interval_timestamp"], name="unique_team_phase_appliance_slot")]
        indexes = [
            models.Index(fields=["team", "phase"]),
            models.Index(fields=["team", "appliance"]),
            models.Index(fields=["timestamp"]),
            models.Index(fields=["team", "interval_timestamp"]),
            models.Index(fields=["team", "phase", "interval_timestamp"]),
        ]

    def save(self, *args, **kwargs):
        if not self.interval_timestamp and self.timestamp:
            self.interval_timestamp = snap_to_15min_interval(self.timestamp)
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        temp_str = f"{self.outdoor_temperature}°C" if self.outdoor_temperature is not None else "N/A"
        return f"{self.team.team_name} - {self.appliance} (Energy: {self.energy}, Temp: {temp_str})"


class TariffInterval(models.Model):
    hour_index = models.IntegerField(unique=True)
    day_label = models.CharField(max_length=32)
    start_time = models.DateTimeField()
    start_time_ist = models.CharField(max_length=64)
    duration_hours = models.IntegerField(default=1)
    price_per_kwh = models.FloatField()
    currency = models.CharField(max_length=8, default="INR")
    seed_used = models.IntegerField(null=True, blank=True)
    published_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["hour_index"]

    def __str__(self) -> str:
        return f"Hour {self.hour_index} ({self.day_label}): INR {self.price_per_kwh:.2f}/kWh"
