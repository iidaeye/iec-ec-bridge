from django.urls import path

from . import views

urlpatterns = [
    path("webhooks/shopify/orders-create", views.orders_create, name="wh_orders_create"),
    path("webhooks/shopify/orders-cancelled", views.orders_cancelled, name="wh_orders_cancelled"),
    path("api/members/register", views.members_register, name="members_register"),
    path("api/rx/refresh", views.rx_refresh, name="rx_refresh"),
    path("healthz", views.healthz, name="healthz"),
]
