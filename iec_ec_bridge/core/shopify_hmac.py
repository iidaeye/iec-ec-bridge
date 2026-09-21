"""Shopify Webhook の署名検証。"""
import base64
import hashlib
import hmac

HEADER = "X-Shopify-Hmac-Sha256"


def compute_hmac(secret: str, raw_body: bytes) -> str:
    digest = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).digest()
    return base64.b64encode(digest).decode("ascii")


def verify_webhook(secret: str, raw_body: bytes, header_value) -> bool:
    """raw_body は JSON に変換する前の生バイト列を渡すこと。"""
    if not secret or not header_value:
        return False
    expected = compute_hmac(secret, raw_body)
    return hmac.compare_digest(expected, str(header_value).strip())
