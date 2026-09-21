"""PostgreSQL に置くのは、会員の対応・Webhook の受信履歴・照合結果のログ・発注書の記録だけ。
処方と交付記録の正本は FileMaker。"""
from django.db import models


class Member(models.Model):
    """患者ID と Shopify 顧客ID の対応。"""
    patient_id = models.CharField(max_length=64, unique=True)
    shopify_customer_id = models.CharField(max_length=32, unique=True)
    email = models.EmailField(blank=True)
    line_user_id = models.CharField(max_length=64, blank=True, db_index=True)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_synced_at = models.DateTimeField(null=True, blank=True)
    rx_snapshot = models.JSONField(default=dict, blank=True)   # 最後にメタフィールドへ書いた内容
    last_sync_error = models.TextField(blank=True)

    def __str__(self):
        return f"{self.patient_id} → customer {self.shopify_customer_id}"


class WebhookEvent(models.Model):
    """Shopify Webhook の受信履歴。webhook_id で再送を一意にする。"""
    RECEIVED, PROCESSING, DONE, RETRY, FAILED, SKIPPED = (
        "received", "processing", "done", "pending_retry", "failed", "skipped")
    STATUS = [(s, s) for s in (RECEIVED, PROCESSING, DONE, RETRY, FAILED, SKIPPED)]

    webhook_id = models.CharField(max_length=128, unique=True)
    topic = models.CharField(max_length=64, db_index=True)
    shop_domain = models.CharField(max_length=128, blank=True)
    resource_id = models.CharField(max_length=64, db_index=True)   # 注文 ID
    payload = models.JSONField()
    status = models.CharField(max_length=16, choices=STATUS, default=RECEIVED, db_index=True)
    attempts = models.PositiveIntegerField(default=0)
    last_error = models.TextField(blank=True)
    received_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)
    next_retry_at = models.DateTimeField(null=True, blank=True, db_index=True)
    staff_alerted = models.BooleanField(default=False)

    def __str__(self):
        return f"{self.topic} {self.resource_id} [{self.status}]"


class OrderVerification(models.Model):
    """注文ごとの照合結果。「なぜこの注文が通ったか／止まったか」を後から説明するためのログ。"""
    OK, REJECTED, PENDING, CANCELLED, ERROR = "ok", "rejected", "pending", "cancelled", "error"
    STATUS = [(s, s) for s in (OK, REJECTED, PENDING, CANCELLED, ERROR)]

    order_id = models.CharField(max_length=64, unique=True)
    order_name = models.CharField(max_length=32, blank=True)
    shopify_customer_id = models.CharField(max_length=32, blank=True)
    patient_id = models.CharField(max_length=64, blank=True, db_index=True)
    status = models.CharField(max_length=16, choices=STATUS, default=PENDING, db_index=True)
    reasons = models.JSONField(default=list, blank=True)     # NG 理由コード
    lines = models.JSONField(default=list, blank=True)       # 明細ごとの判定
    delivery = models.CharField(max_length=16, blank=True)   # pickup / ship_direct / ship_clinic
    shipping_address = models.JSONField(default=dict, blank=True)
    dispense_record_ids = models.JSONField(default=list, blank=True)
    action = models.CharField(max_length=64, blank=True)     # dispense_created / order_cancelled
    note = models.TextField(blank=True)
    event = models.ForeignKey(WebhookEvent, null=True, blank=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.order_name or self.order_id} [{self.status}]"


class PurchaseOrder(models.Model):
    """発注書（FAX 用 PDF）の記録。"""
    po_number = models.CharField(max_length=32, unique=True)
    supplier = models.CharField(max_length=128, db_index=True)
    method = models.CharField(max_length=16, default="fax_pdf")   # fax_pdf / email
    file_path = models.CharField(max_length=512, blank=True)
    dispense_record_ids = models.JSONField(default=list)
    order_names = models.JSONField(default=list)
    line_count = models.PositiveIntegerField(default=0)
    box_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    send_error = models.TextField(blank=True)

    def __str__(self):
        return f"{self.po_number} {self.supplier}"
