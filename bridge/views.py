"""HTTP エンドポイント。

POST /webhooks/shopify/orders-create   Shopify → 注文の照合（HMAC 検証、受信履歴で二重処理を防止）
POST /webhooks/shopify/orders-cancelled Shopify → 交付記録を cancelled に
POST /api/members/register              既存 LIFF → 患者ID とメールを受け Shopify 顧客を作成
POST /api/rx/refresh                    FileMaker スクリプト → 指定患者の処方を即時に再同期
GET  /healthz                           監視
"""
import hmac
import json
import logging

from django.conf import settings
from django.db import IntegrityError, connection, transaction
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from iec_ec_bridge.core.shopify_hmac import HEADER as HMAC_HEADER, verify_webhook

from . import services
from .models import Member, WebhookEvent

log = logging.getLogger(__name__)


def _header(request: HttpRequest, name: str) -> str:
    return request.headers.get(name, "")


# ---- Shopify Webhook ------------------------------------------------------------

def _receive_webhook(request: HttpRequest, expected_topic: str) -> HttpResponse:
    raw = request.body
    if not verify_webhook(settings.SHOPIFY_WEBHOOK_SECRET, raw, _header(request, HMAC_HEADER)):
        log.warning("webhook rejected: bad HMAC (%s)", expected_topic)
        return HttpResponse("invalid signature", status=401)
    topic = _header(request, "X-Shopify-Topic") or expected_topic
    if topic != expected_topic:
        return HttpResponse("topic mismatch", status=400)
    try:
        payload = json.loads(raw.decode("utf-8"))
        order_id = str(payload["id"])
    except (ValueError, KeyError, UnicodeDecodeError):
        return HttpResponse("bad payload", status=400)
    webhook_id = _header(request, "X-Shopify-Webhook-Id") or f"{topic}:{order_id}"
    try:
        with transaction.atomic():
            event = WebhookEvent.objects.create(
                webhook_id=webhook_id, topic=topic, shop_domain=_header(request, "X-Shopify-Shop-Domain"),
                resource_id=order_id, payload=payload)
    except IntegrityError:
        log.info("webhook %s already received; ignoring retry", webhook_id)
        return JsonResponse({"status": "duplicate"}, status=200)
    if settings.WEBHOOK_PROCESS_INLINE:
        services.process_event(event)
        return JsonResponse({"status": event.status}, status=200)
    return JsonResponse({"status": "queued"}, status=200)


@csrf_exempt
@require_POST
def orders_create(request):
    return _receive_webhook(request, services.TOPIC_CREATE)


@csrf_exempt
@require_POST
def orders_cancelled(request):
    return _receive_webhook(request, services.TOPIC_CANCELLED)


# ---- /api/* （共有キー＋送信元 IP） --------------------------------------------------

def _client_ip(request: HttpRequest) -> str:
    xff = request.META.get("HTTP_X_FORWARDED_FOR", "")
    return (xff.split(",")[0].strip() if xff else request.META.get("REMOTE_ADDR", ""))


def _api_authorized(request: HttpRequest) -> bool:
    key = settings.API_SHARED_KEY
    given = _header(request, "X-Api-Key") or _header(request, "Authorization").replace("Bearer ", "", 1)
    if not key or not given or not hmac.compare_digest(key, given):
        return False
    if settings.API_ALLOWED_IPS and _client_ip(request) not in settings.API_ALLOWED_IPS:
        return False
    return True


def _json_body(request: HttpRequest) -> dict:
    try:
        return json.loads(request.body.decode("utf-8") or "{}")
    except (ValueError, UnicodeDecodeError):
        return {}


@csrf_exempt
@require_POST
def members_register(request):
    if not _api_authorized(request):
        return JsonResponse({"error": "unauthorized"}, status=401)
    body = _json_body(request)
    patient_id = str(body.get("patient_id") or "").strip()
    email = str(body.get("email") or "").strip()
    if not patient_id or not email or "@" not in email:
        return JsonResponse({"error": "patient_id and email are required"}, status=400)
    try:
        member = services.register_member(
            patient_id, email, line_user_id=str(body.get("line_user_id") or "").strip(),
            first_name=str(body.get("first_name") or ""), last_name=str(body.get("last_name") or ""))
    except PermissionError as e:
        return JsonResponse({"error": str(e)}, status=403)
    except Exception as e:
        log.exception("register failed")
        return JsonResponse({"error": f"{type(e).__name__}: {e}"}, status=502)
    return JsonResponse({"patient_id": member.patient_id,
                         "shopify_customer_id": member.shopify_customer_id,
                         "rx_lines": len((member.rx_snapshot or {}).get("lines", []))})


@csrf_exempt
@require_POST
def rx_refresh(request):
    if not _api_authorized(request):
        return JsonResponse({"error": "unauthorized"}, status=401)
    patient_id = str(_json_body(request).get("patient_id") or "").strip()
    if not patient_id:
        return JsonResponse({"error": "patient_id is required"}, status=400)
    member = Member.objects.filter(patient_id=patient_id, active=True).first()
    if member is None:
        return JsonResponse({"status": "not_a_member"}, status=200)
    try:
        payload = services.sync_member(member)
    except Exception as e:
        return JsonResponse({"error": f"{type(e).__name__}: {e}"}, status=502)
    return JsonResponse({"status": "synced", "rx_lines": len(payload["lines"])})


@require_GET
def healthz(request):
    try:
        with connection.cursor() as cur:
            cur.execute("SELECT 1")
        return JsonResponse({"status": "ok"})
    except Exception as e:
        return JsonResponse({"status": "db_error", "error": str(e)}, status=503)
