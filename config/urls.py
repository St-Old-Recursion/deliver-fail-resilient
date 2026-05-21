from django.urls import path, include

urlpatterns = [
    path("api/", include("apps.orders.urls")),
    path("api/debug/", include("apps.common.urls")),
]
