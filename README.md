# bid-rss-mailer

国・準公的機関の入札/公募RSSを収集し、キーワードセットでルールベース判定して、毎朝JST 7:00にメール配信するMVPです。

## MVP仕様
- RSS/Atom/RDF混在のフィードを収集
- `data/keyword_sets.yaml` の3セットで判定
  - 必須語2語以上で候補
  - 加点語一致でスコア加点
  - 除外語一致で原則除外（例外語設定対応）
- SQLiteで履歴管理し、同一URL(正規化キー)を再送しない
- メールは1通に集約してセット別に上位10件を送信
- 失敗/異常時は管理者へ通知

## スキーマ（設定ファイル）
`data/sources.yaml`
```yaml
version: 1
sources:
  - id: gsi-nyusatu-1
    name: 国土地理院 入札公告1
    organization: 国土地理院
    url: https://www.gsi.go.jp/nyusatu1.rdf
    enabled: true
    timeout_sec: 20
    retries: 2
```

検証ルール:
- `id/name/organization/url` 必須
- `url` は `http://` または `https://`
- `id` 重複禁止
- **正規化後URL** 重複禁止

`data/keyword_sets.yaml`
```yaml
version: 1
keyword_sets:
  - id: set-a-it-ops-cloud
    name: "A: IT運用・保守・クラウド"
    enabled: true
    min_required_matches: 2
    required: [保守, 運用, 監視, 委託, 役務, システム]
    boost: [クラウド, AWS, Azure]
    exclude: [工事, 建設]
    exclude_exceptions: []
    top_n: 10
```

検証ルール:
- `id/name/enabled/min_required_matches/required/boost/exclude` 必須
- `id` 重複禁止
- `top_n` は整数（既定10）

## ディレクトリ構成
```text
.
├─ data/
│  ├─ sources.yaml
│  └─ keyword_sets.yaml
├─ docs/ops/
│  └─ initial-acquisition.md
├─ src/bid_rss_mailer/
│  ├─ main.py
│  ├─ pipeline.py
│  ├─ fetcher.py
│  ├─ scorer.py
│  ├─ storage.py
│  ├─ mailer.py
│  ├─ config.py
│  ├─ normalize.py
│  ├─ subscribers.py
│  ├─ x_draft.py
│  └─ x_publish.py
├─ tests/
├─ scripts/
│  ├─ generate_lp_config.py
│  └─ validate_lp.py
├─ web/lp/
│  ├─ index.html
│  ├─ styles.css
│  ├─ app.js
│  ├─ config.js
│  └─ free_today.json
└─ .github/workflows/
   ├─ ci.yml
   ├─ daily-mail.yml
   ├─ lp-verify.yml
   ├─ subscribers-verify.yml
   ├─ x-draft.yml
   └─ x-publish.yml
```

## 必要環境
- Python 3.12+
- SMTPサーバ

## セットアップ
```bash
python -m venv .venv
. .venv/Scripts/activate   # Windows PowerShell
pip install -r requirements.txt
```

`.env.example` をコピーして環境変数を設定する運用を推奨:
```powershell
Copy-Item .env.example .env
```

## 環境変数
- `ADMIN_EMAIL` 失敗通知/日次配信の宛先
- `SMTP_HOST`
- `SMTP_PORT`
- `SMTP_USER` (不要なら空文字可)
- `SMTP_PASS` (不要なら空文字可)
- `SMTP_FROM`
- `DB_PATH` (任意。未指定時は `data/app.db`)
- `APP_BASE_URL` (任意・将来拡張)
- `SMTP_STARTTLS` (任意。既定 `true`)
- `SMTP_USE_SSL` (任意。既定 `SMTP_PORT=465` 時 true)
- `LP_CHECKOUT_URL` (任意。LPの購入導線URL。未設定時は「準備中」表示)
- `LP_SUPPORT_EMAIL` (任意。LPの問い合わせ先。既定 `support@example.com`)
- `LP_PLAN_NAME` (任意。LPの価格表示。既定 `月額1,980円`)
- `LP_PUBLIC_URL` (X投稿文に入れるLP公開URL)
- `X_DRAFT_OUTPUT_DIR` (任意。X投稿文の出力先。既定 `out/x-drafts`)
- `X_PUBLISH_MODE` (任意。`manual` / `webhook` / `x_api_v2`。既定 `manual`)
- `X_WEBHOOK_URL` (`webhook` モード時に必須)
- `X_API_BEARER_TOKEN` (`x_api_v2` モード時に必須)

