"""Django 設定。認証情報はすべて環境変数から読む（Dokku の config:set）。"""
import os
from pathlib import Path

import dj_database_url

BASE_DIR = Path(__file__).resolve().parent.parent


def env(name, default=None):
    return os.environ.get(name, default)


def env_bool(name, default=False):
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def env_int(name, default):
    v = os.environ.get(name)
    return int(v) if v not in (None, "") else default


def env_list(name, default=()):
    v = os.environ.get(name)
    if not v:
        return list(default)
    return [x.strip() for x in v.split(",") if x.strip()]


SECRET_KEY = env("DJANGO_SECRET_KEY") or "dev-only-insecure-key"
DEBUG = env_bool("DJANGO_DEBUG", False)
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", ["localhost", "127.0.0.1"])
CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "bridge",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [],
    "APP_DIRS": True,
    "OPTIONS": {"context_processors": [
        "django.template.context_processors.request",
        "django.contrib.auth.context_processors.auth",
        "django.contrib.messages.context_processors.messages",
    ]},
}]

DATABASES = {
    "default": dj_database_url.config(
        default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}", conn_max_age=60)
}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LANGUAGE_CODE = "ja"
TIME_ZONE = "Asia/Tokyo"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

# Dokku のリバースプロキシ配下
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
USE_X_FORWARDED_HOST = True

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"plain": {"format": "%(asctime)s %(levelname)s %(name)s: %(message)s"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "plain"}},
    "root": {"handlers": ["console"], "level": env("LOG_LEVEL", "INFO")},
}

# ---- Shopify ----
SHOPIFY_SHOP_DOMAIN = env("SHOPIFY_SHOP_DOMAIN", "")
SHOPIFY_ADMIN_TOKEN = env("SHOPIFY_ADMIN_TOKEN", "")
SHOPIFY_WEBHOOK_SECRET = env("SHOPIFY_WEBHOOK_SECRET", "")
SHOPIFY_API_VERSION = env("SHOPIFY_API_VERSION", "2025-07")
# このタグが付いた Shopify 商品は処方必須とみなす（CL_Product 未登録なら注文 NG）
SHOPIFY_RX_REQUIRED_TAG = env("SHOPIFY_RX_REQUIRED_TAG", "rx-required")
# 顧客メタフィールド（テーマの「あなたの処方レンズ」ページが読む）
SHOPIFY_METAFIELD_NAMESPACE = env("SHOPIFY_METAFIELD_NAMESPACE", "iec")

# ---- FileMaker Data API ----
FM_HOST = env("FM_HOST", "")
FM_DATABASE = env("FM_DATABASE", "Helm_cData")
FM_USER = env("FM_USER", "")
FM_PASSWORD = env("FM_PASSWORD", "")
FM_VERIFY_TLS = env_bool("FM_VERIFY_TLS", True)
FM_DATE_FORMAT = env("FM_DATE_FORMAT", "%m/%d/%Y")
FM_LAYOUTS = {
    "product": env("FM_LAYOUT_PRODUCT", "api_CL_Product"),
    "rx": env("FM_LAYOUT_RX", "api_CL_Rx"),
    "rxline": env("FM_LAYOUT_RXLINE", "api_CL_RxLine"),
    "dispense": env("FM_LAYOUT_DISPENSE", "api_CL_Dispense"),
    "linelink": env("FM_LAYOUT_LINELINK", "api_LineLink"),
}
FM_RX_PORTAL_NAME = env("FM_RX_PORTAL_NAME", "lines")
FM_LINELINK_LINE_FIELD = env("FM_LINELINK_LINE_FIELD", "line_user_id")
FM_LINELINK_PATIENT_FIELD = env("FM_LINELINK_PATIENT_FIELD", "patient_id")

# ---- /api/* の認証 ----
API_SHARED_KEY = env("API_SHARED_KEY", "")
API_ALLOWED_IPS = env_list("API_ALLOWED_IPS")

# ---- 業務設定 ----
SHIPPING_ENABLED = env_bool("SHIPPING_ENABLED", False)
SPARE_BOXES = env_int("SPARE_BOXES", 1)
PENDING_ALERT_MINUTES = env_int("PENDING_ALERT_MINUTES", 30)
WORKER_POLL_SECONDS = env_int("WORKER_POLL_SECONDS", 5)
# True にすると Webhook を受けたリクエスト内で処理する（ワーカーを動かさない小規模構成・テスト用）
WEBHOOK_PROCESS_INLINE = env_bool("WEBHOOK_PROCESS_INLINE", False)
# 配送方法名の判定: この語を含む配送方法は院内受取
PICKUP_KEYWORDS = env_list("PICKUP_KEYWORDS", ["受取", "受け取り", "pickup", "Pickup"])

CLINIC_NAME = env("CLINIC_NAME", "医療法人 イイダ眼科医院")
CLINIC_ADDRESS = env("CLINIC_ADDRESS", "")
CLINIC_TEL = env("CLINIC_TEL", "")
CLINIC_FAX = env("CLINIC_FAX", "")

PO_OUTPUT_DIR = env("PO_OUTPUT_DIR", str(BASE_DIR / "var" / "po"))
PO_METHOD = env("PO_METHOD", "fax_pdf")          # fax_pdf / email
PO_EMAIL_FROM = env("PO_EMAIL_FROM", "")
PO_SUPPLIER_EMAILS = dict(
    kv.split("=", 1) for kv in env("PO_SUPPLIER_EMAILS", "").split(";") if "=" in kv)
PO_FONT_PATH = env("PO_FONT_PATH", "")            # 日本語 TTF を使いたい場合のみ

STAFF_NOTIFY_EMAIL = env("STAFF_NOTIFY_EMAIL", "")
EMAIL_BACKEND = env("EMAIL_BACKEND", "django.core.mail.backends.console.EmailBackend")
EMAIL_HOST = env("EMAIL_HOST", "")
EMAIL_PORT = env_int("EMAIL_PORT", 587)
EMAIL_HOST_USER = env("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", True)
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", PO_EMAIL_FROM or "noreply@localhost")
