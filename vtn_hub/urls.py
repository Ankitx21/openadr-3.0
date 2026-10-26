from django.urls import path
from . import views

urlpatterns = [
    path("", views.dashboard_view, name="dashboard"),
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("api/v3/auth/verify", views.api_verify_token, name="api_verify_token"),
    # Phase 1: Testing Endpoints (Check connection & data sending)
    path("api/v3/testing", views.api_testing, name="api_testing"),
    path("api/v3/testing-data", views.api_testing, name="api_testing_data_alias"),
    path("api/v3/testing/data", views.api_testing, name="api_testing_data"),

    # Phase 2: Baseline Data Endpoints (2-Week Active Period - 15-Minute Intervals)
    path("api/v3/baseline", views.api_baseline, name="api_baseline"),
    path("api/v3/baseline-data", views.api_baseline, name="api_baseline_data_alias"),
    path("api/v3/baseline/data", views.api_baseline, name="api_baseline_data"),
    path("api/v3/baseline/intervals", views.api_telemetry_intervals, name="api_baseline_intervals"),

    # Phase 3: Prototype Product Posting Endpoints (Smart Tariff Optimized Loads)
    path("api/v3/prototype", views.api_prototype, name="api_prototype"),
    path("api/v3/prototype-data", views.api_prototype, name="api_prototype_data_alias"),
    path("api/v3/prototype/data", views.api_prototype, name="api_prototype_data"),

    # Rolling Day-Ahead Tariffs (Always Active for 7+ Months - Continuous)
    path("api/v3/tariffs", views.api_tariffs_json, name="api_tariffs"),
    path("api/v3/tariffs.json", views.api_tariffs_json, name="api_tariffs_json"),
    path("api/v3/tariffs/publish", views.api_publish_tariffs, name="api_publish_tariffs"),

    # Backward-compatible legacy aliases
    path("api/v3/sandbox/test", views.api_sandbox_test, name="api_sandbox_test"),
    path("api/v3/reports", views.api_reports, name="api_reports"),
    path("api/v3/appliances", views.api_appliances, name="api_appliances"),
    path("api/v3/leaderboard", views.api_leaderboard, name="api_leaderboard"),
    path("analytics/", views.analytics_view, name="analytics"),
    path("api/v3/telemetry/intervals", views.api_telemetry_intervals, name="api_telemetry_intervals"),
    path("download/tariffs.csv", views.download_tariffs_csv, name="download_tariffs_csv"),
    path("download/baseline-data.csv", views.download_telemetry_csv, name="download_baseline_csv"),
    path("download/telemetry.csv", views.download_telemetry_csv, name="download_telemetry_csv"),
]
