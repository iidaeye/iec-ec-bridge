"""Webhook 受信〜照合〜交付記録／自動キャンセルの通しテスト（シナリオ番号は開発仕様に対応）。"""
import json
from datetime import timedelta

import pytest
from django.utils import timezone

from bridge import services
from bridge.models import OrderVerification, WebhookEvent
from iec_ec_bridge.core.shopify_hmac import compute_hmac
from tests.conftest import WEBHOOK_SECRET
from tests.fakes import R, order_payload, props

URL_CREATE = "/webhooks/shopify/orders-create"
URL_CANCEL = "/webhooks/shopify/orders-cancelled"


def post_webhook(client, url, payload, topic="orders/create", webhook_id=None, secret=WEBHOOK_SECRET):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return client.post(url, data=body, content_type="application/json",
                       headers={"X-Shopify-Hmac-Sha256": compute_hmac(secret, body),
                                "X-Shopify-Topic": topic,
                                "X-Shopify-Webhook-Id": webhook_id or f"wh-{payload['id']}-{topic}",
                                "X-Shopify-Shop-Domain": "iida-eye-shop-dev.myshopify.com"})


pytestmark = pytest.mark.django_db


def test_13_bad_signature_is_rejected(client, shopify, member):
    r = post_webhook(client, URL_CREATE, order_payload(), secret="wrong")
    assert r.status_code == 401
    assert WebhookEvent.objects.count() == 0


def test_01_valid_order_creates_two_dispenses(client, repo, shopify, member):
    r = post_webhook(client, URL_CREATE, order_payload())
    assert r.status_code == 200 and r.json()["status"] == "done"
    ver = OrderVerification.objects.get(order_id="1001")
    assert ver.status == "ok" and ver.action == "dispense_created" and ver.patient_id == "P001"
    assert len(ver.dispense_record_ids) == 2
    recs = repo.dispenses_for_order("1001")
    assert {(x.dispense.rx_line_id, x.dispense.boxes, x.dispense.state) for x in recs} == {
        ("line-R", 2, "verified"), ("line-L", 1, "verified")}
    assert all(x.delivery == "pickup" and x.channel == "ec" and x.order_name == "#1001" for x in recs)
    assert shopify.cancelled == []


def test_13_retry_of_same_webhook_is_processed_once(client, repo, shopify, member):
    post_webhook(client, URL_CREATE, order_payload(), webhook_id="wh-A")
    r = post_webhook(client, URL_CREATE, order_payload(), webhook_id="wh-A")
    assert r.json()["status"] == "duplicate"
    assert WebhookEvent.objects.count() == 1
    assert len(repo.dispenses_for_order("1001")) == 2


def test_same_order_with_new_webhook_id_is_skipped(client, repo, shopify, member):
    post_webhook(client, URL_CREATE, order_payload(), webhook_id="wh-A")
    post_webhook(client, URL_CREATE, order_payload(), webhook_id="wh-B")
    assert WebhookEvent.objects.get(webhook_id="wh-B").status == "skipped"
    assert len(repo.dispenses_for_order("1001")) == 2


def test_04_expired_rx_is_cancelled_and_refunded(client, repo, shopify, member):
    post_webhook(client, URL_CREATE, order_payload(created_at="2027-04-02T09:00:00+09:00"))
    ver = OrderVerification.objects.get(order_id="1001")
    assert ver.status == "rejected" and ver.reasons == ["rx_expired"]
    assert shopify.cancelled == [{"order_id": "1001", "note": "EC連携: 処方照合NG rx_expired", "refund": True}]
    assert repo.dispenses_for_order("1001") == []


def test_05_expiry_day_is_ok(client, repo, shopify, member):
    post_webhook(client, URL_CREATE, order_payload(created_at="2027-04-01T23:00:00+09:00"))
    assert OrderVerification.objects.get(order_id="1001").status == "ok"


def test_06_tampered_spec_is_cancelled(client, repo, shopify, member):
    items = [{"id": 1, "sku": "1D-SPH", "quantity": 1, "product_id": 701, "properties": props(R, pwr="-5.00")}]
    post_webhook(client, URL_CREATE, order_payload(line_items=items))
    ver = OrderVerification.objects.get(order_id="1001")
    assert ver.status == "rejected" and ver.reasons == ["spec_mismatch"]
    assert len(shopify.cancelled) == 1


