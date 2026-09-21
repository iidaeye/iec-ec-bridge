"""照合 OK（state=verified, channel=ec, 未発注）の交付記録から発注先別に発注書を作る。"""
from __future__ import annotations

import logging
import os
from collections import defaultdict
from typing import Dict, List

from django.conf import settings
from django.core.mail import EmailMessage
from django.utils import timezone

from iec_ec_bridge.po import EmailPoSender, FaxPdfSender, PoDoc, PoLine, PoSender, render_po_pdf

from . import gateways, notify
from .models import OrderVerification, PurchaseOrder

log = logging.getLogger(__name__)


def _django_send(subject, body, to, attachments, from_email):
    msg = EmailMessage(subject, body, from_email or settings.DEFAULT_FROM_EMAIL, [to])
    for path in attachments:
        msg.attach_file(path)
    msg.send(fail_silently=False)


def get_sender() -> PoSender:
    if settings.PO_METHOD == "email":
        return EmailPoSender(_django_send, settings.PO_SUPPLIER_EMAILS, settings.PO_EMAIL_FROM)
    return FaxPdfSender()


def next_po_number(today) -> str:
    prefix = "PO" + today.strftime("%Y%m%d") + "-"
    n = PurchaseOrder.objects.filter(po_number__startswith=prefix).count() + 1
    while PurchaseOrder.objects.filter(po_number=f"{prefix}{n:02d}").exists():
        n += 1
    return f"{prefix}{n:02d}"


def _s(v) -> str:
    if v is None:
        return ""
    return str(int(v)) if hasattr(v, "to_integral") and v == v.to_integral() else str(v.normalize() if hasattr(v, "normalize") else v)


def generate_purchase_orders(dry_run: bool = False, sender: PoSender = None) -> List[PurchaseOrder]:
    repo = gateways.get_repo()
    sender = sender or get_sender()
    records = repo.verified_ec_dispenses_awaiting_po()
    if not records:
        return []
    products = repo.products(active_only=False)
    rx_by_line = repo.rx_by_line_ids([r.dispense.rx_line_id for r in records])
    order_ids = {r.dispense.shopify_order_id for r in records if r.dispense.shopify_order_id}
    ship_to = {v.order_id: v.shipping_address
               for v in OrderVerification.objects.filter(order_id__in=order_ids)}

    groups: Dict[str, list] = defaultdict(list)
    for rec in records:
        rx = rx_by_line.get(rec.dispense.rx_line_id)
        line = next((l for l in rx.lines if l.rx_line_id == rec.dispense.rx_line_id), None) if rx else None
        if line is None:
            notify.staff(f"交付記録 {rec.record_id} の処方明細が見つかりません", f"注文 {rec.order_name}")
            continue
        info = products.get(line.product_code)
        supplier = info.supplier if info else "(発注先未設定)"
        groups[supplier].append(PoLine(
            product_code=line.product_code, maker=info.maker if info else "",
            product_name=info.product_name if info else "", eye=line.eye,
            boxes=rec.dispense.boxes, bc=_s(line.bc), dia=_s(line.dia), pwr=_s(line.pwr),
            cyl=_s(line.cyl), ax=_s(line.ax), add_power=line.add_power or "", color=line.color or "",
            delivery=rec.delivery or "pickup", order_name=rec.order_name,
            ship_to=ship_to.get(rec.dispense.shopify_order_id) if rec.delivery == "ship_direct" else None,
        ))
        groups[supplier + "\x00ids"].append(rec.record_id)

    today = timezone.localdate()
    now = timezone.localtime().replace(tzinfo=None)
    os.makedirs(settings.PO_OUTPUT_DIR, exist_ok=True)
    result = []
    for supplier, lines in groups.items():
        if supplier.endswith("\x00ids"):
            continue
        record_ids = groups[supplier + "\x00ids"]
        lines.sort(key=lambda l: (l.product_code, l.order_name, l.eye))
        po_number = next_po_number(today)
        doc = PoDoc(po_number=po_number, supplier=supplier, issued_on=today,
                    clinic_name=settings.CLINIC_NAME, clinic_address=settings.CLINIC_ADDRESS,
                    clinic_tel=settings.CLINIC_TEL, clinic_fax=settings.CLINIC_FAX, lines=lines,
                    note="患者宅直送分は別記の送付先へお願いします。" if any(l.delivery == "ship_direct" for l in lines) else "")
        path = os.path.join(settings.PO_OUTPUT_DIR, f"{po_number}.pdf")
        render_po_pdf(doc, path, settings.PO_FONT_PATH)
        po = PurchaseOrder(po_number=po_number, supplier=supplier, method=sender.method,
                           file_path=path, dispense_record_ids=record_ids,
                           order_names=sorted({l.order_name for l in lines}),
                           line_count=len(lines), box_count=doc.total_boxes)
        if dry_run:
            po.send_error = "dry-run"
            result.append(po)
            continue
        po.save()
        try:
            sender.send(doc, path)
            po.sent_at = timezone.now()
        except Exception as e:
            log.exception("PO %s send failed", po_number)
            po.send_error = f"{type(e).__name__}: {e}"
            notify.staff(f"発注書 {po_number} の送信に失敗", f"{e}\nPDF: {path}")
        po.save()
        if not po.send_error:
            for rid in record_ids:
                repo.set_dispense_state(rid, "ordered", po_sent_at=now)
        result.append(po)
    return result
