"""FileMaker Data API クライアントとリポジトリを、模擬 HTTP セッションで確認する。"""
from datetime import date, datetime
from decimal import Decimal as D

import pytest
import requests

from iec_ec_bridge.fm.client import FileMakerClient, FileMakerError, FileMakerUnavailable
from iec_ec_bridge.fm.repo import RxRepository


class Resp:
    def __init__(self, body, status=200):
        self._body, self.status_code = body, status

    def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


def ok(response=None):
    return Resp({"messages": [{"code": "0", "message": "OK"}], "response": response or {}})


def err(code, message="", status=400):
    return Resp({"messages": [{"code": str(code), "message": message}], "response": {}}, status)


class FakeSession:
    """呼び出しを記録し、あらかじめ積んだ応答を順に返す。"""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, **kw):
        self.calls.append((method, url, kw.get("json"), kw.get("headers"), kw.get("auth")))
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def make_client(responses):
    s = FakeSession(responses)
    return FileMakerClient("fms.example.jp", "Helm_cData", "ec_bridge", "pw", session=s), s


def test_login_then_find_uses_bearer_token():
    c, s = make_client([ok({"token": "T1"}), ok({"data": [{"recordId": "5", "fieldData": {"a": 1}}]})])
    recs = c.find("api_CL_Rx", [{"rx_id": "==x"}], limit=10)
    assert recs == [{"recordId": "5", "fieldData": {"a": 1}}]
    method, url, body, headers, auth = s.calls[0]
    assert method == "POST" and url.endswith("/fmi/data/vLatest/databases/Helm_cData/sessions") and auth == ("ec_bridge", "pw")
    method, url, body, headers, _ = s.calls[1]
    assert url.endswith("/layouts/api_CL_Rx/_find") and headers["Authorization"] == "Bearer T1"
    assert body == {"query": [{"rx_id": "==x"}], "limit": "10", "offset": "1"}


def test_no_records_returns_empty_list():
    c, _ = make_client([ok({"token": "T1"}), err(401, "No records match the request")])
    assert c.find("api_CL_Rx", [{"rx_id": "==none"}]) == []


def test_expired_token_relogins_once():
    c, s = make_client([ok({"token": "T1"}), err(952, "Invalid token"), ok({"token": "T2"}),
                        ok({"data": []})])
    assert c.find("api_CL_Rx", [{"rx_id": "==x"}]) == []
    assert s.calls[-1][3]["Authorization"] == "Bearer T2"


def test_connection_error_is_unavailable():
    c, _ = make_client([requests.ConnectionError("refused")])
    with pytest.raises(FileMakerUnavailable):
        c.find("api_CL_Rx", [{"rx_id": "==x"}])
    c, _ = make_client([ok({"token": "T1"}), Resp({}, 503)])
    with pytest.raises(FileMakerUnavailable):
        c.find("api_CL_Rx", [{"rx_id": "==x"}])
    c, _ = make_client([err(212, "Invalid user account", 401)])
    with pytest.raises(FileMakerUnavailable):
        c.login()


def test_other_errors_raise_filemaker_error():
    c, _ = make_client([ok({"token": "T1"}), err(102, "Field is missing")])
    with pytest.raises(FileMakerError) as e:
        c.create("api_CL_Dispense", {"x": 1})
    assert e.value.code == "102"


def test_create_and_edit_paths():
    c, s = make_client([ok({"token": "T1"}), ok({"recordId": "77", "modId": "0"}), ok({"modId": "1"})])
    assert c.create("api_CL_Dispense", {"boxes": 2}) == "77"
    c.edit("api_CL_Dispense", "77", {"state": "ordered"})
    assert s.calls[1][0:3] == ("POST", c.base_url + "/layouts/api_CL_Dispense/records", {"fieldData": {"boxes": 2}})
    assert s.calls[2][0:3] == ("PATCH", c.base_url + "/layouts/api_CL_Dispense/records/77", {"fieldData": {"state": "ordered"}})


def test_find_all_pages_until_short_batch():
    page = [{"recordId": str(i), "fieldData": {}} for i in range(3)]
    c, s = make_client([ok({"token": "T1"}), ok({"data": page}), ok({"data": page[:1]})])
    assert len(list(c.find_all("L", [{"a": "1"}], page=3))) == 4
    assert s.calls[1][2]["offset"] == "1" and s.calls[2][2]["offset"] == "4"


# ---- リポジトリ ----------------------------------------------------------------

RX_RECORD = {
    "recordId": "10",
    "fieldData": {"rx_id": "rx1", "patient_id": "P001", "rx_date": "10/01/2026", "valid_until": "04/01/2027",
                  "status": "active", "ec_allowed": 1},
    "portalData": {"lines": [
        {"recordId": "1", "CL_RxLine::rx_line_id": "line-R", "CL_RxLine::eye": "R",
         "CL_RxLine::bc": 8.5, "CL_RxLine::dia": 14.2, "CL_RxLine::pwr": -3.25, "CL_RxLine::cyl": "",
         "CL_RxLine::ax": "", "CL_RxLine::add_power": "", "CL_RxLine::color": "",
         "CL_RxLine::CL_Product::product_code": "1D-SPH"},
        {"recordId": "2", "CL_RxLine::rx_line_id": "line-L", "CL_RxLine::eye": "L",
         "CL_RxLine::bc": "8.6", "CL_RxLine::dia": "14.5", "CL_RxLine::pwr": "-2", "CL_RxLine::cyl": "-0.75",
         "CL_RxLine::ax": 180, "CL_RxLine::add_power": "", "CL_RxLine::color": "",
         "CL_RxLine::product_code": "2W-TOR"},
    ]},
}


