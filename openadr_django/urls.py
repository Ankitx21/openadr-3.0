from django.urls import include, path

urlpatterns = [
    path("", include("vtn_hub.urls")),
]
