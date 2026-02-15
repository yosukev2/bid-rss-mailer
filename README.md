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
├─ src/bid_rss_mailer/
│  ├─ main.py
│  ├─ pipeline.py
│  ├─ fetcher.py
│  ├─ scorer.py
│  ├─ storage.py
│  ├─ mailer.py
│  ├─ config.py
│  └─ normalize.py
├─ tests/
└─ .github/workflows/
   ├─ ci.yml
   └─ daily-mail.yml
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

PowerShell例:
```powershell
$env:ADMIN_EMAIL="admin@example.com"
$env:SMTP_HOST="smtp.example.com"
$env:SMTP_PORT="587"
$env:SMTP_USER="mailer-user"
$env:SMTP_PASS="mailer-pass"
$env:SMTP_FROM="bid-rss-mailer@example.com"
$env:DB_PATH="data/app.db"
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

必要なSecrets:
- `ADMIN_EMAIL`
- `SMTP_HOST`
- `SMTP_PORT`
- `SMTP_USER`
- `SMTP_PASS`
- `SMTP_FROM`

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