def test_07_cap_plus_one_is_cancelled_with_remaining(client, repo, shopify, member):
    items = [{"id": 1, "sku": "1D-SPH", "quantity": 9, "product_id": 701, "properties": props(R)}]
    post_webhook(client, URL_CREATE, order_payload(line_items=items))
    ver = OrderVerification.objects.get(order_id="1001")
    assert ver.reasons == ["quantity_exceeded"] and ver.lines[0]["remaining"] == 8


def test_08_clinic_dispense_is_deducted(client, repo, shopify, member):
    repo.add_clinic_dispense("line-R", 3)
    items = [{"id": 1, "sku": "1D-SPH", "quantity": 6, "product_id": 701, "properties": props(R)}]
    post_webhook(client, URL_CREATE, order_payload(line_items=items))
    assert OrderVerification.objects.get(order_id="1001").reasons == ["quantity_exceeded"]


def test_unlinked_customer_is_cancelled(client, repo, shopify, db):
    post_webhook(client, URL_CREATE, order_payload(customer_id=9999))
    ver = OrderVerification.objects.get(order_id="1001")
    assert ver.status == "rejected" and ver.reasons == ["not_a_patient"]


def test_15_eyecare_only_order_needs_no_rx(client, repo, shopify, member):
    items = [{"id": 1, "sku": "EYE-SHAMPOO", "quantity": 2, "product_id": 800, "properties": []}]
    post_webhook(client, URL_CREATE, order_payload(line_items=items))
    ver = OrderVerification.objects.get(order_id="1001")
    assert ver.status == "ok" and ver.dispense_record_ids == []


def test_rx_required_tag_without_master_is_rejected(client, repo, shopify, member):
    shopify.product_tags_map["800"] = ["rx-required"]
    items = [{"id": 1, "sku": "NEW-CL", "quantity": 1, "product_id": 800, "properties": []}]
    post_webhook(client, URL_CREATE, order_payload(line_items=items))
    assert OrderVerification.objects.get(order_id="1001").reasons == ["unknown_product"]


def test_shipping_disabled_rejects_shipped_order(client, repo, shopify, member, settings):
    settings.SHIPPING_ENABLED = False
    post_webhook(client, URL_CREATE, order_payload(shipping="宅配便"))
    ver = OrderVerification.objects.get(order_id="1001")
    assert ver.status == "rejected" and ver.reasons == ["shipping_disabled"] and ver.delivery == "ship"


def test_shipping_enabled_sets_direct_or_clinic_delivery(client, repo, shopify, member, settings):
    settings.SHIPPING_ENABLED = True
    post_webhook(client, URL_CREATE, order_payload(shipping="宅配便"))
    recs = {x.dispense.rx_line_id: x.delivery for x in repo.dispenses_for_order("1001")}
    assert recs == {"line-R": "ship_clinic", "line-L": "ship_direct"}   # 2W-TOR は direct_ship=1


def test_pickup_shipping_method_is_pickup(client, repo, shopify, member, settings):
    settings.SHIPPING_ENABLED = False
    post_webhook(client, URL_CREATE, order_payload(shipping="院内受取"))
    assert OrderVerification.objects.get(order_id="1001").delivery == "pickup"


def test_14_filemaker_down_holds_order_then_processes(client, repo, shopify, member):
    repo.unavailable = True
    r = post_webhook(client, URL_CREATE, order_payload())
    assert r.status_code == 200 and r.json()["status"] == "pending_retry"
    ev = WebhookEvent.objects.get()
    assert ev.status == "pending_retry" and ev.next_retry_at is not None
    assert OrderVerification.objects.get(order_id="1001").status == "pending"
    assert shopify.cancelled == []       # キャンセルしない

    # まだ再試行時刻前なら処理しない
    assert services.process_pending_events() == 0
    # 復旧後、再試行時刻を過ぎれば自動で照合される
    repo.unavailable = False
    WebhookEvent.objects.filter(pk=ev.pk).update(next_retry_at=timezone.now() - timedelta(seconds=1))
    assert services.process_pending_events() == 1
    ev.refresh_from_db()
    assert ev.status == "done" and ev.attempts == 2
    assert OrderVerification.objects.get(order_id="1001").status == "ok"
    assert len(repo.dispenses_for_order("1001")) == 2


