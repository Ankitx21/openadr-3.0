from django.contrib import admin
from .models import Team, TelemetryRecord, TariffInterval, generate_team_token


@admin.register(Team)
class TeamAdmin(admin.ModelAdmin):
    list_display = ("team_name", "is_connected", "last_connected_at", "total_records")
    readonly_fields = ("token", "first_connected_at", "last_connected_at", "last_tariff_fetched_at")
    actions = ["rotate_tokens"]

    @admin.action(description="Rotate selected team tokens (old tokens stop working)")
    def rotate_tokens(self, request, queryset):
        for team in queryset:
            team.token = generate_team_token()
            team.save(update_fields=["token"])
        self.message_user(request, "Tokens rotated. Open each team to retrieve its new token.")
    search_fields = ("team_name", "token")
    list_filter = ("first_connected_at",)

    def total_records(self, obj):
        return obj.telemetry_records.count()
    total_records.short_description = "Telemetry Reports"


@admin.register(TelemetryRecord)
class TelemetryRecordAdmin(admin.ModelAdmin):
    list_display = ("id", "timestamp", "team", "appliance", "outdoor_temperature", "energy")
    list_filter = ("team", "appliance")
    search_fields = ("team__team_name", "appliance", "notes")
    date_hierarchy = "timestamp"


@admin.register(TariffInterval)
class TariffIntervalAdmin(admin.ModelAdmin):
    list_display = ("hour_index", "day_label", "start_time_ist", "price_per_kwh", "currency", "seed_used", "published_at")
    list_filter = ("day_label", "seed_used")
    ordering = ("hour_index",)
