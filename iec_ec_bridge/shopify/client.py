"""Shopify Admin API（GraphQL）クライアント。使う操作だけを関数にしている。

- 注文の自動キャンセル＋全額返金（orderCancel）
- 顧客メタフィールドの保存（metafieldsSet）: 「あなたの処方レンズ」ページが読む
- 顧客の作成・検索（customerCreate / customers）
- 商品のタグ取得（処方が要る商品かの判定）
HTTP セッションは差し替え可能で、テストでは模擬セッションを渡す。
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, Iterable, List, Optional

import requests

log = logging.getLogger(__name__)


class ShopifyError(Exception):
    pass


class ShopifyUnavailable(ShopifyError):
    """接続不可・5xx・レート制限超過。保留して再試行する。"""


class ShopifyAdminClient:
    def __init__(self, shop_domain: str, access_token: str, api_version: str = "2025-07",
                 session=None, timeout: float = 20, sleep=time.sleep, max_retries: int = 3):
        if not shop_domain or not access_token:
            raise ValueError("Shopify shop domain / admin token is not configured")
        self.url = f"https://{shop_domain}/admin/api/{api_version}/graphql.json"
        self._headers = {"X-Shopify-Access-Token": access_token,
                         "Content-Type": "application/json"}
        self._session = session or requests.Session()
        self._timeout = timeout
        self._sleep = sleep
        self._max_retries = max_retries

    # ---- 共通 -------------------------------------------------------------
    def graphql(self, query: str, variables: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        payload = {"query": query, "variables": variables or {}}
        last_err = None
        for attempt in range(self._max_retries):
            try:
                resp = self._session.post(self.url, json=payload, headers=self._headers,
                                          timeout=self._timeout)
            except requests.RequestException as e:
                last_err = ShopifyUnavailable(str(e))
                self._sleep(2 ** attempt)
                continue
            if resp.status_code == 429 or resp.status_code >= 500:
                last_err = ShopifyUnavailable(f"HTTP {resp.status_code}")
                self._sleep(2 ** attempt)
                continue
            if resp.status_code in (401, 403):
                raise ShopifyError(f"HTTP {resp.status_code}: Admin API token rejected")
            try:
                body = resp.json()
            except ValueError as e:
                raise ShopifyUnavailable(f"non-JSON response HTTP {resp.status_code}") from e
            errors = body.get("errors")
            if errors:
                if any("THROTTLED" in json.dumps(e) for e in errors):
                    last_err = ShopifyUnavailable("throttled")
                    self._sleep(2 ** attempt)
                    continue
                raise ShopifyError(json.dumps(errors, ensure_ascii=False))
            return body.get("data") or {}
        raise last_err or ShopifyUnavailable("unknown")

    @staticmethod
    def gid(kind: str, numeric_id) -> str:
        s = str(numeric_id)
        return s if s.startswith("gid://") else f"gid://shopify/{kind}/{s}"

    @staticmethod
    def numeric_id(gid: str) -> str:
        return str(gid).rsplit("/", 1)[-1]

    # ---- 注文 ---------------------------------------------------------------
    def cancel_order(self, order_id, *, staff_note: str, reason: str = "OTHER",
                     refund: bool = True, restock: bool = False, notify_customer: bool = True) -> str:
        q = """
        mutation CancelOrder($orderId: ID!, $reason: OrderCancelReason!, $refund: Boolean!,
                             $restock: Boolean!, $notify: Boolean, $note: String) {
          orderCancel(orderId: $orderId, reason: $reason, refund: $refund, restock: $restock,
                      notifyCustomer: $notify, staffNote: $note) {
            job { id done }
            orderCancelUserErrors { field message code }
          }
        }"""
        data = self.graphql(q, {"orderId": self.gid("Order", order_id), "reason": reason,
                                "refund": refund, "restock": restock,
                                "notify": notify_customer, "note": staff_note[:1000]})
        res = data.get("orderCancel") or {}
        _raise_user_errors(res.get("orderCancelUserErrors"))
        return (res.get("job") or {}).get("id", "")

    # ---- 商品 ---------------------------------------------------------------
    def product_tags(self, product_ids: Iterable) -> Dict[str, List[str]]:
        """numeric product id -> tags"""
        ids = sorted({str(i) for i in product_ids if i})
        if not ids:
            return {}
        q = """
        query ProductTags($ids: [ID!]!) {
          nodes(ids: $ids) { ... on Product { id tags productType } }
        }"""
        data = self.graphql(q, {"ids": [self.gid("Product", i) for i in ids]})
        out: Dict[str, List[str]] = {}
        for node in data.get("nodes") or []:
            if node:
                out[self.numeric_id(node["id"])] = list(node.get("tags") or [])
        return out

    def variant_by_sku(self, sku: str) -> Optional[Dict[str, Any]]:
        q = """
        query VariantBySku($q: String!) {
          productVariants(first: 1, query: $q) { nodes { id sku product { id title } } }
        }"""
        nodes = (self.graphql(q, {"q": f"sku:{sku}"}).get("productVariants") or {}).get("nodes") or []
        return nodes[0] if nodes else None

    # ---- 顧客 ---------------------------------------------------------------
    def find_customer_by_email(self, email: str) -> Optional[str]:
        q = """
        query CustomerByEmail($q: String!) {
          customers(first: 1, query: $q) { nodes { id email } }
        }"""
        nodes = (self.graphql(q, {"q": f"email:{email}"}).get("customers") or {}).get("nodes") or []
        for n in nodes:
            if (n.get("email") or "").lower() == email.lower():
                return n["id"]
        return None

    def create_customer(self, email: str, *, first_name: str = "", last_name: str = "",
                        tags: Iterable[str] = (), metafields: Iterable[Dict[str, str]] = ()) -> str:
        q = """
        mutation CreateCustomer($input: CustomerInput!) {
          customerCreate(input: $input) { customer { id } userErrors { field message } }
        }"""
        inp: Dict[str, Any] = {"email": email, "tags": list(tags)}
        if first_name:
            inp["firstName"] = first_name
        if last_name:
            inp["lastName"] = last_name
        if metafields:
            inp["metafields"] = list(metafields)
        res = self.graphql(q, {"input": inp}).get("customerCreate") or {}
        _raise_user_errors(res.get("userErrors"))
        return res["customer"]["id"]

    def set_customer_metafields(self, customer_id, metafields: Iterable[Dict[str, str]]) -> None:
        """metafields: [{namespace, key, type, value}]"""
        q = """
        mutation SetMetafields($metafields: [MetafieldsSetInput!]!) {
          metafieldsSet(metafields: $metafields) {
            metafields { id key }
            userErrors { field message code }
          }
        }"""
        owner = self.gid("Customer", customer_id)
        payload = [dict(m, ownerId=owner) for m in metafields]
        res = self.graphql(q, {"metafields": payload}).get("metafieldsSet") or {}
        _raise_user_errors(res.get("userErrors"))

    def add_customer_tags(self, customer_id, tags: Iterable[str]) -> None:
        q = """
        mutation AddTags($id: ID!, $tags: [String!]!) {
          tagsAdd(id: $id, tags: $tags) { userErrors { field message } }
        }"""
        res = self.graphql(q, {"id": self.gid("Customer", customer_id), "tags": list(tags)})
        _raise_user_errors((res.get("tagsAdd") or {}).get("userErrors"))


def _raise_user_errors(errors) -> None:
    if errors:
        raise ShopifyError("; ".join(f"{e.get('field')}: {e.get('message')}" for e in errors))
