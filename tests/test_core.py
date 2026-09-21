"""開発仕様のテストシナリオ番号 (#n) に対応する単体テスト。"""
from datetime import date
from decimal import Decimal as D

import pytest

from iec_ec_bridge.core import eligibility as E
from iec_ec_bridge.core.models import (PROP_RX_LINE_ID, Dispense, Order, OrderLine,
                                        Product, Rx, RxLine)
from iec_ec_bridge.core.quantity import period_cap, remaining_boxes, used_boxes
from iec_ec_bridge.core.shopify_hmac import compute_hmac, verify_webhook

PRODUCTS = {
    "1D-SPH": Product("1D-SPH", 30),
    "2W-TOR": Product("2W-TOR", 84),
    "EYE-SHAMPOO": Product("EYE-SHAMPOO", 1, requires_rx=False),
}
RX_DATE = date(2026, 10, 1)
VALID_UNTIL = date(2027, 4, 1)   # 6 か月 = 182 日 → 1day は ceil(182/30)+1 = 8 箱

R = RxLine("line-R", "rx1", "R", "1D-SPH", bc=D("8.5"), dia=D("14.2"), pwr=D("-3.25"))
L = RxLine("line-L", "rx1", "L", "2W-TOR", bc=D("8.6"), dia=D("14.5"), pwr=D("-2.00"),
           cyl=D("-0.75"), ax=D("180"))


def make_rx(**kw):
    base = dict(rx_id="rx1", patient_id="P001", rx_date=RX_DATE, valid_until=VALID_UNTIL,
                status="active", ec_allowed=True, lines=(R, L))
    base.update(kw)
    return Rx(**base)


def index(rx):
    return {l.rx_line_id: rx for l in rx.lines}


def props(line, **override):
    p = {PROP_RX_LINE_ID: line.rx_line_id}
    for k in ("bc", "dia", "pwr", "cyl", "ax", "add_power", "color"):
        v = getattr(line, k)
        if v is not None:
            p[k] = str(v)
    p.update(override)
    return p


def order(lines, patient="P001", day=date(2026, 11, 1), oid="1001"):
    return Order(oid, patient, day, tuple(lines))


def verify(o, rx=None, dispenses=()):
    return E.verify_order(o, PRODUCTS, index(rx or make_rx()), dispenses)


# --- 照合 ---------------------------------------------------------------

def test_01_02_both_eyes_different_products_ok():
    res = verify(order([OrderLine("1D-SPH", 2, props(R)), OrderLine("2W-TOR", 1, props(L))]))
    assert res.ok and [l.rx_line_id for l in res.lines] == ["line-R", "line-L"]


def test_03_eye_without_rx_cannot_be_ordered():
    rx = make_rx(lines=(R,))
    res = verify(order([OrderLine("2W-TOR", 1, props(L))]), rx)
    assert res.reasons == [E.RX_MISSING]


def test_04_day_after_expiry_is_rejected():
    res = verify(order([OrderLine("1D-SPH", 1, props(R))], day=date(2027, 4, 2)))
    assert res.reasons == [E.RX_EXPIRED]


def test_05_expiry_day_is_ok():
    assert verify(order([OrderLine("1D-SPH", 1, props(R))], day=VALID_UNTIL)).ok


@pytest.mark.parametrize("override", [
    {"pwr": "-3.50"}, {"bc": "8.6"}, {"cyl": "-0.75"}, {"pwr": "abc"}, {"pwr": ""},
])
def test_06_tampered_spec_is_rejected(override):
    res = verify(order([OrderLine("1D-SPH", 1, props(R, **override))]))
    assert res.reasons == [E.SPEC_MISMATCH]


def test_06_toric_axis_tamper_and_missing_cyl():
    assert verify(order([OrderLine("2W-TOR", 1, props(L, ax="90"))])).reasons == [E.SPEC_MISMATCH]
    p = props(L); del p["cyl"]
    assert verify(order([OrderLine("2W-TOR", 1, p)])).reasons == [E.SPEC_MISMATCH]


def test_spec_format_variants_match():
    # "-3.25" / "-3.250" / 全角マイナス / 前後空白 は同じ値として扱う
    for pwr in ("-3.250", " -3.25 ", "−3.25"):
        assert verify(order([OrderLine("1D-SPH", 1, props(R, pwr=pwr))])).ok


def test_sku_swapped_to_other_product():
    res = verify(order([OrderLine("2W-TOR", 1, props(R))]))
    assert res.reasons == [E.PRODUCT_MISMATCH]


def test_09_superseded_rx_is_rejected():
    res = verify(order([OrderLine("1D-SPH", 1, props(R))]), make_rx(status="superseded"))
    assert res.reasons == [E.RX_NOT_ACTIVE]


