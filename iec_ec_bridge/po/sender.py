"""発注書の送り方。当面は FAX 用 PDF（ファイルを作るだけ。送信はスタッフが行う）。
メールや Web 発注へ移行するときは PoSender を実装して po_service で切り替える。"""
from __future__ import annotations

from typing import Callable, Mapping, Optional

from .pdf import PoDoc


class PoSender:
    method = "base"

    def send(self, doc: PoDoc, pdf_path: str) -> Optional[str]:
        """送信する。戻り値は送信の記録（メッセージ ID 等）。失敗は例外。"""
        raise NotImplementedError


class FaxPdfSender(PoSender):
    """PDF を出力フォルダに置く。FAX 送信は人が行う。"""
    method = "fax_pdf"

    def send(self, doc: PoDoc, pdf_path: str) -> Optional[str]:
        return pdf_path


class EmailPoSender(PoSender):
    """発注先のメールアドレスへ PDF を添付して送る。send_fn(subject, body, to, attachments) を注入。"""
    method = "email"

    def __init__(self, send_fn: Callable, supplier_emails: Mapping[str, str], from_email: str = ""):
        self._send = send_fn
        self._emails = dict(supplier_emails)
        self._from = from_email

    def send(self, doc: PoDoc, pdf_path: str) -> Optional[str]:
        to = self._emails.get(doc.supplier)
        if not to:
            raise LookupError(f"発注先 {doc.supplier} のメールアドレスが未設定です")
        subject = f"【発注書 {doc.po_number}】{doc.clinic_name}"
        body = (f"{doc.supplier} 御中\n\n添付の発注書のとおり発注いたします。\n"
                f"発注番号: {doc.po_number}\n合計 {doc.total_boxes} 箱\n\n{doc.clinic_name}")
        self._send(subject, body, to, [pdf_path], self._from)
        return to