def test_partial_failure_retry_does_not_duplicate_dispenses(client, repo, shopify, member):
    # 1 件目の交付記録を作った直後に FileMaker が落ちた状況
    original = repo.create_ec_dispense

    def flaky(**kw):
        rid = original(**kw)
        repo.unavailable = True
        return rid
    repo.create_ec_dispense = flaky
    post_webhook(client, URL_CREATE, order_payload())
    ev = WebhookEvent.objects.get()
    assert ev.status == "pending_retry"
    repo.create_ec_dispense = original
    repo.unavailable = False
    WebhookEvent.objects.filter(pk=ev.pk).update(next_retry_at=timezone.now() - timedelta(seconds=1))
    services.process_pending_events()
    assert len(repo.dispenses_for_order("1001")) == 2   # 3 件にならない


def test_pending_over_threshold_alerts_staff_once(client, repo, shopify, member, monkeypatch, settings):
    settings.PENDING_ALERT_MINUTES = 30
    alerts = []
    monkeypatch.setattr(services.notify, "staff", lambda s, b: alerts.append(s))
    repo.unavailable = True
    post_webhook(client, URL_CREATE, order_payload())
    ev = WebhookEvent.objects.get()
    assert alerts == []
    WebhookEvent.objects.filter(pk=ev.pk).update(received_at=timezone.now() - timedelta(minutes=31),
                                                 next_retry_at=timezone.now() - timedelta(seconds=1))
    services.process_pending_events()
    assert len(alerts) == 1
    WebhookEvent.objects.filter(pk=ev.pk).update(next_retry_at=timezone.now() - timedelta(seconds=1))
    services.process_pending_events()
    assert len(alerts) == 1


def test_shopify_down_on_cancel_holds_instead_of_failing(client, repo, shopify, member):
    shopify.unavailable = True
    post_webhook(client, URL_CREATE, order_payload(created_at="2027-05-01T09:00:00+09:00"))
    assert WebhookEvent.objects.get().status == "pending_retry"
    assert OrderVerification.objects.get(order_id="1001").status == "pending"


def test_11_cancelled_order_releases_boxes(client, repo, shopify, member):
    post_webhook(client, URL_CREATE, order_payload())
    r = post_webhook(client, URL_CANCEL, {"id": 1001, "name": "#1001"}, topic="orders/cancelled")
    assert r.status_code == 200
    assert {x.dispense.state for x in repo.dispenses_for_order("1001")} == {"cancelled"}
    assert OrderVerification.objects.get(order_id="1001").status == "cancelled"
    # 上限が戻る: 8 箱を新しい注文で買える
    items = [{"id": 1, "sku": "1D-SPH", "quantity": 8, "product_id": 701, "properties": props(R)}]
    post_webhook(client, URL_CREATE, order_payload(order_id=1002, name="#1002", line_items=items))
    assert OrderVerification.objects.get(order_id="1002").status == "ok"


def test_cancel_of_rejected_order_is_noop(client, repo, shopify, member):
    post_webhook(client, URL_CREATE, order_payload(created_at="2027-05-01T09:00:00+09:00"))
    post_webhook(client, URL_CANCEL, {"id": 1001, "name": "#1001"}, topic="orders/cancelled")
    assert OrderVerification.objects.get(order_id="1001").status == "rejected"


def test_cancel_after_arrival_notifies_staff_and_keeps_state(client, repo, shopify, member, monkeypatch):
    alerts = []
    monkeypatch.setattr(services.notify, "staff", lambda s, b: alerts.append(s))
    post_webhook(client, URL_CREATE, order_payload())
    for rec in repo.dispenses_for_order("1001"):
        repo.set_dispense_state(rec.record_id, "arrived")
    post_webhook(client, URL_CANCEL, {"id": 1001, "name": "#1001"}, topic="orders/cancelled")
    assert {x.dispense.state for x in repo.dispenses_for_order("1001")} == {"arrived"}
    assert len(alerts) == 1


def test_topic_mismatch_is_rejected(client, shopify, member):
    r = post_webhook(client, URL_CREATE, order_payload(), topic="orders/cancelled")
    assert r.status_code == 400


def test_unexpected_error_marks_failed_without_cancelling(client, repo, shopify, member, monkeypatch):
    monkeypatch.setattr(services.notify, "staff", lambda s, b: None)
    repo.rx_by_line_ids = lambda ids: (_ for _ in ()).throw(RuntimeError("boom"))
    post_webhook(client, URL_CREATE, order_payload())
    assert WebhookEvent.objects.get().status == "failed"
    assert OrderVerification.objects.get(order_id="1001").status == "error"
    assert shopify.cancelled == []
