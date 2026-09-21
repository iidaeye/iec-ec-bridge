"""Shopify Admin API クライアントを模擬 HTTP セッションで確認する。"""
import pytest
import requests

from iec_ec_bridge.shopify.client import ShopifyAdminClient, ShopifyError, ShopifyUnavailable


class Resp:
    def __init__(self, body, status=200):
        self._body, self.status_code = body, status

    def json(self):
        return self._body


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, json=None, headers=None, timeout=None):
        self.calls.append((url, json, headers))
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def make(responses):
    s = FakeSession(responses)
    sleeps = []
    c = ShopifyAdminClient("iida-eye-shop-dev.myshopify.com", "shpat_x", "2025-07", session=s,
                           sleep=sleeps.append)
    return c, s, sleeps


def test_cancel_order_sends_mutation_with_refund():
    c, s, _ = make([Resp({"data": {"orderCancel": {"job": {"id": "gid://shopify/Job/1", "done": False},
                                                   "orderCancelUserErrors": []}}})])
    assert c.cancel_order(1001, staff_note="NG rx_expired") == "gid://shopify/Job/1"
    url, body, headers = s.calls[0]
    assert url == "https://iida-eye-shop-dev.myshopify.com/admin/api/2025-07/graphql.json"
    assert headers["X-Shopify-Access-Token"] == "shpat_x"
    assert "orderCancel(" in body["query"]
    assert body["variables"] == {"orderId": "gid://shopify/Order/1001", "reason": "OTHER", "refund": True,
                                 "restock": False, "notify": True, "note": "NG rx_expired"}


def test_user_errors_raise():
    c, _, _ = make([Resp({"data": {"orderCancel": {"job": None, "orderCancelUserErrors": [
        {"field": ["orderId"], "message": "Order is already cancelled", "code": "INVALID"}]}}})])
    with pytest.raises(ShopifyError, match="already cancelled"):
        c.cancel_order(1001, staff_note="x")


def test_retries_on_429_and_5xx_then_unavailable():
    c, s, sleeps = make([Resp({}, 429), Resp({}, 503), Resp({"data": {"nodes": []}})])
    assert c.product_tags([1]) == {}
    assert len(s.calls) == 3 and sleeps == [1, 2]
    c, _, _ = make([Resp({}, 503), Resp({}, 503), Resp({}, 503)])
    with pytest.raises(ShopifyUnavailable):
        c.product_tags([1])
    c, _, _ = make([requests.ConnectionError("x")] * 3)
    with pytest.raises(ShopifyUnavailable):
        c.product_tags([1])


def test_401_raises_configuration_error():
    c, _, _ = make([Resp({}, 401)])
    with pytest.raises(ShopifyError, match="token rejected"):
        c.product_tags([1])


def test_graphql_errors_raise():
    c, _, _ = make([Resp({"errors": [{"message": "Field 'x' doesn't exist"}]})])
    with pytest.raises(ShopifyError, match="doesn't exist"):
        c.product_tags([1])


def test_product_tags_maps_numeric_ids():
    c, s, _ = make([Resp({"data": {"nodes": [
        {"id": "gid://shopify/Product/701", "tags": ["rx-required", "cl"], "productType": "CL"}, None]}})])
    assert c.product_tags([701, "702"]) == {"701": ["rx-required", "cl"]}
    assert s.calls[0][1]["variables"]["ids"] == ["gid://shopify/Product/701", "gid://shopify/Product/702"]


def test_set_customer_metafields_adds_owner():
    c, s, _ = make([Resp({"data": {"metafieldsSet": {"metafields": [{"id": "m", "key": "rx_lines"}], "userErrors": []}}})])
    c.set_customer_metafields(5001, [{"namespace": "iec", "key": "rx_lines", "type": "json", "value": "{}"}])
    assert s.calls[0][1]["variables"]["metafields"] == [
        {"namespace": "iec", "key": "rx_lines", "type": "json", "value": "{}", "ownerId": "gid://shopify/Customer/5001"}]


def test_find_and_create_customer():
    c, s, _ = make([Resp({"data": {"customers": {"nodes": [{"id": "gid://shopify/Customer/1", "email": "A@x.jp"}]}}}),
                    Resp({"data": {"customers": {"nodes": []}}}),
                    Resp({"data": {"customerCreate": {"customer": {"id": "gid://shopify/Customer/2"}, "userErrors": []}}})])
    assert c.find_customer_by_email("a@x.jp") == "gid://shopify/Customer/1"
    assert c.find_customer_by_email("b@x.jp") is None
    gid = c.create_customer("b@x.jp", tags=["patient"], metafields=[{"namespace": "iec", "key": "patient_id",
                                                                       "type": "single_line_text_field", "value": "P1"}])
    assert gid == "gid://shopify/Customer/2"
    assert s.calls[2][1]["variables"]["input"]["tags"] == ["patient"]
    assert ShopifyAdminClient.numeric_id(gid) == "2"