PowerShell例:
```powershell
$env:ADMIN_EMAIL="admin@example.com"
$env:SMTP_HOST="smtp.example.com"
$env:SMTP_PORT="587"
$env:SMTP_USER="mailer-user"
$env:SMTP_PASS="mailer-pass"
$env:SMTP_FROM="bid-rss-mailer@example.com"
$env:DB_PATH="data/app.db"
$env:LP_CHECKOUT_URL="https://checkout.stripe.com/c/pay/..."
$env:LP_SUPPORT_EMAIL="support@example.com"
$env:LP_PLAN_NAME="月額1,980円"
$env:LP_PUBLIC_URL="https://example.com/lp"
$env:X_DRAFT_OUTPUT_DIR="out/x-drafts"
$env:X_PUBLISH_MODE="manual"
$env:X_WEBHOOK_URL=""
$env:X_API_BEARER_TOKEN=""
```

## 実行コマンド
設定とDB初期化検証:
```bash
PYTHONPATH=src python -m bid_rss_mailer.main self-test
```
```powershell
$env:PYTHONPATH="src"
python -m bid_rss_mailer.main self-test
```

ドライラン（DB更新あり/メール送信なし）:
```bash
PYTHONPATH=src python -m bid_rss_mailer.main run --dry-run
```
```powershell
$env:PYTHONPATH="src"
python -m bid_rss_mailer.main run --dry-run
```

本番実行（メール送信あり）:
```bash
PYTHONPATH=src python -m bid_rss_mailer.main run
```
```powershell
$env:PYTHONPATH="src"
python -m bid_rss_mailer.main run
```

X投稿文下書き生成（Phase1。投稿API連携なし）:
```powershell
$env:PYTHONPATH="src"
python -m bid_rss_mailer.main x-draft --top-n 5
```
同日再生成を許可する場合:
```powershell
$env:PYTHONPATH="src"
python -m bid_rss_mailer.main x-draft --top-n 5 --force
```

X投稿実行（Phase2。mode切替）:
```powershell
$env:PYTHONPATH="src"
python -m bid_rss_mailer.main x-publish --mode manual
```
```powershell
$env:PYTHONPATH="src"
python -m bid_rss_mailer.main x-publish --mode webhook
```
```powershell
$env:PYTHONPATH="src"
python -m bid_rss_mailer.main x-publish --mode x_api_v2
```

購読者管理（Stripe未導入期間の手動運用）:
```powershell
$env:PYTHONPATH="src"
python -m bid_rss_mailer.main subscriber-add --email user1@example.com --plan manual --keyword-sets all
python -m bid_rss_mailer.main subscriber-stop --email user1@example.com
python -m bid_rss_mailer.main subscriber-list --json
```

## LP（Issue4）
ローカルでLPを確認:
```powershell
python scripts/generate_lp_config.py --output web/lp/config.js
python scripts/validate_lp.py
python -m http.server 8080 --directory web/lp
```

ブラウザで `http://127.0.0.1:8080` を開き、以下を確認:
- 無料枠/有料枠/免責/購入導線が表示される
- `LP_CHECKOUT_URL` 未設定時に「準備中」メッセージが表示される
- `free_today.json` のタイトル一覧が表示される（本文全文は保持しない）

## テスト
```bash
PYTHONPATH=src pytest -q
```
```powershell
$env:PYTHONPATH="src"
pytest -q
```

## GitHub Actions
- `ci.yml`: push / pull_request でテスト実行
- `daily-mail.yml`: 毎日定期実行 + 手動実行
  - cron: `0 22 * * *` (UTC 22:00 = JST 翌日 07:00)
  - `workflow_dispatch` で `dry_run` と `mock_smtp` を選択可
- `lp-verify.yml`: LP静的ページの検証 + artifact保存（`workflow_dispatch`対応）
- `subscribers-verify.yml`: subscriber DB/CLIの検証（`workflow_dispatch`対応）
- `x-draft.yml`: X投稿文（Phase1）生成 + artifact保存（`workflow_dispatch`対応）
- `x-publish.yml`: X投稿実行（Phase2）+ artifact保存（`workflow_dispatch`対応）

