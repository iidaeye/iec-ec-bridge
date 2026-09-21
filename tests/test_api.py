import json

import pytest

from bridge.models import Member

pytestmark = pytest.mark.django_db
HEADERS = {"X-Api-Key": "test-api-key"}


def post(client, url, body, headers=HEADERS):
    return client.post(url, data=json.dumps(body), content_type="application/json", headers=headers)


def test_healthz(client):
    assert client.get("/healthz").json() == {"status": "ok"}


def test_api_requires_shared_key(client, shopify):
    assert post(client, "/api/rx/refresh", {"patient_id": "P001"}, headers={}).status_code == 401
    assert post(client, "/api/rx/refresh", {"patient_id": "P001"}, headers={"X-Api-Key": "nope"}).status_code == 401


def test_api_ip_allowlist(client, shopify, settings):
    settings.API_ALLOWED_IPS = ["203.0.113.5"]
    assert post(client, "/api/rx/refresh", {"patient_id": "P001"}).status_code == 401
    r = client.post("/api/rx/refresh", data=json.dumps({"patient_id": "P001"}), content_type="application/json",
                    headers=HEADERS, REMOTE_ADDR="203.0.113.5")
    assert r.status_code == 200 and r.json() == {"status": "not_a_member"}


def test_register_creates_customer_and_syncs(client, repo, shopify):
    r = post(client, "/api/members/register",
             {"patient_id": "P001", "email": "p001@example.com", "line_user_id": "U-line-1"})
    assert r.status_code == 200, r.content
    m = Member.objects.get(patient_id="P001")
    assert m.shopify_customer_id == "9001" and shopify.tags["gid://shopify/Customer/9001"] == ["patient"]
    assert r.json()["rx_lines"] == 2
    mf = shopify.metafields["9001"]
    assert mf["patient_id"] == "P001"
    lines = json.loads(mf["rx_lines"])["lines"]
    assert [(l["eye"], l["sku"], l["remaining_boxes"]) for l in lines] == [("R", "1D-SPH", 8), ("L", "2W-TOR", 4)]


def test_register_rejects_wrong_line_link(client, repo, shopify):
    r = post(client, "/api/members/register",
             {"patient_id": "P002", "email": "x@example.com", "line_user_id": "U-line-1"})
    assert r.status_code == 403 and Member.objects.count() == 0


def test_register_existing_email_reuses_customer(client, repo, shopify):
    shopify.customers["p001@example.com"] = "gid://shopify/Customer/5001"
    r = post(client, "/api/members/register", {"patient_id": "P001", "email": "P001@example.com"})
    assert r.status_code == 200
    assert Member.objects.get(patient_id="P001").shopify_customer_id == "5001"


def test_register_same_email_for_other_patient_is_refused(client, repo, shopify, member):
    shopify.customers["p001@example.com"] = "gid://shopify/Customer/5001"
    r = post(client, "/api/members/register", {"patient_id": "P002", "email": "p001@example.com"})
    assert r.status_code == 403


def test_rx_refresh_syncs_member(client, repo, shopify, member):
    r = post(client, "/api/rx/refresh", {"patient_id": "P001"})
    assert r.json() == {"status": "synced", "rx_lines": 2}
    member.refresh_from_db()
    assert member.last_synced_at is not None and len(member.rx_snapshot["lines"]) == 2
