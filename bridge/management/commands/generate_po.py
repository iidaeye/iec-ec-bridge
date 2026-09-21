"""照合 OK で未発注の交付記録から、発注先ごとの発注書 PDF を作る（1 日 1 回のジョブ）。"""
from django.core.management.base import BaseCommand, CommandError

from bridge import po_service


class Command(BaseCommand):
    help = "発注書 PDF を生成し、交付記録を ordered にする"

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="PDF は作るが FileMaker は更新しない")

    def handle(self, *args, **opts):
        try:
            pos = po_service.generate_purchase_orders(dry_run=opts["dry_run"])
        except ValueError as e:   # FM_HOST などの設定漏れ
            raise CommandError(str(e))
        if not pos:
            self.stdout.write("no orders awaiting purchase")
        for po in pos:
            self.stdout.write(f"{po.po_number} {po.supplier}: {po.line_count} line(s), {po.box_count} box(es) -> {po.file_path}")
