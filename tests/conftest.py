import pytest

from bridge import gateways
from tests.fakes import FakeRepo, FakeShopify

WEBHOOK_SECRET = "test-webhook-secret"


@pytest.fixture(autouse=True)
def _settings(settings, tmp_path):
    settings.SHOPIFY_WEBHOOK_SECRET = WEBHOOK_SECRET
    settings.WEBHOOK_PROCESS_INLINE = True
    settings.API_SHARED_KEY = "test-api-key"
    settings.API_ALLOWED_IPS = []
    settings.SHIPPING_ENABLED = False
    settings.SPARE_BOXES = 1
    settings.PO_OUTPUT_DIR = str(tmp_path / "po")
    settings.STAFF_NOTIFY_EMAIL = ""
    yield


@pytest.fixture
def repo():
    r = FakeRepo()
    gateways.set_overrides(repo=r)
    yield r
    gateways.clear_overrides()


@pytest.fixture
def shopify(repo):
    s = FakeShopify()
    gateways.set_overrides(repo=repo, shopify=s)
    yield s
    gateways.clear_overrides()


@pytest.fixture
def member(db):
    from bridge.models import Member
    return Member.objects.create(patient_id="P001", shopify_customer_id="5001", email="p001@example.com")
