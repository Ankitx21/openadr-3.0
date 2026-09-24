from __future__ import annotations

from datetime import datetime, timedelta, timezone
from django.db import connection, models

IST = timezone(timedelta(hours=5, minutes=30), name="IST")


class Team(models.Model):
    team_name = models.CharField(max_length=64, unique=True)
    token = models.CharField(max_length=64, unique=True)
    first_connected_at = models.DateTimeField(null=True, blank=True)
    last_connected_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["team_name"]

    def __str__(self) -> str:
        return f"{self.team_name} ({self.token})"

    @property
    def is_connected(self) -> bool:
        return bool(self.first_connected_at)

    def get_latest_appliances(self):
        """Return dict of latest reading per appliance for this team."""
        records = (
            self.telemetry_records.order_by("appliance", "-id")
            .distinct("appliance")
            if "postgresql" in connection.vendor
            else None
        )
        if records is not None:
            return {
                r.appliance: {
                    "power_kw": r.power_kw,
                    "energy_kwh": r.energy_kwh,
                    "status": r.status,
                    "timestamp": r.timestamp.isoformat(),
                }
                for r in records
            }

        # Fallback for SQLite
        app_dict = {}
        for r in self.telemetry_records.order_by("-id"):
            if r.appliance not in app_dict:
                app_dict[r.appliance] = {
                    "power_kw": r.power_kw,
                    "energy_kwh": r.energy_kwh,
                    "status": r.status,
                    "timestamp": r.timestamp.isoformat(),
                }
        return app_dict


class TelemetryRecord(models.Model):
    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="telemetry_records")
    appliance = models.CharField(max_length=128)
    power_kw = models.FloatField()
    energy_kwh = models.FloatField(default=0.0)
    status = models.CharField(max_length=64, default="RUNNING")
    notes = models.TextField(blank=True, default="")
    raw_payload = models.TextField(blank=True, default="")
    timestamp = models.DateTimeField()

    class Meta:
        ordering = ["-id"]
        indexes = [
            models.Index(fields=["team", "appliance"]),
            models.Index(fields=["timestamp"]),
        ]

    def __str__(self) -> str:
        return f"{self.team.team_name} - {self.appliance} ({self.power_kw} kW)"
