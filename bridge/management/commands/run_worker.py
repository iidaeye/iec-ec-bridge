"""Webhook イベントを処理するワーカー（Procfile の worker）。保留イベントの再試行も担う。"""
import logging
import signal
import time

from django.conf import settings
from django.core.management.base import BaseCommand

from bridge import services

log = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Webhook イベントを順に処理する。--once で 1 回だけ実行"

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")

    def handle(self, *args, **opts):
        stop = {"flag": False}

        def _stop(*_):
            stop["flag"] = True
        signal.signal(signal.SIGTERM, _stop)
        signal.signal(signal.SIGINT, _stop)
        while True:
            n = services.process_pending_events()
            if n:
                log.info("processed %d event(s)", n)
            if opts["once"] or stop["flag"]:
                self.stdout.write(f"processed {n}")
                return
            time.sleep(settings.WORKER_POLL_SECONDS if n == 0 else 0)
