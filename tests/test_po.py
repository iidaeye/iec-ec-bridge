"""発注書 PDF の生成と、交付記録の ordered 更新。"""
from datetime import date

import pytest

from bridge import po_service
from bridge.models import OrderVerification, PurchaseOrder
from iec_ec_bridge.po import EmailPoSender, PoDoc, PoLine, render_po_pdf


def test_render_po_pdf_writes_pdf(tmp_path):
    doc = PoDoc("PO20261102-01", "卸X", date(2026, 11, 2), "医療法人 イイダ眼科医院", [
        PoLine("1D-SPH", "メーカーA", "ワンデーA", "R", 2, "8.5", "14.2", "-3.25", order_name="#1001"),
        PoLine("2W-TOR", "メーカーB", "2ウィークB", "L", 1, "8.6", "14.5", "-2", "-0.75", "180",
               delivery="ship_direct", order_name="#1001",
               ship_to={"name": "山田 太郎", "zip": "500-0000", "city": "岐阜市", "address1": "1-2-3"}),
    ], clinic_tel="058-000-0000", clinic_fax="058-000-0001", note="テスト")
    path = render_po_pdf(doc, str(tmp_path / "po.pdf"))
    data = open(path, "rb").read()
    assert data.startswith(b"%PDF") and len(data) > 1500 and doc.total_boxes == 3


def test_email_sender_requires_supplier_address(tmp_path):
    sent = []
    s = EmailPoSender(lambda *a: sent.append(a), {"卸X": "order@x.jp"}, "po@clinic.jp")
    doc = PoDoc("PO1", "卸X", date(2026, 11, 2), "院", [PoLine("A", "", "", "R", 1)])
    s.send(doc, "x.pdf")
    assert sent[0][2] == "order@x.jp" and sent[0][3] == ["x.pdf"]
    with pytest.raises(LookupError):
        s.send(PoDoc("PO2", "未知", date(2026, 11, 2), "院", []), "y.pdf")


@pytest.mark.django_db
def test_generate_po_groups_by_supplier_and_marks_ordered(repo, shopify, settings):
    from datetime import datetime
    OrderVerification.objects.create(order_id="1001", order_name="#1001", status="ok", delivery="ship",
                                     shipping_address={"name": "山田 太郎", "zip": "500-0000", "city": "岐阜市",
                                                       "address1": "1-2-3"})
    repo.create_ec_dispense(rx_line_id="line-R", patient_id="P001", boxes=2, shopify_order_id="1001",
                            order_name="#1001", delivery="ship_clinic", ordered_at=datetime(2026, 11, 1))
    repo.create_ec_dispense(rx_line_id="line-L", patient_id="P001", boxes=1, shopify_order_id="1001",
                            order_name="#1001", delivery="ship_direct", ordered_at=datetime(2026, 11, 1))
    repo.add_clinic_dispense("line-R", 1)   # 院内交付は発注対象外
    pos = po_service.generate_purchase_orders()
    assert {p.supplier: (p.line_count, p.box_count) for p in pos} == {"卸X": (1, 2), "メーカーB直販": (1, 1)}
    assert all(p.sent_at is not None and p.file_path.endswith(".pdf") and open(p.file_path, "rb").read(4) == b"%PDF"
               for p in pos)
    assert {r.dispense.state for r in repo.dispenses_for_order("1001")} == {"ordered"}
    assert all(r.po_sent_at is not None for r in repo.dispenses_for_order("1001"))
    assert PurchaseOrder.objects.count() == 2
    assert sorted(p.po_number for p in pos)[0].endswith("-01")
    # 2 回目は対象なし
    assert po_service.generate_purchase_orders() == []


@pytest.mark.django_db
def test_generate_po_dry_run_keeps_state(repo, shopify):
    from datetime import datetime
    repo.create_ec_dispense(rx_line_id="line-R", patient_id="P001", boxes=2, shopify_order_id="1001",
                            order_name="#1001", delivery="pickup", ordered_at=datetime(2026, 11, 1))
    pos = po_service.generate_purchase_orders(dry_run=True)
    assert len(pos) == 1 and PurchaseOrder.objects.count() == 0
    assert {r.dispense.state for r in repo.dispenses_for_order("1001")} == {"verified"}


@pytest.mark.django_db
def test_generate_po_send_failure_keeps_verified(repo, shopify, monkeypatch):
    from datetime import datetime
    monkeypatch.setattr(po_service.notify, "staff", lambda s, b: None)
    repo.create_ec_dispense(rx_line_id="line-R", patient_id="P001", boxes=2, shopify_order_id="1001",
                            order_name="#1001", delivery="pickup", ordered_at=datetime(2026, 11, 1))
    sender = EmailPoSender(lambda *a: None, {}, "")   # 発注先メール未設定 → 失敗
    pos = po_service.generate_purchase_orders(sender=sender)
    assert pos[0].send_error and pos[0].sent_at is None
    assert {r.dispense.state for r in repo.dispenses_for_order("1001")} == {"verified"}
