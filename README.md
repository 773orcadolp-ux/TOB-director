# TOB予兆検出システム

EDINET の大量保有報告書・変更報告書を監視し、TOB/MBO/株式交換の予兆シグナルを自動検出するツール。

## 背景

2026年のOlympicグループ（8289）のPPIHによる買収事例の分析から、変更報告書の**長期遅延**が重要な予兆シグナルになることが判明。本ツールはこの知見をベースに構築。

| シグナル | Olympic事例 | スコア |
|---------|------------|--------|
| 変更報告書の遅延 | **11ヶ月（218営業日）** | +5 |
| 創業家系の持分集約 | 31.45% | +3 |
| 借入金による株式取得 | みずほ銀行94% | +4 |
| 業績の急激な悪化 | 営業利益98%減 | +4 |

## インストール

```bash
git clone https://github.com/yourname/tob-detector.git
cd tob-detector
pip install -r requirements.txt
```

## 使い方

```bash
# デモ（ネットワーク不要・動作確認用）
python -m detector.tob_detector --demo

# 日付パーサーのテスト
python -m detector.tob_detector --parse-test

# 特定日のスキャン
python -m detector.tob_detector --date 2026-01-23

# 期間スキャン
python -m detector.tob_detector --range 2025-01-01 2026-04-01

# 銘柄を絞って期間スキャン
python -m detector.tob_detector --range 2025-01-01 2026-04-01 --code 8289
```

## スコアリング基準

| スコア合計 | リスクレベル | 対応 |
|-----------|-------------|------|
| 10以上 | 🔴 CRITICAL | 即時精査推奨 |
| 7〜9   | 🟠 HIGH | 業績・適時開示と突合 |
| 4〜6   | 🟡 MEDIUM | 継続監視 |
| 〜3    | ⬜ LOW | 通常 |

## 自動化（GitHub Actions）

平日20時（JST）に自動スキャン。結果はSlackに通知。

**初期設定:**
1. GitHub の Settings → Secrets → Actions に追加
   - `SLACK_WEBHOOK_URL`: Slack Incoming Webhook URL

**手動実行:**
GitHub Actions タブ → "TOB予兆 日次スキャン" → "Run workflow"

## 2段階パイプライン

```
Stage 1: EDINETのAPIメタデータのみ（高速・近似）
  periodEnd と提出日の差が 10日超 → Stage 2 候補へ

Stage 2: ZIPダウンロードしてXMLを精査（正確）
  表紙の「報告義務発生日」を直接取得
  和暦（令和・平成・昭和）→ 西暦変換に対応
```

## ⚠️ 既知の課題

- `periodEnd` が空欄の書類はStage1で検出漏れになる可能性あり
  → 実際のAPIレスポンスで確認中（Issue #1 参照）
- スコアリングの重みはOlympic事例1件から導出。他事例での検証が必要

## ディレクトリ構成

```
tob-detector/
├── .github/workflows/
│   ├── daily_scan.yml   # 日次自動スキャン
│   └── test.yml         # PR時のテスト
├── detector/
│   └── tob_detector.py  # メインスクリプト
├── tests/
│   └── test_date_parser.py
├── scripts/
│   └── notify_slack.py  # Slack通知
└── results/             # スキャン結果（.gitignore対象）
```
