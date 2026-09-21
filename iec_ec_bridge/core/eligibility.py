"""注文と処方の照合。純粋関数のみ。"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Iterable, List, Mapping, Optional

from .models import (PROP_RX_LINE_ID, SPEC_KEYS, Dispense, Order, OrderLine,
                     Product, Rx, RxLine)
from .quantity import DEFAULT_SPARE_BOXES, remaining_boxes

# 理由コード
OK = "ok"
NOT_A_PATIENT = "not_a_patient"
UNKNOWN_PRODUCT = "unknown_product"
RX_MISSING = "rx_missing"
RX_NOT_OWNED = "rx_not_owned"
RX_NOT_ACTIVE = "rx_not_active"
EC_NOT_ALLOWED = "ec_not_allowed"
RX_EXPIRED = "rx_expired"
PRODUCT_MISMATCH = "product_mismatch"
SPEC_MISMATCH = "spec_mismatch"
QUANTITY_EXCEEDED = "quantity_exceeded"
BAD_QUANTITY = "bad_quantity"

_NUMERIC_KEYS = ("bc", "dia", "pwr", "cyl", "ax")
_Q = Decimal("0.01")


@dataclass(frozen=True)
class LineResult:
    index: int
    sku: str
    reason: str
    rx_line_id: Optional[str] = None
    remaining: Optional[int] = None   # quantity_exceeded のとき購入可能な残り箱数

    @property
    def ok(self) -> bool:
        return self.reason == OK


@dataclass(frozen=True)
class OrderResult:
    order_id: str
    lines: List[LineResult]

    @property
    def ok(self) -> bool:
        return all(l.ok for l in self.lines)

    @property
    def reasons(self) -> List[str]:
        return sorted({l.reason for l in self.lines if not l.ok})


def _norm_num(value) -> Optional[Decimal]:
    if value is None:
        return None
    s = str(value).strip().replace("＋", "+").replace("−", "-").replace("－", "-")
    if s == "":
        return None
    try:
        return Decimal(s).quantize(_Q)
    except InvalidOperation:
        return Decimal("NaN")  # 数値でない入力はどの処方とも一致させない


def _norm_text(value) -> Optional[str]:
    if value is None:
        return None
    s = str(value).strip().lower()
    return s or None


def spec_matches(rx_line: RxLine, properties: Mapping[str, str]) -> bool:
    """注文明細のプロパティが処方明細の規格と完全一致するか。"""
    for key in SPEC_KEYS:
        rx_val = getattr(rx_line, key)
        ord_val = properties.get(key)
        if key in _NUMERIC_KEYS:
            a, b = _norm_num(rx_val), _norm_num(ord_val)
            if a is None and b is None:
                continue
            if a is None or b is None or a.is_nan() or b.is_nan() or a != b:
                return False
        else:
            if _norm_text(rx_val) != _norm_text(ord_val):
                return False
    return True


def verify_order(order: Order,
                 products: Mapping[str, Product],
                 rx_by_line_id: Mapping[str, Rx],
                 dispenses: Iterable[Dispense],
                 spare_boxes: int = DEFAULT_SPARE_BOXES) -> OrderResult:
    """注文全体を照合する。1 行でも NG なら OrderResult.ok は False。

    rx_by_line_id: rx_line_id -> その明細を含む処方ヘッダ。
    """
    dispenses = list(dispenses)
    results: List[Optional[LineResult]] = [None] * len(order.lines)
    pending_qty = defaultdict(list)  # rx_line_id -> [(index, line)]

    for i, line in enumerate(order.lines):
        def ng(reason, rx_line_id=None, remaining=None, _i=i, _line=line):
            results[_i] = LineResult(_i, _line.sku, reason, rx_line_id, remaining)

        if order.patient_id is None:
            ng(NOT_A_PATIENT); continue
        if line.quantity <= 0:
            ng(BAD_QUANTITY); continue
        product = products.get(line.sku)
        if product is None:
            ng(UNKNOWN_PRODUCT); continue
        if not product.requires_rx:
            results[i] = LineResult(i, line.sku, OK); continue

        rx_line_id = (line.properties.get(PROP_RX_LINE_ID) or "").strip()
        rx = rx_by_line_id.get(rx_line_id) if rx_line_id else None
        if rx is None:
            ng(RX_MISSING); continue
        rx_line = next((l for l in rx.lines if l.rx_line_id == rx_line_id), None)
        if rx_line is None:
            ng(RX_MISSING); continue
        if rx.patient_id != order.patient_id:
            ng(RX_NOT_OWNED, rx_line_id); continue
        if rx.status != "active":
            ng(RX_NOT_ACTIVE, rx_line_id); continue
        if not rx.ec_allowed:
            ng(EC_NOT_ALLOWED, rx_line_id); continue
        if order.order_date > rx.valid_until:
            ng(RX_EXPIRED, rx_line_id); continue
        if rx_line.product_code != line.sku:
            ng(PRODUCT_MISMATCH, rx_line_id); continue
        if not spec_matches(rx_line, line.properties):
            ng(SPEC_MISMATCH, rx_line_id); continue
        pending_qty[rx_line_id].append((i, line))

    # 数量は同じ処方明細を指す行を合算して判定する
    for rx_line_id, items in pending_qty.items():
        rx = rx_by_line_id[rx_line_id]
        product = products[items[0][1].sku]
        remaining = remaining_boxes(rx.rx_date, rx.valid_until, product.days_per_box,
                                    dispenses, rx_line_id, spare_boxes,
                                    exclude_order_id=order.order_id)
        total = sum(l.quantity for _, l in items)
        for i, line in items:
            if total <= remaining:
                results[i] = LineResult(i, line.sku, OK, rx_line_id, remaining)
            else:
                results[i] = LineResult(i, line.sku, QUANTITY_EXCEEDED, rx_line_id, remaining)

    return OrderResult(order.order_id, [r for r in results if r is not None])
