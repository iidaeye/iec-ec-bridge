"""発注書 PDF（FAX 送信用）。Django にも FileMaker にも依存しない。

日本語は reportlab 内蔵の CID フォント（HeiseiKakuGo-W5）で埋め込みなしに出す。
見た目を揃えたい場合は TTF のパスを font_path に渡す。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Dict, List, Optional

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

CID_FONT = "HeiseiKakuGo-W5"


@dataclass(frozen=True)
class PoLine:
    product_code: str
    maker: str
    product_name: str
    eye: str
    boxes: int
    bc: str = ""
    dia: str = ""
    pwr: str = ""
    cyl: str = ""
    ax: str = ""
    add_power: str = ""
    color: str = ""
    delivery: str = "pickup"            # pickup / ship_direct / ship_clinic
    order_name: str = ""
    ship_to: Optional[Dict[str, str]] = None   # ship_direct のときの送付先


@dataclass
class PoDoc:
    po_number: str
    supplier: str
    issued_on: date
    clinic_name: str
    lines: List[PoLine]
    clinic_address: str = ""
    clinic_tel: str = ""
    clinic_fax: str = ""
    note: str = ""
    extra: Dict[str, str] = field(default_factory=dict)

    @property
    def total_boxes(self) -> int:
        return sum(l.boxes for l in self.lines)


def _font(font_path: str = "") -> str:
    if font_path:
        name = "PoFont"
        if name not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont(name, font_path))
        return name
    if CID_FONT not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(UnicodeCIDFont(CID_FONT))
    return CID_FONT


DELIVERY_LABEL = {"pickup": "当院", "ship_clinic": "当院", "ship_direct": "患者宅直送"}


def _fmt_pwr(v: str) -> str:
    """度数は符号付き（+ を明示）。"""
    if v in ("", None):
        return ""
    s = str(v)
    return s if s.startswith(("+", "-")) else ("0.00" if float(s) == 0 else f"+{s}")


def render_po_pdf(doc: PoDoc, path: str, font_path: str = "") -> str:
    font = _font(font_path)
    base = ParagraphStyle("base", fontName=font, fontSize=10, leading=14)
    small = ParagraphStyle("small", parent=base, fontSize=8.5, leading=11)
    title = ParagraphStyle("title", parent=base, fontSize=18, leading=24, alignment=1)
    head = ParagraphStyle("head", parent=base, fontSize=11, leading=15)

    pdf = SimpleDocTemplate(path, pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm,
                            topMargin=15 * mm, bottomMargin=15 * mm,
                            title=f"発注書 {doc.po_number}", author=doc.clinic_name)
    story = [Paragraph("発 注 書", title), Spacer(1, 4 * mm)]

    meta = Table([
        [Paragraph(f"<b>{_esc(doc.supplier)}</b> 御中", head),
         Paragraph(f"発注番号: {_esc(doc.po_number)}<br/>発注日: {doc.issued_on.strftime('%Y年%m月%d日')}", base)],
        [Paragraph("下記のとおり発注いたします。", base),
         Paragraph("<br/>".join(_esc(x) for x in (doc.clinic_name, doc.clinic_address,
                                                 f"TEL {doc.clinic_tel}" if doc.clinic_tel else "",
                                                 f"FAX {doc.clinic_fax}" if doc.clinic_fax else "") if x), base)],
    ], colWidths=[95 * mm, 85 * mm])
    meta.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story += [meta, Spacer(1, 5 * mm)]

    header = ["No", "製品コード", "メーカー / 製品名", "眼", "BC", "DIA", "PWR", "CYL", "AX", "ADD", "色", "箱数", "納品先", "注文No"]
    rows = [header]
    for i, l in enumerate(doc.lines, 1):
        rows.append([
            str(i), l.product_code, Paragraph(_esc(f"{l.maker} {l.product_name}".strip()), small),
            l.eye, l.bc, l.dia, _fmt_pwr(l.pwr), _fmt_pwr(l.cyl) if l.cyl else "", l.ax,
            l.add_power, l.color, str(l.boxes), DELIVERY_LABEL.get(l.delivery, l.delivery), l.order_name])
    rows.append(["", "", "合計", "", "", "", "", "", "", "", "", str(doc.total_boxes), "", ""])
    widths = [7, 18, 38, 7, 9, 10, 12, 11, 8, 10, 10, 8, 16, 16]   # 合計 180mm
    table = Table(rows, colWidths=[w * mm for w in widths], repeatRows=1)
    table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), font), ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.black),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8e8e8")),
        ("ALIGN", (3, 1), (-1, -1), "CENTER"), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("SPAN", (0, -1), (2, -1)), ("ALIGN", (0, -1), (2, -1), "RIGHT"),
    ]))
    story += [table, Spacer(1, 5 * mm)]

    direct = [l for l in doc.lines if l.delivery == "ship_direct" and l.ship_to]
    if direct:
        story.append(Paragraph("患者宅への直送先", head))
        drows = [["注文No", "製品コード", "箱数", "送付先"]]
        for l in direct:
            a = l.ship_to or {}
            addr = " ".join(x for x in (a.get("zip", ""), a.get("province", ""), a.get("city", ""),
                                        a.get("address1", ""), a.get("address2", "")) if x)
            who = " ".join(x for x in (a.get("name", ""), f"TEL {a.get('phone')}" if a.get("phone") else "") if x)
            drows.append([l.order_name, l.product_code, str(l.boxes),
                          Paragraph(f"{_esc(who)}<br/>{_esc(addr)}", small)])
        dt = Table(drows, colWidths=[22 * mm, 25 * mm, 12 * mm, 120 * mm], repeatRows=1)
        dt.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (-1, -1), font), ("FONTSIZE", (0, 0), (-1, -1), 8.5),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.black),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8e8e8")),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
        story += [dt, Spacer(1, 4 * mm)]

    if doc.note:
        story.append(Paragraph(_esc(doc.note).replace("\n", "<br/>"), base))
    pdf.build(story)
    return path


def _esc(s: str) -> str:
    return (str(s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
