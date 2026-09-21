import json
from datetime import date

import pytest

from bridge import services
from bridge.models import Member
from iec_ec_bridge.core.models import Rx
from tests.fakes import L, R, RX, FakeRepo

pytestmark = pytest.mark.django_db


def test_metafield_payload_has_specs_and_remaining(repo):
    repo.add_clinic_dispense("line-R", 3)
    p = services.rx_metafield_payload(repo, "P001", today=date(2026, 11, 1))
    r, l = p["lines"]
    assert r["eye"] == "R" and r["pwr"] == "-3.25" and r["bc"] == "8.5" and r["remaining_boxes"] == 5
    assert r["variant_id"] == "gid://shopify/ProductVariant/111" and r["valid_until"] == "2027-04-01"
    assert l["cyl"] == "-0.75" and l["ax"] == "180" and l["remaining_boxes"] == 4
    assert json.dumps(p, ensure_ascii=False)   # JSON 化できる


def test_10_ec_not_allowed_rx_is_not_listed():
    repo = FakeRepo(rxs=[Rx("rx1", "P001", RX.rx_date, RX.valid_until, "active", False, (R, L))])
    assert services.rx_metafield_payload(repo, "P001", today=date(2026, 11, 1))["lines"] == []


def test_03_only_eye_with_rx_is_listed():
    repo = FakeRepo(rxs=[Rx("rx1", "P001", RX.rx_date, RX.valid_until, "active", True, (R,))])
    lines = services.rx_metafield_payload(repo, "P001", today=date(2026, 11, 1))["lines"]
    assert [l["eye"] for l in lines] == ["R"]


def test_expired_rx_is_not_listed(repo):
    assert services.rx_metafield_payload(repo, "P001", today=date(2027, 4, 2))["lines"] == []


def test_sync_all_members_records_errors(repo, shopify):
    Member.objects.create(patient_id="P001", shopify_customer_id="1")
    bad = Member.objects.create(patient_id="P002", shopify_customer_id="2")
    orig = shopify.set_customer_metafields

    def flaky(cid, mfs):
        if cid == "2":
            raise RuntimeError("boom")
        orig(cid, mfs)
    shopify.set_customer_metafields = flaky
    res = services.sync_all_members()
    assert res == {"ok": 1, "failed": 1}
    bad.refresh_from_db()
    assert "boom" in bad.last_sync_error
    assert Member.objects.get(patient_id="P001").last_synced_at is not None
