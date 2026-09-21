"""通知の差し込み口。患者への LINE 通知は次の段階で実装するため、いまはログとスタッフ向けメールのみ。

呼び出し側はこのモジュールの関数だけを使う。LINE Messaging API を足すときはここを差し替える。
"""
import logging

from django.conf import settings
from django.core.mail import send_mail

log = logging.getLogger(__name__)

REASON_TEXT = {
    "rx_expired": "処方の有効期限が切れています。受診のご予約をお願いします。",
    "rx_missing": "有効な処方が見つかりません。受診のご予約をお願いします。",
    "rx_not_active": "現在の処方ではご注文いただけません。受診のご予約をお願いします。",
    "ec_not_allowed": "この処方はオンライン注文の対象外です。受付までお問い合わせください。",
    "spec_mismatch": "ご注文の規格が処方と一致しません。",
    "product_mismatch": "ご注文の製品が処方と一致しません。",
    "quantity_exceeded": "ご注文の箱数が購入できる上限を超えています。",
    "not_a_patient": "当院の患者登録が確認できません。",
    "rx_not_owned": "処方の確認ができませんでした。",
    "unknown_product": "取り扱いを確認できない商品が含まれています。",
    "shipping_disabled": "現在、自宅発送は受け付けていません。院内受取をお選びください。",
}
STAFF_REASONS = {"spec_mismatch", "product_mismatch", "unknown_product", "rx_not_owned"}


def patient_order_rejected(patient_id: str, order_name: str, reasons, remaining=None) -> None:
    text = " ".join(REASON_TEXT.get(r, r) for r in reasons)
    if remaining is not None:
        text += f" 購入できる残りは {remaining} 箱です。"
    log.info("notify patient %s: order %s rejected: %s", patient_id, order_name, text)
    # TODO: LINE Messaging API で患者へ push（既存 LIFF と同じプロバイダー）


def patient_order_accepted(patient_id: str, order_name: str) -> None:
    log.info("notify patient %s: order %s accepted", patient_id, order_name)


def staff(subject: str, body: str) -> None:
    log.warning("STAFF NOTICE: %s\n%s", subject, body)
    to = settings.STAFF_NOTIFY_EMAIL
    if to:
        try:
            send_mail(f"[EC連携] {subject}", body, settings.DEFAULT_FROM_EMAIL, [to],
                      fail_silently=True)
        except Exception:  # メール障害で本処理を止めない
            log.exception("staff mail failed")
