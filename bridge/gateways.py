"""設定から FileMaker / Shopify クライアントを組み立てる。テストでは差し替える。"""
from django.conf import settings

from iec_ec_bridge.fm import FileMakerClient, RxRepository
from iec_ec_bridge.shopify import ShopifyAdminClient

_overrides = {}


def set_overrides(repo=None, shopify=None):
    """テスト用: 模擬のリポジトリ／Shopify クライアントを差し込む。"""
    _overrides["repo"] = repo
    _overrides["shopify"] = shopify


def clear_overrides():
    _overrides.clear()


def get_repo() -> RxRepository:
    if _overrides.get("repo") is not None:
        return _overrides["repo"]
    client = FileMakerClient(settings.FM_HOST, settings.FM_DATABASE, settings.FM_USER,
                             settings.FM_PASSWORD, verify_tls=settings.FM_VERIFY_TLS)
    return RxRepository(client, layouts=settings.FM_LAYOUTS, date_format=settings.FM_DATE_FORMAT,
                        rx_portal_name=settings.FM_RX_PORTAL_NAME,
                        linelink_line_field=settings.FM_LINELINK_LINE_FIELD,
                        linelink_patient_field=settings.FM_LINELINK_PATIENT_FIELD)


def get_shopify() -> ShopifyAdminClient:
    if _overrides.get("shopify") is not None:
        return _overrides["shopify"]
    return ShopifyAdminClient(settings.SHOPIFY_SHOP_DOMAIN, settings.SHOPIFY_ADMIN_TOKEN,
                              settings.SHOPIFY_API_VERSION)
