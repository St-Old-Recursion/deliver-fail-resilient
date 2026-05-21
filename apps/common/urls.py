from django.urls import path
from . import views

urlpatterns = [
    path("circuit-breaker/status", views.circuit_breaker_status),
    path("saga-logs/", views.saga_logs),
    path("failures/", views.failure_counters),
    path("inject-failure/", views.inject_failure),
]
