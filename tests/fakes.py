"""テスト用の模擬実装。外部サービス（FileMaker・Shopify）には接続しない。"""
from __future__ import annotations

from datetime import date
from decimal import Decimal as D
from typing import Dict, List

from iec_ec_bridge.core.models import Dispense, Rx, RxLine
from iec_ec_bridge.fm.client import FileMakerUnavailable
from iec_ec_bridge.fm.repo import DispenseRecord, ProductInfo
from iec_ec_bridge.shopify.client import ShopifyUnavailable

RX_DATE = date(2026, 10, 1)
VALID_UNTIL = date(2027, 4, 1)

PRODUCTS = {
    "1D-SPH": ProductInfo("p1", "1D-SPH", "メーカーA", "ワンデーA", "sphere", "1day", 30,
                          "卸X", "fax", False, True, "gid://shopify/ProductVariant/111"),
    "2W-TOR": ProductInfo("p2", "2W-TOR", "メーカーB", "2ウィークB トーリック", "toric", "2week", 84,
                          "メーカーB直販", "fax", True, True, ""),
}
R = RxLine("line-R", "rx1", "R", "1D-SPH", bc=D("8.5"), dia=D("14.2"), pwr=D("-3.25"))
L = RxLine("line-L", "rx1", "L", "2W-TOR", bc=D("8.6"), dia=D("14.5"), pwr=D("-2.00"),
           cyl=D("-0.75"), ax=D("180"))
RX = Rx("rx1", "P001", RX_DATE, VALID_UNTIL, "active", True, (R, L))


def props(line: RxLine, **override):
    p = {"_rx_line_id": line.rx_line_id}
    for k in ("bc", "dia", "pwr", "cyl", "ax", "add_power", "color"):
        v = getattr(line, k)
        if v is not None:
            p[k] = str(v)
    p.update(override)
    return [{"name": k, "value": v} for k, v in p.items()]


class FakeRepo:
    """RxRepository と同じメソッドを持つインメモリ実装。"""

    def __init__(self, rxs=(RX,), products=PRODUCTS, line_links=None):
        self.rxs: List[Rx] = list(rxs)
        self._products = dict(products)
        self.dispenses: Dict[str, DispenseRecord] = {}
        self.line_links = dict(line_links or {"U-line-1": "P001"})
        self.unavailable = False
        self._seq = 0
        self.calls: List[tuple] = []

    def _check(self):
        if self.unavailable:
            raise FileMakerUnavailable("connection refused")

    def products(self, active_only=True):
        self._check()
        return {k: v for k, v in self._products.items() if v.active or not active_only}

    def core_products(self, products):
        return {k: p.as_core() for k, p in products.items()}

    def rx_by_line_ids(self, line_ids):
        self._check()
        ids = set(line_ids)
        return {l.rx_line_id: rx for rx in self.rxs for l in rx.lines if l.rx_line_id in ids}

    def active_rx_for_patient(self, patient_id, on=None):
        self._check()
        on = on or date.today()
        return [rx for rx in self.rxs if rx.patient_id == patient_id and rx.status == "active"
                and rx.ec_allowed and rx.valid_until >= on]

    def dispenses_for_lines(self, line_ids):
        self._check()
        ids = set(line_ids)
        return [r.dispense for r in self.dispenses.values() if r.dispense.rx_line_id in ids]

    def dispenses_for_order(self, shopify_order_id):
        self._check()
        return [r for r in self.dispenses.values() if r.dispense.shopify_order_id == shopify_order_id]

    def add_clinic_dispense(self, rx_line_id, boxes, state="dispensed", kind="sale", patient_id="P001"):
        self._seq += 1
        rid = str(self._seq)
        self.dispenses[rid] = DispenseRecord(rid, Dispense(rx_line_id, kind, boxes, state), patient_id,
                                             "clinic", "", "pickup", None, None)
        return rid

    def create_ec_dispense(self, *, rx_line_id, patient_id, boxes, shopify_order_id, order_name,
                           delivery, ordered_at):
        self._check()
        self._seq += 1
        rid = str(self._seq)
        self.dispenses[rid] = DispenseRecord(
            rid, Dispense(rx_line_id, "sale", boxes, "verified", shopify_order_id), patient_id,
            "ec", order_name, delivery, ordered_at, None)
        self.calls.append(("create", rid, rx_line_id, boxes))
        return rid

    def set_dispense_state(self, record_id, state, **ts):
        self._check()
        r = self.dispenses[record_id]
        d = r.dispense
        self.dispenses[record_id] = DispenseRecord(
            r.record_id, Dispense(d.rx_line_id, d.kind, d.boxes, state, d.shopify_order_id),
            r.patient_id, r.channel, r.order_name, r.delivery, r.ordered_at,
            ts.get("po_sent_at", r.po_sent_at))
        self.calls.append(("state", record_id, state))

    def verified_ec_dispenses_awaiting_po(self):
        self._check()
        return [r for r in self.dispenses.values()
                if r.channel == "ec" and r.dispense.state == "verified" and r.po_sent_at is None]

    def patient_id_for_line_user(self, line_user_id):
        self._check()
        return self.line_links.get(line_user_id)


