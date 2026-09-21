# iec-ec-bridge（照合コア）

イイダ眼科医院 患者限定EC の連携サービスのうち、Shopify にも FileMaker にも依存しない中核部分です。

- `iec_ec_bridge/core/models.py` … データ型（処方・交付記録・注文）
- `iec_ec_bridge/core/eligibility.py` … 注文と処方の照合 `verify_order()`。NG 理由はコードで返す
- `iec_ec_bridge/core/quantity.py` … 購入数量の上限（期間日数 ÷ 1箱日数 の切り上げ ＋ 予備箱 − 交付済み）
- `iec_ec_bridge/core/shopify_hmac.py` … Webhook 署名検証
- `tests/test_core.py` … 開発仕様のテストシナリオ番号に対応した単体テスト

## 検証方法

    pip install pytest
    python -m pytest -v

テスト名の先頭の数字（`test_04_...` など）が、開発仕様タブのシナリオ番号です。

## 未実装（次の段階）

Django プロジェクト本体、Webhook 受信ビュー、FileMaker Data API クライアント、Shopify Admin API クライアント、
発注書（PDF）生成、定期ジョブ、Dokku 用の Procfile。
