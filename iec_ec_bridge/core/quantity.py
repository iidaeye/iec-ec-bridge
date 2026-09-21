"""購入数量の上限計算。"""
from __future__ import annotations

import math
from datetime import date
from typing import Iterable, Optional

from .models import Dispense

DEFAULT_SPARE_BOXES = 1


def period_cap(rx_date: date, valid_until: date, days_per_box: int,
               spare_boxes: int = DEFAULT_SPARE_BOXES) -> int:
    """有効期間全体で片眼に交付できる箱数の上限。"""
    if days_per_box <= 0:
        raise ValueError("days_per_box must be positive")
    days = (valid_until - rx_date).days
    if days <= 0:
        return 0
    return math.ceil(days / days_per_box) + spare_boxes


def used_boxes(dispenses: Iterable[Dispense], rx_line_id: str,
               exclude_order_id: Optional[str] = None) -> int:
    """交付済み・注文中の箱数。trial と cancelled は数えない。

    exclude_order_id: Webhook 再送時に同じ注文を二重に数えないための除外指定。
    """
    total = 0
    for d in dispenses:
        if d.rx_line_id != rx_line_id or d.kind != "sale" or d.state == "cancelled":
            continue
        if exclude_order_id is not None and d.shopify_order_id == exclude_order_id:
            continue
        total += d.boxes
    return total


def remaining_boxes(rx_date: date, valid_until: date, days_per_box: int,
                    dispenses: Iterable[Dispense], rx_line_id: str,
                    spare_boxes: int = DEFAULT_SPARE_BOXES,
                    exclude_order_id: Optional[str] = None) -> int:
    cap = period_cap(rx_date, valid_until, days_per_box, spare_boxes)
    return max(0, cap - used_boxes(dispenses, rx_line_id, exclude_order_id))
