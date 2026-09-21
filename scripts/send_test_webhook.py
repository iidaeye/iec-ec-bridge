#!/usr/bin/env python
"""署名付きのテスト Webhook を連携サービスに送る（開発ストアを使わずに動作確認するため）。

例:
  export SHOPIFY_WEBHOOK_SECRET=...   # サービス側と同じ値
  python scripts/send_test_webhook.py http://localhost:8000 --customer 5001 --rx-line-r line-R --rx-line-l line-L
  python scripts/send_test_webhook.py http://localhost:8000 --cancel 1001
"""
import argparse
import json
import os
import sys
import uuid
from datetime import datetime, timezone, timedelta

import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from iec_ec_bridge.core.shopify_hmac import compute_hmac  # noqa: E402

JST = timezone(timedelta(hours=9))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("base_url")
    ap.add_argument("--order-id", type=int, default=1001)
    ap.add_argument("--customer", default="5001", help="Shopify 顧客ID（Member に登録済みのもの）")
    ap.add_argument("--sku", default="1D-SPH")
    ap.add_argument("--qty", type=int, default=2)
    ap.add_argument("--rx-line", default="", help="注文明細の _rx_line_id")
    ap.add_argument("--spec", default="", help='規格 JSON 例: {"bc":"8.5","dia":"14.2","pwr":"-3.25"}')
    ap.add_argument("--shipping", default="", help="配送方法名（空なら院内受取扱い）")
    ap.add_argument("--cancel", type=int, help="この注文IDの orders/cancelled を送る")
    a = ap.parse_args()

    secret = os.environ.get("SHOPIFY_WEBHOOK_SECRET", "")
    if not secret:
        sys.exit("SHOPIFY_WEBHOOK_SECRET を環境変数に設定してください")

    if a.cancel:
        topic, path = "orders/cancelled", "/webhooks/shopify/orders-cancelled"
        payload = {"id": a.cancel, "name": f"#{a.cancel}", "cancelled_at": datetime.now(JST).isoformat()}
    else:
        topic, path = "orders/create", "/webhooks/shopify/orders-create"
        props = [{"name": "_rx_line_id", "value": a.rx_line}] if a.rx_line else []
        props += [{"name": k, "value": v} for k, v in (json.loads(a.spec) if a.spec else {}).items()]
        payload = {
            "id": a.order_id, "name": f"#{a.order_id}", "created_at": datetime.now(JST).isoformat(),
            "customer": {"id": int(a.customer), "email": "test@example.com"},
            "line_items": [{"id": 1, "sku": a.sku, "quantity": a.qty, "product_id": 1, "properties": props}],
            "shipping_lines": [{"code": a.shipping, "title": a.shipping}] if a.shipping else [],
            "shipping_address": {"name": "テスト 太郎", "zip": "500-0000", "province": "岐阜県",
                                 "city": "岐阜市", "address1": "1-2-3"},
        }
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    r = requests.post(a.base_url.rstrip("/") + path, data=body, timeout=30, headers={
        "Content-Type": "application/json",
        "X-Shopify-Topic": topic,
        "X-Shopify-Hmac-Sha256": compute_hmac(secret, body),
        "X-Shopify-Webhook-Id": str(uuid.uuid4()),
        "X-Shopify-Shop-Domain": "iida-eye-shop-dev.myshopify.com",
    })
    print(r.status_code, r.text)


if __name__ == "__main__":
    main()