class FakeShopify:
    def __init__(self):
        self.cancelled: List[dict] = []
        self.metafields: Dict[str, dict] = {}
        self.customers: Dict[str, str] = {}   # email -> gid
        self.tags: Dict[str, List[str]] = {}
        self.product_tags_map: Dict[str, List[str]] = {}
        self.unavailable = False
        self._next_id = 9000

    def _check(self):
        if self.unavailable:
            raise ShopifyUnavailable("HTTP 503")

    @staticmethod
    def numeric_id(gid):
        return str(gid).rsplit("/", 1)[-1]

    @staticmethod
    def gid(kind, numeric_id):
        return f"gid://shopify/{kind}/{numeric_id}"

    def cancel_order(self, order_id, *, staff_note, reason="OTHER", refund=True, restock=False,
                     notify_customer=True):
        self._check()
        self.cancelled.append({"order_id": str(order_id), "note": staff_note, "refund": refund})
        return "gid://shopify/Job/1"

    def product_tags(self, product_ids):
        self._check()
        return {str(p): self.product_tags_map.get(str(p), []) for p in product_ids}

    def set_customer_metafields(self, customer_id, metafields):
        self._check()
        self.metafields.setdefault(str(customer_id), {}).update({m["key"]: m["value"] for m in metafields})

    def find_customer_by_email(self, email):
        self._check()
        return self.customers.get(email.lower())

    def create_customer(self, email, **kw):
        self._check()
        self._next_id += 1
        gid = self.gid("Customer", self._next_id)
        self.customers[email.lower()] = gid
        self.tags[gid] = list(kw.get("tags") or [])
        return gid

    def add_customer_tags(self, customer_id, tags):
        self._check()
        self.tags.setdefault(self.gid("Customer", customer_id), []).extend(tags)


def order_payload(order_id=1001, name="#1001", customer_id=5001, line_items=None,
                  created_at="2026-11-01T10:00:00+09:00", shipping=None, address=True):
    payload = {
        "id": order_id, "name": name, "created_at": created_at,
        "customer": {"id": customer_id, "email": "p001@example.com"},
        "line_items": line_items if line_items is not None else [
            {"id": 1, "sku": "1D-SPH", "quantity": 2, "product_id": 701, "properties": props(R)},
            {"id": 2, "sku": "2W-TOR", "quantity": 1, "product_id": 702, "properties": props(L)},
        ],
        "shipping_lines": [{"code": shipping, "title": shipping}] if shipping else [],
    }
    if address:
        payload["shipping_address"] = {"name": "山田 太郎", "zip": "500-0000", "province": "岐阜県",
                                       "city": "岐阜市", "address1": "1-2-3", "phone": "090-0000-0000"}
    return payload