必要なSecrets:
- `ADMIN_EMAIL`
- `SMTP_HOST`
- `SMTP_PORT`
- `SMTP_USER`
- `SMTP_PASS`
- `SMTP_FROM`
- `LP_CHECKOUT_URL` (任意。設定した場合のみ購入導線が有効)

## 外部アカウントが必要な手作業（LP公開）
GitHub Pagesで公開する場合:
1. Repository Settings → Pages を開く
2. Source を `GitHub Actions` に設定
3. `lp-verify.yml` 実行で生成された `web/lp` を公開対象にするワークフローを追加（またはVercel等で `web/lp` を静的配信）
4. 公開URLを `APP_BASE_URL` に設定
5. Stripe Checkout URL作成後に `LP_CHECKOUT_URL` をSecretsに設定

チェック方法:
1. 公開URLでLP表示を確認
2. 「有料プランを開始する」リンクがCheckoutに遷移することを確認
3. 免責・無料枠・問い合わせ先が表示されることを確認

## X導線 Phase1（投稿文生成のみ）
- 生成ロジックはテンプレート + 上位N件のルールベースです（要約なし）。
- 1日1回（JST日付単位）で重複生成を抑止します。再生成する場合のみ `--force` を使います。
- 出力先は既定で `out/x-drafts/YYYY-MM-DD.txt` です。

運用手順（手動投稿）:
1. `python -m bid_rss_mailer.main x-draft --top-n 5` を実行
2. `out/x-drafts/YYYY-MM-DD.txt` の文面を確認
3. Xへ手動投稿

外部要件（Phase2）:
1. X APIアプリ作成・権限付与
2. 投稿用トークン発行とGitHub Secrets登録
3. 自動投稿ワークフローへ接続（Issue6で実装）

## X導線 Phase2（投稿実行）
- `x-publish` は mode を切り替えて実行します。
  - `manual`: 外部投稿せず、実行記録のみ作成
  - `webhook`: `X_WEBHOOK_URL` へ投稿文JSONを送信（Zapier/IFTTT連携向け）
  - `x_api_v2`: X API `POST /2/tweets` を直接実行（`X_API_BEARER_TOKEN` 必須）
- 同日重複投稿はDBで抑止されます。再実行は `--force` を使います。

外部要件で詰まる場合の前進手順:
1. まず `manual` で毎朝の運用を固定
2. 次に `webhook` 連携で自動化（X API不要）
3. 最後に `x_api_v2` に切り替える

## 初期獲得オペ
- 手順書: `docs/ops/initial-acquisition.md`

## Stripe未導入時の購読者運用
1. 管理者が `subscriber-add` で購読者を登録
2. 解約/停止時は `subscriber-stop` で状態を `stopped` に変更
3. `subscriber-list --json` で監査ログを取得

## 運用上の注意
- SQLiteはGitHub Actions上でキャッシュ復元して継続利用します。キャッシュが消えた場合は再送判定履歴がリセットされます。
- RSS取得失敗は実行を継続し、失敗一覧を警告メール通知します。
- SMTP送信失敗や設定不備は非0終了し、可能な場合は管理者に障害通知メールを送ります。
- DBは30日保持です。`deliveries` の古いレコードを削除し、参照されない古い `items` も削除します。
- 本MVPは本文全文保存を行いません。保存対象はタイトル/URL/機関/取得日時/スコア/セット名/締切抽出です。

## 重複URLの扱い
- URLは正規化してハッシュ化し `items.url_key` で一意管理します。
- 同一実行内でも同一 `item_id` はセット内で1件に集約します（重複行をメールに出さない）。
- 再実行時は `deliveries` の `(keyword_set_id, item_id)` で再送を防止します。

## 取得不可RSSの記録
- 実測で差し替えたRSSの記録: `https://github.com/yosukev2/bid-rss-mailer/issues/1#issuecomment-3903810714`

## 出典方針
- 出典は各アイテムのURLリンクを参照し、本文全文は保存しません。
- メール本文にはタイトル/URL/機関/日付/スコアのみを記載します。
