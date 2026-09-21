from django.contrib import admin

from .models import Member, OrderVerification, PurchaseOrder, WebhookEvent


@admin.register(Member)
class MemberAdmin(admin.ModelAdmin):
    list_display = ("patient_id", "shopify_customer_id", "email", "active", "last_synced_at")
    search_fields = ("patient_id", "shopify_customer_id", "email", "line_user_id")


@admin.register(WebhookEvent)
class WebhookEventAdmin(admin.ModelAdmin):
    list_display = ("received_at", "topic", "resource_id", "status", "attempts", "next_retry_at")
    list_filter = ("topic", "status")
    search_fields = ("resource_id", "webhook_id")
    readonly_fields = ("payload",)


@admin.register(OrderVerification)
class OrderVerificationAdmin(admin.ModelAdmin):
    list_display = ("created_at", "order_name", "patient_id", "status", "action", "delivery")
    list_filter = ("status", "delivery")
    search_fields = ("order_id", "order_name", "patient_id")


@admin.register(PurchaseOrder)
class PurchaseOrderAdmin(admin.ModelAdmin):
    list_display = ("po_number", "supplier", "method", "line_count", "box_count", "created_at", "sent_at")
    list_filter = ("supplier", "method")
