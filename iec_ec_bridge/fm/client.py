"""FileMaker Data API の薄いクライアント。

- セッショントークンを保持し、期限切れ（エラー 952）なら 1 回だけ再ログインして再試行する
- 「該当レコードなし」（エラー 401）は空リストとして扱う
- ネットワーク障害・5xx・ログイン失敗は FileMakerUnavailable にまとめ、呼び出し側が「保留して再試行」できるようにする
- HTTP セッションは差し替え可能（テストでは模擬セッションを渡す）
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Iterator, List, Optional

import requests

log = logging.getLogger(__name__)

NO_RECORDS = "401"
INVALID_TOKEN = "952"


class FileMakerError(Exception):
    def __init__(self, code: str, message: str = ""):
        super().__init__(f"FileMaker error {code}: {message}")
        self.code = str(code)
        self.message = message


class FileMakerUnavailable(FileMakerError):
    """接続できない・サーバー側障害。注文は保留にして後で再試行する。"""

    def __init__(self, message: str):
        super().__init__("unavailable", message)


class FileMakerClient:
    def __init__(self, host: str, database: str, user: str, password: str,
                 session=None, timeout: float = 20, verify_tls: bool = True,
                 api_version: str = "vLatest"):
        if not host or not database:
            raise ValueError("FileMaker host/database is not configured")
        self.base_url = f"https://{host}/fmi/data/{api_version}/databases/{database}"
        self._auth = (user, password)
        self._session = session or requests.Session()
        self._timeout = timeout
        self._verify = verify_tls
        self._token: Optional[str] = None

    # ---- セッション -----------------------------------------------------
    def login(self) -> str:
        try:
            resp = self._session.request(
                "POST", f"{self.base_url}/sessions", json={}, auth=self._auth,
                headers={"Content-Type": "application/json"},
                timeout=self._timeout, verify=self._verify)
        except requests.RequestException as e:  # 接続不可・タイムアウト
            raise FileMakerUnavailable(f"login failed: {e}") from e
        if resp.status_code >= 500:
            raise FileMakerUnavailable(f"login HTTP {resp.status_code}")
        body = _json(resp)
        code, msg = _message(body)
        if code != "0":
            # 認証情報の誤りも「利用不可」として保留にし、スタッフに知らせる
            raise FileMakerUnavailable(f"login rejected ({code}: {msg})")
        self._token = body["response"]["token"]
        return self._token

    def logout(self) -> None:
        if not self._token:
            return
        try:
            self._session.request("DELETE", f"{self.base_url}/sessions/{self._token}",
                                  timeout=self._timeout, verify=self._verify)
        except requests.RequestException:
            pass
        self._token = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.logout()

    # ---- 共通リクエスト ---------------------------------------------------
    def _request(self, method: str, path: str, json: Any = None, _retry: bool = True) -> Dict:
        if not self._token:
            self.login()
        try:
            resp = self._session.request(
                method, f"{self.base_url}{path}", json=json,
                headers={"Authorization": f"Bearer {self._token}",
                         "Content-Type": "application/json"},
                timeout=self._timeout, verify=self._verify)
        except requests.RequestException as e:
            raise FileMakerUnavailable(f"{method} {path}: {e}") from e
        if resp.status_code >= 500:
            raise FileMakerUnavailable(f"{method} {path}: HTTP {resp.status_code}")
        body = _json(resp)
        code, msg = _message(body)
        if code == "0":
            return body.get("response", {})
        if code == INVALID_TOKEN and _retry:
            log.info("FileMaker token expired; re-login")
            self._token = None
            return self._request(method, path, json, _retry=False)
        if code == NO_RECORDS:
            return {"data": []}
        raise FileMakerError(code, msg)

    # ---- レコード操作 -----------------------------------------------------
    def find(self, layout: str, query: List[Dict[str, str]], limit: int = 200,
             offset: int = 1, sort: Optional[List[Dict[str, str]]] = None) -> List[Dict]:
        payload: Dict[str, Any] = {"query": query, "limit": str(limit), "offset": str(offset)}
        if sort:
            payload["sort"] = sort
        return self._request("POST", f"/layouts/{layout}/_find", payload).get("data", [])

    def find_all(self, layout: str, query: List[Dict[str, str]], page: int = 200,
                 sort: Optional[List[Dict[str, str]]] = None) -> Iterator[Dict]:
        offset = 1
        while True:
            batch = self.find(layout, query, limit=page, offset=offset, sort=sort)
            yield from batch
            if len(batch) < page:
                return
            offset += page

    def get_records(self, layout: str, limit: int = 200, offset: int = 1) -> List[Dict]:
        return self._request("GET", f"/layouts/{layout}/records?_limit={limit}&_offset={offset}"
                             ).get("data", [])

    def get_all_records(self, layout: str, page: int = 200) -> Iterator[Dict]:
        offset = 1
        while True:
            batch = self.get_records(layout, limit=page, offset=offset)
            yield from batch
            if len(batch) < page:
                return
            offset += page

    def create(self, layout: str, field_data: Dict[str, Any]) -> str:
        resp = self._request("POST", f"/layouts/{layout}/records", {"fieldData": field_data})
        return str(resp["recordId"])

    def edit(self, layout: str, record_id: str, field_data: Dict[str, Any]) -> None:
        self._request("PATCH", f"/layouts/{layout}/records/{record_id}", {"fieldData": field_data})

    def get_record(self, layout: str, record_id: str) -> Optional[Dict]:
        data = self._request("GET", f"/layouts/{layout}/records/{record_id}").get("data", [])
        return data[0] if data else None

    def run_script(self, layout: str, script: str, param: str = "") -> Dict:
        return self._request("GET", f"/layouts/{layout}/script/{script}?script.param={param}")


def _json(resp) -> Dict:
    try:
        return resp.json()
    except ValueError as e:
        raise FileMakerUnavailable(f"non-JSON response (HTTP {resp.status_code})") from e


def _message(body: Dict):
    msgs = body.get("messages") or [{}]
    return str(msgs[0].get("code", "")), msgs[0].get("message", "")