def test_trial_rx_is_not_purchasable():
    res = verify(order([OrderLine("1D-SPH", 1, props(R))]), make_rx(status="trial"))
    assert res.reasons == [E.RX_NOT_ACTIVE]


def test_10_ec_not_allowed():
    res = verify(order([OrderLine("1D-SPH", 1, props(R))]), make_rx(ec_allowed=False))
    assert res.reasons == [E.EC_NOT_ALLOWED]


def test_other_patients_rx_line_id_is_rejected():
    res = verify(order([OrderLine("1D-SPH", 1, props(R))], patient="P999"))
    assert res.reasons == [E.RX_NOT_OWNED]


def test_unlinked_customer_cannot_buy_anything():
    res = verify(order([OrderLine("EYE-SHAMPOO", 1)], patient=None))
    assert res.reasons == [E.NOT_A_PATIENT]


def test_15_eyecare_only_needs_no_rx():
    assert verify(order([OrderLine("EYE-SHAMPOO", 3)])).ok


def test_one_bad_line_fails_whole_order():
    res = verify(order([OrderLine("EYE-SHAMPOO", 1), OrderLine("1D-SPH", 1, props(R, pwr="-9.00"))]))
    assert not res.ok and res.lines[0].ok and not res.lines[1].ok


def test_unknown_sku_and_bad_quantity():
    assert verify(order([OrderLine("NOPE", 1)])).reasons == [E.UNKNOWN_PRODUCT]
    assert verify(order([OrderLine("1D-SPH", 0, props(R))])).reasons == [E.BAD_QUANTITY]


# --- 数量上限 -----------------------------------------------------------

def test_period_cap_examples():
    assert period_cap(RX_DATE, VALID_UNTIL, 30) == 8        # 1day 6 か月
    assert period_cap(RX_DATE, VALID_UNTIL, 84) == 4        # 2week 6 か月: ceil(182/84)=3, +1
    assert period_cap(RX_DATE, date(2027, 1, 1), 30) == 5   # 1day 3 か月: ceil(92/30)=4, +1
    assert period_cap(RX_DATE, RX_DATE, 30) == 0
    with pytest.raises(ValueError):
        period_cap(RX_DATE, VALID_UNTIL, 0)


def test_07_exactly_cap_ok_and_cap_plus_one_rejected():
    assert verify(order([OrderLine("1D-SPH", 8, props(R))])).ok
    res = verify(order([OrderLine("1D-SPH", 9, props(R))]))
    assert res.reasons == [E.QUANTITY_EXCEEDED] and res.lines[0].remaining == 8


def test_08_clinic_dispense_is_deducted():
    d = [Dispense("line-R", "sale", 3, "dispensed")]
    assert verify(order([OrderLine("1D-SPH", 5, props(R))]), dispenses=d).ok
    res = verify(order([OrderLine("1D-SPH", 6, props(R))]), dispenses=d)
    assert res.reasons == [E.QUANTITY_EXCEEDED] and res.lines[0].remaining == 5


def test_11_cancelled_and_trial_do_not_count():
    d = [Dispense("line-R", "sale", 8, "cancelled"), Dispense("line-R", "trial", 0, "dispensed"),
         Dispense("line-L", "sale", 4, "dispensed")]
    assert used_boxes(d, "line-R") == 0
    assert verify(order([OrderLine("1D-SPH", 8, props(R))]), dispenses=d).ok


def test_split_lines_for_same_rx_line_are_summed():
    res = verify(order([OrderLine("1D-SPH", 5, props(R)), OrderLine("1D-SPH", 4, props(R))]))
    assert res.reasons == [E.QUANTITY_EXCEEDED]


def test_13_webhook_retry_does_not_double_count():
    d = [Dispense("line-R", "sale", 8, "verified", shopify_order_id="1001")]
    assert verify(order([OrderLine("1D-SPH", 8, props(R))], oid="1001"), dispenses=d).ok
    assert not verify(order([OrderLine("1D-SPH", 1, props(R))], oid="1002"), dispenses=d).ok
    assert remaining_boxes(RX_DATE, VALID_UNTIL, 30, d, "line-R") == 0


# --- Webhook 署名 -------------------------------------------------------

def test_13_hmac():
    body = '{"id":1001,"note":"日本語"}'.encode("utf-8")
    sig = compute_hmac("s3cret", body)
    assert verify_webhook("s3cret", body, sig)
    assert not verify_webhook("s3cret", body + b" ", sig)
    assert not verify_webhook("other", body, sig)
    assert not verify_webhook("s3cret", body, None)
    assert not verify_webhook("", body, sig)
    # 既知値との突き合わせ（実装の取り違え防止）
    assert compute_hmac("key", b"The quick brown fox jumps over the lazy dog") == \
        "97yD9DBThCSxMpjmqm+xQ+9NWaFJRhdZl0edvC0aPNg="
