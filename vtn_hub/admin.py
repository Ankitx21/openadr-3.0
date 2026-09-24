from django.contrib import admin
from .models import Team, TelemetryRecord


@admin.register(Team)
class TeamAdmin(admin.ModelAdmin):
    list_display = ("team_name", "token", "is_connected", "last_connected_at", "total_records")
    search_fields = ("team_name", "token")
    list_filter = ("first_connected_at",)

    def total_records(self, obj):
        return obj.telemetry_records.count()
    total_records.short_description = "Telemetry Reports"


@admin.register(TelemetryRecord)
class TelemetryRecordAdmin(admin.ModelAdmin):
    list_display = ("id", "timestamp", "team", "appliance", "power_kw", "energy_kwh", "status")
    list_filter = ("team", "appliance", "status")
    search_fields = ("team__team_name", "appliance", "notes")
    date_hierarchy = "timestamp"
