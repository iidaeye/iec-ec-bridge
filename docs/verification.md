# 検証手順

## A. 自動テスト（外部サービスなし）

    pip install -r requirements-dev.txt
    python -m pytest -v

| テスト | 開発仕様のシナリオ |
| --- | --- |
| `tests/test_core.py` | 照合ルール・数量上限・HMAC（前回分 27 件） |
| `tests/test_webhooks.py` | #1 両眼注文で交付記録 2 件、#4/#5 期限の翌日と当日、#6 規格改ざん、#7 上限＋1、#8 院内交付の差し引き、#11 キャンセルで上限が戻る、#13 署名不正の拒否と再送の 1 回処理、#14 FileMaker 停止中の保留と復旧後の自動照合、#15 アイケア用品 |
| `tests/test_sync.py` | #3 片眼のみ、#10 ec_allowed=0 で表示されない、期限切れは表示されない |
| `tests/test_api.py` | 会員登録（LINE ID 対応の確認、既存顧客の再利用）、共有キーと IP 制限、healthz |
| `tests/test_fm_client.py` | Data API のログイン・トークン失効時の再ログイン・レコードなし・接続不可、日付書式、FM レコードから処方への変換 |
| `tests/test_shopify_client.py` | orderCancel の変数、userErrors、429/5xx の再試行、メタフィールド、顧客検索・作成 |
| `tests/test_po.py` | 発注書 PDF、発注先ごとの分割、ordered への更新、dry-run、送信失敗時は verified のまま |

## B. ローカルで起動して Webhook を送る

    export DJANGO_SECRET_KEY=dev SHOPIFY_WEBHOOK_SECRET=devsecret API_SHARED_KEY=devkey
    export FM_HOST=<FileMaker Server> FM_USER=ec_bridge FM_PASSWORD=<pw>
    export SHOPIFY_SHOP_DOMAIN=iida-eye-shop-dev.myshopify.com SHOPIFY_ADMIN_TOKEN=<token>
    python manage.py migrate
    python manage.py runserver                      # 別ターミナルで
    python manage.py run_worker                     # 別ターミナルで

1. 会員を登録する（LINE ID の確認を省くなら `line_user_id` を省略）

       curl -X POST localhost:8000/api/members/register -H "X-Api-Key: devkey" \
            -H "Content-Type: application/json" \
            -d '{"patient_id":"P001","email":"test@example.com"}'

   → Shopify 管理画面で顧客が作られ、メタフィールド `iec.rx_lines` に処方が入る
2. 署名付きのテスト注文を送る（`--customer` は上で返った `shopify_customer_id`）

       python scripts/send_test_webhook.py http://localhost:8000 --customer <id> \
            --sku 1D-SPH --qty 2 --rx-line <rx_line_id> --spec '{"bc":"8.5","dia":"14.2","pwr":"-3.25"}'

   → ワーカーのログに照合結果、FileMaker の CL_Dispense に `verified` の記録
3. 度数を変えて送る → Shopify で注文キャンセル（開発ストアではテスト注文を先に作り、その注文 ID を `--order-id` に指定）
4. キャンセルを送る → 記録が `cancelled` になる

       python scripts/send_test_webhook.py http://localhost:8000 --cancel <order id>

5. 発注書

       python manage.py generate_po --dry-run     # PDF だけ作る
       python manage.py generate_po               # 交付記録を ordered にする
       python manage.py sample_po                  # 見本 PDF（接続なし）

## C. 開発ストアでの通し確認

1. Dokku に配置し、Shopify の Webhook を登録（`docs/deploy.md`）
2. 開発ストアで Bogus Gateway（テスト決済）を有効にし、会員登録した顧客でログインして注文
3. 管理画面 `/admin/` の「Webhook events」「Order verifications」で受信と照合結果を確認
4. FileMaker で CL_Dispense を確認し、「CL_入荷済みにする」で `arrived` にする

## 確認結果の見方

- `OrderVerification.status`: `ok`（交付記録作成）/ `rejected`（自動キャンセル。`reasons` に理由コード）/ `pending`（保留・再試行中）/ `cancelled` / `error`
- `WebhookEvent.status`: `received` → `processing` → `done`。`pending_retry` は外部サービス待ち、`skipped` は重複、`failed` は想定外エラー（スタッフ通知済み）
