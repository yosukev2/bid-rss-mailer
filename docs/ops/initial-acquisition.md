# 初期獲得オペ手順書（M5: 10人有料化）

## 目的
- まず10人の有料登録を安定運用で獲得し、次に25人へ伸ばす。
- 追加開発より、毎朝配信品質と導線の一貫運用を優先する。

## 前提
- LP公開済み
- `x-draft` / `x-publish` が日次で動作
- 配信失敗通知が管理者へ届く

## 毎朝オペ（JST）
1. `daily-mail.yml` の実行結果を確認（取得件数/送信件数/失敗ソース）
2. `x-draft.yml` の投稿文内容を確認
3. `x-publish.yml` で `manual` または `webhook` で投稿実行
4. 投稿後30分でLP遷移数・問い合わせ数を記録

## 日次ログ項目（最低限）
- RSS取得件数
- セット別候補件数
- 実メール送信件数
- X投稿実行モード（manual/webhook/x_api_v2）
- X投稿ステータス（posted/skipped/manual_ready）
- 失敗ソース一覧

## KPI（最初の14日）
- LP訪問数 / 日
- X投稿→LPクリック率
- LP→Checkout遷移率
- Checkout→有料化率
- 解約率（週次）

## 失敗時対応
- `x-publish` が失敗:
  1. `out/x-publish/*.json` の `status` / `response_body` を確認
  2. mode別設定を再確認（`X_WEBHOOK_URL` or `X_API_BEARER_TOKEN`）
  3. その日は `manual` mode で投稿して欠損を回避
- `daily-mail` が失敗:
  1. SMTP疎通とSecretsを確認
  2. `run --dry-run` でRSS取得のみ再確認
  3. 必要なら手動で告知のみ先行

## 週次改善ループ
1. 上位3投稿のテーマ（キーワードセット）を抽出
2. クリック率が低い投稿の冒頭文テンプレを修正（要約はしない）
3. LPの無料/有料差説明と免責文を改善
4. 変更後1週間でKPI比較