def test_repo_rx_by_line_ids_builds_rx_with_portal_lines():
    c, s = make_client([ok({"token": "T1"}),
                        ok({"data": [{"recordId": "1", "fieldData": {"rx_line_id": "line-R", "rx_id": "rx1"}}]}),
                        ok({"data": [RX_RECORD]})])
    repo = RxRepository(c)
    out = repo.rx_by_line_ids(["line-R"])
    assert list(out) == ["line-R"]
    rx = out["line-R"]
    assert rx.rx_date == date(2026, 10, 1) and rx.valid_until == date(2027, 4, 1)
    assert rx.status == "active" and rx.ec_allowed is True and rx.patient_id == "P001"
    r, l = rx.lines
    assert (r.rx_line_id, r.eye, r.product_code, r.pwr, r.cyl) == ("line-R", "R", "1D-SPH", D("-3.25"), None)
    assert (l.product_code, l.cyl, l.ax) == ("2W-TOR", D("-0.75"), D("180"))
    assert s.calls[1][2]["query"] == [{"rx_line_id": "==line-R"}]
    assert s.calls[2][2]["query"] == [{"rx_id": "==rx1"}]


def test_repo_active_rx_query_uses_configured_date_format():
    c, s = make_client([ok({"token": "T1"}), ok({"data": []})])
    repo = RxRepository(c, date_format="%Y/%m/%d")
    repo.active_rx_for_patient("P001", on=date(2026, 11, 1))
    assert s.calls[1][2]["query"] == [{"patient_id": "==P001", "status": "==active", "ec_allowed": "1",
                                       "valid_until": ">=2026/11/01"}]


def test_repo_parses_japanese_and_iso_dates():
    repo = RxRepository.__new__(RxRepository)
    repo.date_format = "%m/%d/%Y"
    assert repo.parse_date("2026/10/01") == date(2026, 10, 1)
    assert repo.parse_date("2026-10-01") == date(2026, 10, 1)
    assert repo.parse_date("10/01/2026 09:30:00") == date(2026, 10, 1)
    assert repo.parse_ts("10/01/2026 09:30:00") == datetime(2026, 10, 1, 9, 30)
    assert repo.parse_date("") is None


def test_repo_create_dispense_and_state():
    c, s = make_client([ok({"token": "T1"}), ok({"recordId": "9"}), ok({"modId": "2"})])
    repo = RxRepository(c)
    rid = repo.create_ec_dispense(rx_line_id="line-R", patient_id="P001", boxes=2, shopify_order_id="1001",
                                  order_name="#1001", delivery="pickup", ordered_at=datetime(2026, 11, 1, 10, 0))
    assert rid == "9"
    assert s.calls[1][2]["fieldData"] == {
        "rx_line_id": "line-R", "patient_id": "P001", "kind": "sale", "channel": "ec",
        "shopify_order_id": "1001", "shopify_order_name": "#1001", "boxes": 2, "delivery": "pickup",
        "state": "verified", "ordered_at": "11/01/2026 10:00:00"}
    repo.set_dispense_state("9", "ordered", po_sent_at=datetime(2026, 11, 2, 9, 0))
    assert s.calls[2][2]["fieldData"] == {"state": "ordered", "po_sent_at": "11/02/2026 09:00:00"}


def test_repo_products_and_awaiting_po():
    prod = {"recordId": "1", "fieldData": {"product_id": "p1", "product_code": "1D-SPH", "maker": "A",
                                             "product_name": "One", "days_per_box": 30, "supplier": "X",
                                             "direct_ship": 0, "active": 1, "order_method": "FAX"}}
    inactive = {"recordId": "2", "fieldData": {"product_code": "OLD", "days_per_box": 30, "active": 0}}
    disp = {"recordId": "3", "fieldData": {"rx_line_id": "line-R", "kind": "sale", "channel": "ec", "boxes": "2",
                                             "state": "verified", "shopify_order_id": "1001",
                                             "shopify_order_name": "#1001", "delivery": "pickup",
                                             "ordered_at": "11/01/2026 10:00:00", "po_sent_at": ""}}
    c, s = make_client([ok({"token": "T1"}), ok({"data": [prod, inactive]}), ok({"data": [disp]})])
    repo = RxRepository(c)
    ps = repo.products()
    assert list(ps) == ["1D-SPH"] and ps["1D-SPH"].order_method == "fax" and ps["1D-SPH"].as_core().days_per_box == 30
    recs = repo.verified_ec_dispenses_awaiting_po()
    assert recs[0].dispense.boxes == 2 and recs[0].po_sent_at is None and recs[0].ordered_at == datetime(2026, 11, 1, 10, 0)
    assert s.calls[2][2]["query"] == [{"state": "==verified", "channel": "==ec", "po_sent_at": "="}]


def test_repo_line_link_lookup_uses_configured_fields():
    c, s = make_client([ok({"token": "T1"}), ok({"data": [{"recordId": "1", "fieldData": {"LineUserID": "U1", "PatientNo": "P001"}}]})])
    repo = RxRepository(c, layouts={"linelink": "api_LineLink"}, linelink_line_field="LineUserID",
                        linelink_patient_field="PatientNo")
    assert repo.patient_id_for_line_user("U1") == "P001"
    assert s.calls[1][2]["query"] == [{"LineUserID": "==U1"}]
