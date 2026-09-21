"""Webhook イベントの処理（注文の照合・交付記録の作成・自動キャンセル）と処方の同期。

流れ（orders/create）:
  1. Webhook ビューが受信履歴を保存して 200 を返す（Shopify の 5 秒制限のため処理はワーカーで行う）
  2. process_event() が注文を組み立て、FileMaker から処方・交付記録・製品マスタを取り、core.verify_order で照合
  3. OK → CL_Dispense に state=verified で作成。NG → Shopify で注文をキャンセル・全額返金し、患者に理由を通知
  4. FileMaker / Shopify に接続できないときはキャンセルせず保留し、ワーカーが再試行。30 分続けばスタッフに通知
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from iec_ec_bridge.core import eligibility as E
from iec_ec_bridge.core.models import PROP_RX_LINE_ID, Order, OrderLine, Product
from iec_ec_bridge.core.quantity import remaining_boxes
from iec_ec_bridge.fm import FileMakerUnavailable
from iec_ec_bridge.shopify import ShopifyUnavailable

from . import gateways, notify
from .models import Member, OrderVerification, WebhookEvent

log = logging.getLogger(__name__)

TOPIC_CREATE = "orders/create"
TOPIC_CANCELLED = "orders/cancelled"
SHIPPING_DISABLED = "shipping_disabled"

RETRY_MINUTES = (1, 2, 5, 10, 10, 10, 10, 10, 10, 10, 10, 10)


class Unavailable(Exception):
    """外部サービスに接続できない。イベントを保留して再試行する。"""


# ---- 注文の組み立て ------------------------------------------------------------

def build_order(payload: dict, patient_id: Optional[str]) -> Order:
    lines = []
    for li in payload.get("line_items") or []:
        props = {}
        for p in li.get("properties") or []:
            if isinstance(p, dict) and p.get("name") is not None:
                props[str(p["name"])] = "" if p.get("value") is None else str(p["value"])
        lines.append(OrderLine(sku=(li.get("sku") or "").strip(), quantity=int(li.get("quantity") or 0),
                               properties=props))
    return Order(order_id=str(payload["id"]), patient_id=patient_id,
                 order_date=_order_date(payload), lines=tuple(lines))


def _order_date(payload: dict) -> date:
    s = payload.get("created_at") or payload.get("processed_at")
    if s:
        try:
            dt = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
            return timezone.localtime(dt).date() if dt.tzinfo else dt.date()
        except ValueError:
            pass
    return timezone.localdate()


def _ordered_at(payload: dict) -> datetime:
    s = payload.get("created_at")
    if s:
        try:
            dt = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
            return timezone.localtime(dt).replace(tzinfo=None)
        except ValueError:
            pass
    return timezone.localtime().replace(tzinfo=None)


def detect_delivery(payload: dict) -> str:
    """注文全体の受け渡し方法: pickup（院内受取）または ship（自宅発送）。"""
    lines = payload.get("shipping_lines") or []
    if not lines:
        return "pickup"
    text = " ".join(f"{l.get('code') or ''} {l.get('title') or ''}" for l in lines)
    for kw in settings.PICKUP_KEYWORDS:
        if kw and kw.lower() in text.lower():
            return "pickup"
    return "ship"


def _shipping_address(payload: dict) -> dict:
    a = payload.get("shipping_address") or {}
    keys = ("name", "zip", "province", "city", "address1", "address2", "phone", "company")
    return {k: a.get(k) for k in keys if a.get(k)}


# ---- イベント処理 --------------------------------------------------------------

def process_event(event: WebhookEvent) -> WebhookEvent:
    """1 イベントを処理して状態を更新する。例外は内部で捕捉し、状態に反映する。"""
    event.attempts += 1
    event.status = WebhookEvent.PROCESSING
    event.save(update_fields=["attempts", "status"])
    try:
        if event.topic == TOPIC_CREATE:
            _process_order_created(event)
        elif event.topic == TOPIC_CANCELLED:
            _process_order_cancelled(event)
        else:
            event.status = WebhookEvent.SKIPPED
            event.last_error = f"unsupported topic {event.topic}"
        if event.status == WebhookEvent.PROCESSING:
            event.status = WebhookEvent.DONE
        event.processed_at = timezone.now()
    except (Unavailable, FileMakerUnavailable, ShopifyUnavailable) as e:
        _schedule_retry(event, str(e))
    except Exception as e:  # 想定外: 失敗として残しスタッフに知らせる。注文はキャンセルしない
        log.exception("event %s failed", event.pk)
        event.status = WebhookEvent.FAILED
        event.last_error = f"{type(e).__name__}: {e}"
        OrderVerification.objects.update_or_create(
            order_id=event.resource_id,
            defaults={"status": OrderVerification.ERROR, "note": event.last_error, "event": event})
        notify.staff(f"注文 {event.resource_id} の処理に失敗",
                     f"手作業での確認が必要です。\n{event.last_error}")
    event.save()
    return event


def _schedule_retry(event: WebhookEvent, error: str) -> None:
    minutes = RETRY_MINUTES[min(event.attempts - 1, len(RETRY_MINUTES) - 1)]
    event.status = WebhookEvent.RETRY
    event.last_error = error
    event.next_retry_at = timezone.now() + timedelta(minutes=minutes)
    log.warning("event %s deferred (%s); retry in %d min", event.pk, error, minutes)
    if event.topic == TOPIC_CREATE:
        OrderVerification.objects.update_or_create(
            order_id=event.resource_id,
            defaults={"status": OrderVerification.PENDING, "note": error, "event": event,
                      "order_name": event.payload.get("name", "")})
    age = timezone.now() - event.received_at
    if not event.staff_alerted and age >= timedelta(minutes=settings.PENDING_ALERT_MINUTES):
        notify.staff(f"注文 {event.payload.get('name', event.resource_id)} が {settings.PENDING_ALERT_MINUTES} 分以上保留",
                     f"FileMaker / Shopify に接続できません。\n{error}")
        event.staff_alerted = True


def _process_order_created(event: WebhookEvent) -> None:
    payload = event.payload
    order_id = str(payload["id"])
    order_name = payload.get("name", "")
    existing = OrderVerification.objects.filter(order_id=order_id).first()
    if existing and existing.status in (OrderVerification.OK, OrderVerification.REJECTED,
                                        OrderVerification.CANCELLED):
        event.status = WebhookEvent.SKIPPED
        event.last_error = f"order already {existing.status}"
        return

    customer_id = str((payload.get("customer") or {}).get("id") or "")
    member = Member.objects.filter(shopify_customer_id=customer_id, active=True).first() if customer_id else None
    patient_id = member.patient_id if member else None
    order = build_order(payload, patient_id)
    delivery = detect_delivery(payload)

    ver, _ = OrderVerification.objects.update_or_create(
        order_id=order_id,
        defaults={"order_name": order_name, "shopify_customer_id": customer_id,
                  "patient_id": patient_id or "", "delivery": delivery,
                  "shipping_address": _shipping_address(payload), "event": event,
                  "status": OrderVerification.PENDING})

    repo = gateways.get_repo()
    shopify = gateways.get_shopify()

    # 製品マスタ（処方が要る製品）。CL_Product にない SKU は Shopify のタグで処方要否を判定
    products_info = repo.products()
    products: Dict[str, Product] = repo.core_products(products_info)
    unknown = {li.get("sku"): li.get("product_id") for li in payload.get("line_items") or []
               if (li.get("sku") or "").strip() not in products}
    if unknown:
        tags = shopify.product_tags(unknown.values())
        for sku, pid in unknown.items():
            if settings.SHOPIFY_RX_REQUIRED_TAG not in tags.get(str(pid), []):
                products[(sku or "").strip()] = Product((sku or "").strip(), 1, requires_rx=False)

    rx_line_ids = [l.properties.get(PROP_RX_LINE_ID, "").strip() for l in order.lines
                   if l.properties.get(PROP_RX_LINE_ID)]
    rx_by_line = repo.rx_by_line_ids(rx_line_ids)
    dispenses = repo.dispenses_for_lines(rx_line_ids)

    result = E.verify_order(order, products, rx_by_line, dispenses, settings.SPARE_BOXES)
    reasons = list(result.reasons)
    if delivery == "ship" and not settings.SHIPPING_ENABLED:
        reasons.append(SHIPPING_DISABLED)
    ver.lines = [asdict(l) for l in result.lines]
    ver.reasons = reasons

    if reasons:
        _reject(ver, shopify, order, reasons, result)
        return

    # 照合 OK → 交付記録を作成（再試行時は作成済みの明細を飛ばす）
    created_ids = []
    existing_lines = {d.dispense.rx_line_id for d in repo.dispenses_for_order(order_id)}
    for lr in result.lines:
        if not lr.rx_line_id or lr.rx_line_id in existing_lines:
            continue
        line = order.lines[lr.index]
        info = products_info.get(line.sku)
        line_delivery = "pickup" if delivery == "pickup" else (
            "ship_direct" if info and info.direct_ship else "ship_clinic")
        rid = repo.create_ec_dispense(
            rx_line_id=lr.rx_line_id, patient_id=patient_id, boxes=line.quantity,
            shopify_order_id=order_id, order_name=order_name, delivery=line_delivery,
            ordered_at=_ordered_at(payload))
        created_ids.append(rid)
        existing_lines.add(lr.rx_line_id)
    ver.dispense_record_ids = created_ids
    ver.status = OrderVerification.OK
    ver.action = "dispense_created"
    ver.save()
    notify.patient_order_accepted(patient_id, order_name)


def _reject(ver: OrderVerification, shopify, order: Order, reasons: List[str], result) -> None:
    remaining = next((l.remaining for l in result.lines if l.reason == E.QUANTITY_EXCEEDED), None)
    note = "EC連携: 処方照合NG " + ", ".join(reasons)
    shopify.cancel_order(order.order_id, staff_note=note, reason="OTHER", refund=True,
                         restock=False, notify_customer=True)
    ver.status = OrderVerification.REJECTED
    ver.action = "order_cancelled"
    ver.note = note
    ver.save()
    notify.patient_order_rejected(order.patient_id or "", ver.order_name, reasons, remaining)
    if set(reasons) & notify.STAFF_REASONS:
        notify.staff(f"注文 {ver.order_name} を自動キャンセル（{', '.join(reasons)}）",
                     f"患者ID: {order.patient_id}\n明細: {ver.lines}")


def _process_order_cancelled(event: WebhookEvent) -> None:
    """Shopify 側でキャンセルされた注文の交付記録を cancelled にし、数量上限を戻す。"""
    order_id = str(event.payload["id"])
    repo = gateways.get_repo()
    records = repo.dispenses_for_order(order_id)
    blocked = []
    for rec in records:
        if rec.dispense.state in ("verified", "ordered"):
            repo.set_dispense_state(rec.record_id, "cancelled")
        elif rec.dispense.state in ("arrived", "dispensed"):
            blocked.append(rec)
    ver = OrderVerification.objects.filter(order_id=order_id).first()
    if ver and ver.status != OrderVerification.REJECTED:
        ver.status = OrderVerification.CANCELLED
        ver.note = (ver.note + "\n" if ver.note else "") + "Shopify でキャンセル"
        ver.save()
    if blocked:
        notify.staff(f"入荷済み／交付済みの注文 {event.payload.get('name', order_id)} がキャンセルされました",
                     "交付記録は自動では変更していません。FileMaker で確認してください。")


# ---- ワーカー ------------------------------------------------------------------

def process_pending_events(limit: int = 50) -> int:
    """未処理・再試行待ちのイベントを順に処理する。処理した件数を返す。"""
    now = timezone.now()
    qs = (WebhookEvent.objects.filter(status__in=[WebhookEvent.RECEIVED, WebhookEvent.RETRY])
          .filter(models_q_next_retry(now)).order_by("received_at")[:limit])
    count = 0
    for event in list(qs):
        with transaction.atomic():
            locked = WebhookEvent.objects.select_for_update(skip_locked=True).filter(
                pk=event.pk, status__in=[WebhookEvent.RECEIVED, WebhookEvent.RETRY]).first()
            if not locked:
                continue
            locked.status = WebhookEvent.PROCESSING
            locked.save(update_fields=["status"])
        process_event(locked)
        count += 1
    return count


def models_q_next_retry(now):
    return Q(next_retry_at__isnull=True) | Q(next_retry_at__lte=now)


# ---- 処方の同期 ----------------------------------------------------------------

def rx_metafield_payload(repo, patient_id: str, today: Optional[date] = None) -> dict:
    """顧客メタフィールド iec.rx_lines に書く JSON。テーマが「あなたの処方レンズ」ページで読む。"""
    today = today or timezone.localdate()
    products = repo.products()
    rxs = repo.active_rx_for_patient(patient_id, on=today)
    line_ids = [l.rx_line_id for rx in rxs for l in rx.lines]
    dispenses = repo.dispenses_for_lines(line_ids)
    lines = []
    for rx in rxs:
        for l in rx.lines:
            info = products.get(l.product_code)
            if info is None:
                log.warning("rx line %s refers to unknown/inactive product %s", l.rx_line_id, l.product_code)
                continue
            remaining = remaining_boxes(rx.rx_date, rx.valid_until, max(info.days_per_box, 1),
                                        dispenses, l.rx_line_id, settings.SPARE_BOXES)
            lines.append({
                "rx_line_id": l.rx_line_id, "eye": l.eye, "sku": l.product_code,
                "variant_id": info.shopify_variant_id or None,
                "maker": info.maker, "product_name": info.product_name,
                "bc": _num(l.bc), "dia": _num(l.dia), "pwr": _num(l.pwr), "cyl": _num(l.cyl),
                "ax": _num(l.ax), "add_power": l.add_power, "color": l.color,
                "remaining_boxes": remaining, "rx_date": rx.rx_date.isoformat(),
                "valid_until": rx.valid_until.isoformat(),
            })
    lines.sort(key=lambda x: (x["valid_until"], x["eye"] != "R"))
    return {"synced_at": timezone.now().isoformat(timespec="seconds"), "patient": True, "lines": lines}


def _num(v):
    if v is None:
        return None
    return str(int(v)) if v == v.to_integral() else str(v.normalize())


def sync_member(member: Member, repo=None, shopify=None) -> dict:
    repo = repo or gateways.get_repo()
    shopify = shopify or gateways.get_shopify()
    ns = settings.SHOPIFY_METAFIELD_NAMESPACE
    try:
        payload = rx_metafield_payload(repo, member.patient_id)
        shopify.set_customer_metafields(member.shopify_customer_id, [
            {"namespace": ns, "key": "rx_lines", "type": "json", "value": json.dumps(payload, ensure_ascii=False)},
            {"namespace": ns, "key": "patient_id", "type": "single_line_text_field", "value": member.patient_id},
        ])
        member.rx_snapshot = payload
        member.last_synced_at = timezone.now()
        member.last_sync_error = ""
        member.save(update_fields=["rx_snapshot", "last_synced_at", "last_sync_error"])
        return payload
    except Exception as e:
        member.last_sync_error = f"{type(e).__name__}: {e}"
        member.save(update_fields=["last_sync_error"])
        raise


def sync_all_members() -> dict:
    repo = gateways.get_repo()
    shopify = gateways.get_shopify()
    ok, failed = 0, []
    for m in Member.objects.filter(active=True).order_by("patient_id"):
        try:
            sync_member(m, repo, shopify)
            ok += 1
        except Exception as e:
            log.exception("sync failed for %s", m.patient_id)
            failed.append((m.patient_id, str(e)))
    if failed:
        notify.staff(f"処方の同期に失敗 {len(failed)} 件",
                     "\n".join(f"{p}: {e}" for p, e in failed[:50]))
    return {"ok": ok, "failed": len(failed)}


# ---- 会員登録 ------------------------------------------------------------------

def register_member(patient_id: str, email: str, line_user_id: str = "",
                    first_name: str = "", last_name: str = "") -> Member:
    """既存 LIFF からの登録。LINE ID と患者 ID の対応を FileMaker で確認し、Shopify 顧客を作成する。"""
    repo = gateways.get_repo()
    shopify = gateways.get_shopify()
    if line_user_id:
        linked = repo.patient_id_for_line_user(line_user_id)
        if linked != patient_id:
            raise PermissionError("LINE ID と患者 ID の対応が一致しません")
    ns = settings.SHOPIFY_METAFIELD_NAMESPACE
    member = Member.objects.filter(patient_id=patient_id).first()
    if member is None:
        customer_gid = shopify.find_customer_by_email(email)
        if customer_gid is None:
            customer_gid = shopify.create_customer(
                email, first_name=first_name, last_name=last_name, tags=["patient"],
                metafields=[{"namespace": ns, "key": "patient_id",
                             "type": "single_line_text_field", "value": patient_id}])
        else:
            shopify.add_customer_tags(customer_gid, ["patient"])
        customer_id = shopify.numeric_id(customer_gid)
        other = Member.objects.filter(shopify_customer_id=customer_id).exclude(patient_id=patient_id).first()
        if other:
            raise PermissionError("このメールアドレスは別の患者に登録済みです")
        member = Member.objects.create(patient_id=patient_id, shopify_customer_id=customer_id,
                                       email=email, line_user_id=line_user_id)
    else:
        member.email = email or member.email
        member.line_user_id = line_user_id or member.line_user_id
        member.active = True
        member.save()
    try:
        sync_member(member, repo, shopify)
    except Exception:
        log.exception("initial sync failed for %s (nightly sync will retry)", patient_id)
    return member
