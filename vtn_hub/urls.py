from django.urls import path
from . import views

urlpatterns = [
    path("", views.dashboard_view, name="dashboard"),
    path("api/v3/auth/verify", views.api_verify_token, name="api_verify_token"),
    path("api/v3/tariffs.json", views.api_tariffs_json, name="api_tariffs_json"),
    path("api/v3/reports", views.api_reports, name="api_reports"),
    path("api/v3/appliances", views.api_appliances, name="api_appliances"),
    path("api/v3/leaderboard", views.api_leaderboard, name="api_leaderboard"),
    path("download/tariffs.csv", views.download_tariffs_csv, name="download_tariffs_csv"),
    path("download/telemetry.csv", views.download_telemetry_csv, name="download_telemetry_csv"),
]
