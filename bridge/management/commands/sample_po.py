"""発注書 PDF の見本を作る（FileMaker / Shopify に接続しない）。レイアウト確認用。"""
from datetime import date

from django.conf import settings
from django.core.management.base import BaseCommand

from iec_ec_bridge.po import PoDoc, PoLine, render_po_pdf


class Command(BaseCommand):
    help = "見本データで発注書 PDF を出力する"

    def add_arguments(self, parser):
        parser.add_argument("--out", default="sample_po.pdf")

    def handle(self, *args, **opts):
        lines = [
            PoLine("1D-SPH", "メーカーA", "ワンデー アキュビュー相当 30枚", "R", 2, "8.5", "14.2", "-3.25",
                   order_name="#1001"),
            PoLine("1D-SPH", "メーカーA", "ワンデー アキュビュー相当 30枚", "L", 2, "8.5", "14.2", "-2.75",
                   order_name="#1001"),
            PoLine("2W-TOR", "メーカーB", "2ウィーク トーリック 6枚", "R", 1, "8.6", "14.5", "-2.00", "-0.75", "180",
                   delivery="ship_direct", order_name="#1002",
                   ship_to={"name": "山田 太郎", "zip": "500-0000", "province": "岐阜県", "city": "岐阜市",
                            "address1": "テスト町1-2-3", "phone": "090-0000-0000"}),
            PoLine("1M-MF", "メーカーC", "マンスリー 遠近両用 3枚", "L", 1, "8.6", "14.2", "+1.50", add_power="High",
                   order_name="#1003"),
        ]
        doc = PoDoc("PO20261102-01", "卸X（見本）", date(2026, 11, 2), settings.CLINIC_NAME, lines,
                    clinic_address=settings.CLINIC_ADDRESS or "岐阜県○○市○○ 1-2-3",
                    clinic_tel=settings.CLINIC_TEL or "058-000-0000",
                    clinic_fax=settings.CLINIC_FAX or "058-000-0001",
                    note="患者宅直送分は別記の送付先へお願いします。\n※ これは見本です。")
        path = render_po_pdf(doc, opts["out"], settings.PO_FONT_PATH)
        self.stdout.write(f"wrote {path}")
