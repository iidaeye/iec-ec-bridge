# iec-ec-bridge

イイダ眼科医院 患者限定EC（Shopify）と FileMaker（Helm_cData.fmp12 の CL_ テーブル）をつなぐ連携サービスです。
Dokku 上の Django + PostgreSQL アプリとして動かします。

## 構成

| 場所 | 役割 |
| --- | --- |
| `iec_ec_bridge/core/` | 照合ロジック（Shopify にも FileMaker にも依存しない純粋関数） |
| `iec_ec_bridge/fm/` | FileMaker Data API クライアントと、FM レコード ⇄ 照合用データ型の変換（`RxRepository`） |
| `iec_ec_bridge/shopify/` | Shopify Admin API（GraphQL）クライアント。注文キャンセル＋返金、顧客メタフィールド、顧客作成 |
| `iec_ec_bridge/po/` | 発注書 PDF の生成と送信方法（FAX 用 PDF／メール） |
| `bridge/` | Django アプリ。Webhook 受信、`/api/*`、イベント処理、処方同期、発注書ジョブ、管理画面 |
| `config/` | Django 設定（すべて環境変数から） |
| `tests/` | 単体テスト・通しテスト（外部サービスは模擬） |
| `docs/` | Dokku への配置手順、検証手順 |

## エンドポイント

| パス | 呼び出し元 | 役割 |
| --- | --- | --- |
| `POST /webhooks/shopify/orders-create` | Shopify | HMAC を検証し受信履歴に保存。ワーカーが照合し、OK なら CL_Dispense を作成、NG なら注文をキャンセル・全額返金 |
| `POST /webhooks/shopify/orders-cancelled` | Shopify | 交付記録を cancelled にして数量上限を戻す |
| `POST /api/members/register` | 既存 LIFF | 患者ID・メール・LINE ID を受け、FileMaker で LINE ID の対応を確認し、Shopify 顧客を作成 |
| `POST /api/rx/refresh` | FileMaker スクリプト | 指定患者の処方を即時に再同期 |
| `GET /healthz` | 監視 | 稼働確認（DB 接続込み） |

`/api/*` は `X-Api-Key` ヘッダー（`API_SHARED_KEY`）と、必要なら送信元 IP（`API_ALLOWED_IPS`）で保護します。

## 処理の流れ（orders/create）

1. Webhook を受信し、署名を検証。`X-Shopify-Webhook-Id` を一意キーにして受信履歴（`WebhookEvent`）に保存し、すぐ 200 を返す（Shopify の 5 秒制限のため）
2. ワーカー（`manage.py run_worker`）が未処理イベントを取り出し、FileMaker から製品マスタ・処方・交付記録を取得して `core.verify_order()` で照合
3. OK → CL_Dispense に `state=verified, channel=ec` で作成（再試行時は作成済み明細を飛ばす）
4. NG → Shopify で注文をキャンセル・全額返金。理由コードは `OrderVerification` に残す。規格不一致などはスタッフにも通知
5. FileMaker / Shopify に接続できない → キャンセルせず保留。1・2・5・10 分…の間隔で再試行し、30 分続けばスタッフに通知
6. 同じ注文 ID に対する処理は 1 回だけ（受信履歴の一意制約＋照合結果の確認）

処方の同期（`manage.py sync_rx`）は会員ごとに有効な処方・残り購入可能箱数を計算し、Shopify 顧客メタフィールド `iec.rx_lines`（JSON）に書きます。
テーマの「あなたの処方レンズ」ページはこれを読み、カート投入時に `_rx_line_id` と規格を注文明細のプロパティとして付けます。

発注書（`manage.py generate_po`）は `state=verified` で未発注の交付記録を発注先ごとにまとめ、PDF を `PO_OUTPUT_DIR` に出力して交付記録を `ordered` にします。
当面は FAX 用 PDF（送信はスタッフ）。`PO_METHOD=email` にすると発注先のメールへ添付送信します。

## 開発とテスト

    pip install -r requirements-dev.txt
    python -m pytest -v            # 90 件。外部サービスには接続しない
    python manage.py migrate       # 既定は SQLite（DATABASE_URL 未設定時）
    python manage.py runserver
    python manage.py sample_po --out sample_po.pdf   # 発注書 PDF の見本

テスト名の先頭の数字（`test_04_...` など）は開発仕様タブのテストシナリオ番号です。

配置手順は `docs/deploy.md`、検証手順は `docs/verification.md` を参照してください。
