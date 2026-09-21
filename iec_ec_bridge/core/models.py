"""照合ロジックが扱うデータ型。Shopify にも FileMaker にも依存しない。"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Mapping, Optional, Sequence

# 注文明細のプロパティ名（テーマ側と共有する定数）
PROP_RX_LINE_ID = "_rx_line_id"
SPEC_KEYS = ("bc", "dia", "pwr", "cyl", "ax", "add_power", "color")


@dataclass(frozen=True)
class Product:
    product_code: str          # Shopify の SKU と一致
    days_per_box: int          # 片眼 1 箱で使える日数
    requires_rx: bool = True   # アイケア用品などは False


@dataclass(frozen=True)
class RxLine:
    rx_line_id: str
    rx_id: str
    eye: str                   # "R" / "L"
    product_code: str
    bc: Optional[Decimal] = None
    dia: Optional[Decimal] = None
    pwr: Optional[Decimal] = None
    cyl: Optional[Decimal] = None
    ax: Optional[Decimal] = None
    add_power: Optional[str] = None
    color: Optional[str] = None


@dataclass(frozen=True)
class Rx:
    rx_id: str
    patient_id: str
    rx_date: date
    valid_until: date
    status: str                # trial / active / superseded / revoked
    ec_allowed: bool
    lines: Sequence[RxLine] = ()


@dataclass(frozen=True)
class Dispense:
    rx_line_id: str
    kind: str                  # sale / trial
    boxes: int
    state: str                 # verified / ordered / arrived / dispensed / cancelled
    shopify_order_id: Optional[str] = None


@dataclass(frozen=True)
class OrderLine:
    sku: str
    quantity: int
    properties: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Order:
    order_id: str
    patient_id: Optional[str]  # Shopify 顧客に紐づく患者ID。未紐づけなら None
    order_date: date
    lines: Sequence[OrderLine] = ()
