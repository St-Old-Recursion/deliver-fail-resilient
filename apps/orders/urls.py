from django.urls import path
from . import views

urlpatterns = [
    path("orders/", views.create_order),
    path("orders/<int:order_id>/status", views.order_status),
    path("orders/<int:order_id>/cancel", views.cancel_order),
]
