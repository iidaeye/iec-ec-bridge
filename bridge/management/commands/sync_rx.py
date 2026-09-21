"""処方の同期（夜間ジョブ）。--patient で 1 人だけ。"""
from django.core.management.base import BaseCommand, CommandError

from bridge import services
from bridge.models import Member


class Command(BaseCommand):
    help = "会員の処方を FileMaker から取り、Shopify 顧客メタフィールドに書く"

    def add_arguments(self, parser):
        parser.add_argument("--patient", help="患者IDを指定して 1 人だけ同期")

    def handle(self, *args, **opts):
        if opts.get("patient"):
            m = Member.objects.filter(patient_id=opts["patient"]).first()
            if not m:
                raise CommandError("member not found")
            payload = services.sync_member(m)
            self.stdout.write(f"{m.patient_id}: {len(payload['lines'])} line(s)")
            return
        res = services.sync_all_members()
        self.stdout.write(f"ok={res['ok']} failed={res['failed']}")
