# Dokku への配置手順

## 1. アプリと PostgreSQL

    dokku apps:create iec-ec-bridge
    dokku postgres:create iec-ec-bridge-db
    dokku postgres:link iec-ec-bridge-db iec-ec-bridge      # DATABASE_URL が設定される
    dokku domains:set iec-ec-bridge bridge.iida-eye.jp
    dokku letsencrypt:enable iec-ec-bridge

## 2. 環境変数

`.env.example` の項目を `dokku config:set iec-ec-bridge KEY=VALUE ...` で設定します。値はチャットやリポジトリに置きません。

最低限必要なもの:

| 変数 | 内容 |
| --- | --- |
| `DJANGO_SECRET_KEY` | ランダムな長い文字列（`python -c "import secrets;print(secrets.token_urlsafe(50))"`） |
| `DJANGO_ALLOWED_HOSTS` | `bridge.iida-eye.jp` |
| `SHOPIFY_SHOP_DOMAIN` | `iida-eye-shop-dev.myshopify.com`（本番は本番ストアの myshopify ドメイン） |
| `SHOPIFY_ADMIN_TOKEN` | カスタムアプリの Admin API アクセストークン（下記スコープ） |
| `SHOPIFY_WEBHOOK_SECRET` | Webhook 署名キー |
| `FM_HOST` / `FM_DATABASE` / `FM_USER` / `FM_PASSWORD` | FileMaker Server と `ec_bridge` アカウント |
| `API_SHARED_KEY` | LIFF・FileMaker スクリプトが `/api/*` を呼ぶときの共有キー |

Shopify カスタムアプリに必要な Admin API スコープ: `read_orders`, `write_orders`（キャンセル・返金）, `read_customers`, `write_customers`（顧客作成・メタフィールド）, `read_products`。

## 3. デプロイとプロセス

    git remote add dokku dokku@<host>:iec-ec-bridge
    git push dokku main
    dokku ps:scale iec-ec-bridge web=1 worker=1
    dokku run iec-ec-bridge python manage.py createsuperuser   # 管理画面（/admin/）用

`Procfile` の `release` でマイグレーションが自動実行されます。`app.json` の `cron` により、処方同期が毎日 2:30、発注書生成が月〜土 9:00 に走ります（Dokku 0.23 以降の cron 機能）。

発注書 PDF は `PO_OUTPUT_DIR`（既定 `/app/var/po`）に出ます。コンテナ再作成で消えないよう永続ストレージを割り当てます:

    dokku storage:ensure-directory iec-ec-bridge-po
    dokku storage:mount iec-ec-bridge /var/lib/dokku/data/storage/iec-ec-bridge-po:/app/var/po

## 4. Shopify 側の設定

1. 管理画面 → 設定 → 通知 → Webhook で次の 2 つを作成（形式 JSON、最新の API バージョン）
   - 注文作成 → `https://bridge.iida-eye.jp/webhooks/shopify/orders-create`
   - 注文キャンセル → `https://bridge.iida-eye.jp/webhooks/shopify/orders-cancelled`
2. 同じ画面に表示される署名キーを `SHOPIFY_WEBHOOK_SECRET` に設定
3. 処方が要る商品（コンタクトレンズ）には商品タグ `rx-required` を付ける。CL_Product に SKU が未登録のまま注文されたとき `unknown_product` で止めるための保険
4. 顧客メタフィールド定義（設定 → カスタムデータ → 顧客）: 名前空間 `iec`、キー `rx_lines`（JSON）、`patient_id`（1 行テキスト）。ストアフロントからの読み取りを許可

## 5. FileMaker 側

FileMaker 定義書のとおり `api_CL_Product` / `api_CL_Rx`（ポータル `lines`）/ `api_CL_RxLine` / `api_CL_Dispense` / `api_LineLink` レイアウトと `ec_bridge` アカウントを用意します。
LINE ID 対応テーブルのレイアウト名・フィールド名が違う場合は `FM_LAYOUT_LINELINK` / `FM_LINELINK_LINE_FIELD` / `FM_LINELINK_PATIENT_FIELD` で合わせます。
Data API が返す日付の書式が `MM/DD/YYYY` でない場合は `FM_DATE_FORMAT` を変えます（例 `%Y/%m/%d`）。

「CL_処方を確定」スクリプトからは次を呼びます（URL から挿入、cURL オプションに `-H "X-Api-Key: <API_SHARED_KEY>"`）:

    POST https://bridge.iida-eye.jp/api/rx/refresh
    {"patient_id": "<患者ID>"}
