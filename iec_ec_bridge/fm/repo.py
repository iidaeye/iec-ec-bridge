"""FileMaker のレコードと照合コアのデータ型の橋渡し。

レイアウト名・日付書式は設定で差し替えられる。フィールド名は FileMaker 定義書と同じ
（英小文字＋アンダースコア）。ポータル経由の値は "CL_RxLine::pwr" のように
テーブル名の接頭辞が付くので、接頭辞の有無どちらでも読めるようにしている。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Iterable, List, Mapping, Optional

from ..core.models import Dispense, Product, Rx, RxLine
from .client import FileMakerClient

log = logging.getLogger(__name__)

DEFAULT_LAYOUTS = {
    "product": "api_CL_Product",
    "rx": "api_CL_Rx",
    "rxline": "api_CL_RxLine",
    "dispense": "api_CL_Dispense",
    "linelink": "api_LineLink",
}
_DATE_FORMATS = ("%m/%d/%Y", "%Y/%m/%d", "%Y-%m-%d", "%d.%m.%Y")


@dataclass(frozen=True)
class ProductInfo:
    """発注書などで使う製品マスタの全情報。照合には core.models.Product だけ渡す。"""
    product_id: str
    product_code: str
    maker: str
    product_name: str
    lens_type: str
    modality: str
    days_per_box: int
    supplier: str
    order_method: str
    direct_ship: bool
    active: bool
    shopify_variant_id: str = ""

    def as_core(self) -> Product:
        return Product(self.product_code, max(self.days_per_box, 1), requires_rx=True)


@dataclass(frozen=True)
class DispenseRecord:
    record_id: str
    dispense: Dispense
    patient_id: str
    channel: str
    order_name: str
    delivery: str
    ordered_at: Optional[datetime]
    po_sent_at: Optional[datetime]
    raw: Mapping[str, Any] = field(default_factory=dict)


class RxRepository:
    def __init__(self, client: FileMakerClient, layouts: Optional[Mapping[str, str]] = None,
                 date_format: str = "%m/%d/%Y", rx_portal_name: str = "lines",
                 linelink_line_field: str = "line_user_id",
                 linelink_patient_field: str = "patient_id"):
        self.c = client
        self.L = dict(DEFAULT_LAYOUTS, **(layouts or {}))
        self.date_format = date_format
        self.portal = rx_portal_name
        self.ll_line = linelink_line_field
        self.ll_patient = linelink_patient_field

    # ---- 製品マスタ -------------------------------------------------------
    def products(self, active_only: bool = True) -> Dict[str, ProductInfo]:
        """product_code (= Shopify SKU) -> ProductInfo"""
        out: Dict[str, ProductInfo] = {}
        for rec in self.c.get_all_records(self.L["product"]):
            p = _product(rec["fieldData"])
            if p is None or (active_only and not p.active):
                continue
            out[p.product_code] = p
        return out

    def core_products(self, products: Mapping[str, ProductInfo]) -> Dict[str, Product]:
        return {code: p.as_core() for code, p in products.items()}

    def set_variant_id(self, product_record_id: str, variant_id: str) -> None:
        self.c.edit(self.L["product"], product_record_id, {"shopify_variant_id": variant_id})

    # ---- 処方 -------------------------------------------------------------
    def rx_by_line_ids(self, line_ids: Iterable[str]) -> Dict[str, Rx]:
        """注文明細が指す rx_line_id ごとに、その明細を含む処方ヘッダ（全明細つき）を返す。"""
        ids = sorted({i for i in line_ids if i})
        if not ids:
            return {}
        rx_ids = set()
        for rec in self.c.find(self.L["rxline"], [{"rx_line_id": f"=={i}"} for i in ids],
                               limit=len(ids)):
            rx_ids.add(_s(rec["fieldData"].get("rx_id")))
        rx_ids.discard("")
        if not rx_ids:
            return {}
        rxs = self.c.find(self.L["rx"], [{"rx_id": f"=={i}"} for i in sorted(rx_ids)],
                          limit=len(rx_ids))
        out: Dict[str, Rx] = {}
        for rec in rxs:
            rx = self._rx(rec)
            for line in rx.lines:
                if line.rx_line_id in ids:
                    out[line.rx_line_id] = rx
        return out

    def active_rx_for_patient(self, patient_id: str, on: Optional[date] = None) -> List[Rx]:
        """EC で購入できる処方（active・ec_allowed・期限内）。"""
        on = on or date.today()
        q = [{"patient_id": f"=={patient_id}", "status": "==active", "ec_allowed": "1",
              "valid_until": f">={self.fmt_date(on)}"}]
        return [self._rx(rec) for rec in self.c.find(self.L["rx"], q, limit=50)]

    def _rx(self, rec: Mapping[str, Any]) -> Rx:
        f = rec["fieldData"]
        lines = []
        for p in (rec.get("portalData") or {}).get(self.portal, []):
            g = _strip_prefix(p)
            lines.append(RxLine(
                rx_line_id=_s(g.get("rx_line_id")), rx_id=_s(f.get("rx_id")),
                eye=_s(g.get("eye")).upper(),
                product_code=_s(g.get("product_code")) or _s(g.get("CL_Product::product_code")),
                bc=_dec(g.get("bc")), dia=_dec(g.get("dia")), pwr=_dec(g.get("pwr")),
                cyl=_dec(g.get("cyl")), ax=_dec(g.get("ax")),
                add_power=_s(g.get("add_power")) or None, color=_s(g.get("color")) or None))
        return Rx(
            rx_id=_s(f.get("rx_id")), patient_id=_s(f.get("patient_id")),
            rx_date=self.parse_date(f.get("rx_date")) or date.min,
            valid_until=self.parse_date(f.get("valid_until")) or date.min,
            status=_s(f.get("status")).lower(), ec_allowed=_truthy(f.get("ec_allowed")),
            lines=tuple(lines))

    # ---- 交付記録 -----------------------------------------------------------
    def dispenses_for_lines(self, line_ids: Iterable[str]) -> List[Dispense]:
        ids = sorted({i for i in line_ids if i})
        if not ids:
            return []
        recs = self.c.find_all(self.L["dispense"], [{"rx_line_id": f"=={i}"} for i in ids])
        return [self._dispense(r).dispense for r in recs]

    def dispenses_for_order(self, shopify_order_id: str) -> List[DispenseRecord]:
        recs = self.c.find(self.L["dispense"], [{"shopify_order_id": f"=={shopify_order_id}"}])
        return [self._dispense(r) for r in recs]

    def create_ec_dispense(self, *, rx_line_id: str, patient_id: str, boxes: int,
                           shopify_order_id: str, order_name: str, delivery: str,
                           ordered_at: datetime) -> str:
        return self.c.create(self.L["dispense"], {
            "rx_line_id": rx_line_id, "patient_id": patient_id, "kind": "sale",
            "channel": "ec", "shopify_order_id": shopify_order_id,
            "shopify_order_name": order_name, "boxes": boxes, "delivery": delivery,
            "state": "verified", "ordered_at": self.fmt_ts(ordered_at)})

    def set_dispense_state(self, record_id: str, state: str, **timestamps: datetime) -> None:
        data: Dict[str, Any] = {"state": state}
        for k, v in timestamps.items():
            data[k] = self.fmt_ts(v)
        self.c.edit(self.L["dispense"], record_id, data)

    def verified_ec_dispenses_awaiting_po(self) -> List[DispenseRecord]:
        recs = self.c.find_all(self.L["dispense"],
                               [{"state": "==verified", "channel": "==ec", "po_sent_at": "="}])
        return [self._dispense(r) for r in recs]

    def _dispense(self, rec: Mapping[str, Any]) -> DispenseRecord:
        f = rec["fieldData"]
        return DispenseRecord(
            record_id=str(rec.get("recordId", "")),
            dispense=Dispense(
                rx_line_id=_s(f.get("rx_line_id")), kind=_s(f.get("kind")).lower() or "sale",
                boxes=_int(f.get("boxes")), state=_s(f.get("state")).lower(),
                shopify_order_id=_s(f.get("shopify_order_id")) or None),
            patient_id=_s(f.get("patient_id")), channel=_s(f.get("channel")).lower(),
            order_name=_s(f.get("shopify_order_name")), delivery=_s(f.get("delivery")),
            ordered_at=self.parse_ts(f.get("ordered_at")),
            po_sent_at=self.parse_ts(f.get("po_sent_at")), raw=f)

    # ---- LINE ID 対応 ---------------------------------------------------------
    def patient_id_for_line_user(self, line_user_id: str) -> Optional[str]:
        recs = self.c.find(self.L["linelink"], [{self.ll_line: f"=={line_user_id}"}], limit=1)
        if not recs:
            return None
        return _s(recs[0]["fieldData"].get(self.ll_patient)) or None

    # ---- 日付 -------------------------------------------------------------------
    def fmt_date(self, d: date) -> str:
        return d.strftime(self.date_format)

    def fmt_ts(self, dt: datetime) -> str:
        return dt.strftime(self.date_format + " %H:%M:%S")

    def parse_date(self, v) -> Optional[date]:
        s = _s(v)
        if not s:
            return None
        for fmt in (self.date_format,) + _DATE_FORMATS:
            try:
                return datetime.strptime(s.split(" ")[0], fmt).date()
            except ValueError:
                continue
        log.warning("unparsable FileMaker date: %r", s)
        return None

    def parse_ts(self, v) -> Optional[datetime]:
        s = _s(v)
        if not s:
            return None
        for fmt in (self.date_format,) + _DATE_FORMATS:
            try:
                return datetime.strptime(s, fmt + " %H:%M:%S")
            except ValueError:
                continue
        d = self.parse_date(s)
        return datetime(d.year, d.month, d.day) if d else None


# ---- 値の変換 -----------------------------------------------------------------

def _s(v) -> str:
    return "" if v is None else str(v).strip()


def _dec(v) -> Optional[Decimal]:
    s = _s(v)
    if s == "":
        return None
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


def _int(v) -> int:
    d = _dec(v)
    return int(d) if d is not None else 0


def _truthy(v) -> bool:
    return _s(v) not in ("", "0", "0.0", "false", "False")


def _strip_prefix(portal_row: Mapping[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for k, v in portal_row.items():
        out[k] = v
        if "::" in k:
            out[k.split("::", 1)[1]] = v
    return out


def _product(f: Mapping[str, Any]) -> Optional[ProductInfo]:
    code = _s(f.get("product_code"))
    if not code:
        return None
    return ProductInfo(
        product_id=_s(f.get("product_id")), product_code=code, maker=_s(f.get("maker")),
        product_name=_s(f.get("product_name")), lens_type=_s(f.get("lens_type")),
        modality=_s(f.get("modality")), days_per_box=_int(f.get("days_per_box")),
        supplier=_s(f.get("supplier")) or "(発注先未設定)",
        order_method=_s(f.get("order_method")).lower() or "fax",
        direct_ship=_truthy(f.get("direct_ship")), active=_truthy(f.get("active")),
        shopify_variant_id=_s(f.get("shopify_variant_id")))
