"""
scripts/notify_slack.py
スキャン結果をSlackに通知する。
GitHub Actions の daily_scan.yml から呼び出される。

必要な GitHub Secret:
  SLACK_WEBHOOK_URL: Slack Incoming Webhook URL
  （Slack App → Incoming Webhooks で発行）
"""

import json
import argparse
import os
import sys
import urllib.request
import urllib.error
from datetime import date


def load_alerts(json_path: str) -> list:
    try:
        with open(json_path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return []
    except json.JSONDecodeError:
        return []


def build_slack_message(alerts: list, scan_date: str) -> dict:
    """
    Slack Block Kit 形式のメッセージを構築。
    アラートなし・低スコアのみ・高スコアありで見た目を変える。
    """
    critical = [a for a in alerts if a["risk_level"] == "CRITICAL"]
    high     = [a for a in alerts if a["risk_level"] == "HIGH"]
    medium   = [a for a in alerts if a["risk_level"] == "MEDIUM"]

    total = len(alerts)

    # ── ヘッダー ──────────────────────────────────────────
    if critical:
        header_emoji = "🚨"
        header_text  = f"*TOB予兆検出 — 要注意案件あり*"
        header_color = "#ff3b3b"
    elif high:
        header_emoji = "⚠️"
        header_text  = f"*TOB予兆検出 — 注意案件あり*"
        header_color = "#ff8c00"
    elif total > 0:
        header_emoji = "📋"
        header_text  = f"*TOB予兆検出 — 継続監視案件*"
        header_color = "#f5c518"
    else:
        header_emoji = "✅"
        header_text  = f"*TOB予兆検出 — 異常なし*"
        header_color = "#4caf50"

    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"{header_emoji} EDINET 日次スキャン結果 {scan_date}",
            }
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"🔴 CRITICAL: *{len(critical)}件*"},
                {"type": "mrkdwn", "text": f"🟠 HIGH: *{len(high)}件*"},
                {"type": "mrkdwn", "text": f"🟡 MEDIUM: *{len(medium)}件*"},
                {"type": "mrkdwn", "text": f"📊 合計検出: *{total}件*"},
            ]
        },
        {"type": "divider"},
    ]

    # ── 高リスク案件の詳細（最大5件）──────────────────────
    top_alerts = sorted(alerts, key=lambda x: x["total_score"], reverse=True)[:5]

    for a in top_alerts:
        level = a["risk_level"]
        icon = "🔴" if level == "CRITICAL" else "🟠" if level == "HIGH" else "🟡"
        delay_info = f"{a['biz_days_late']}営業日遅延（{a['delay_level']}）"

        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"{icon} *{a['filer_name']}*\n"
                    f"　銘柄: `{a['sec_code']}`　"
                    f"スコア: *{a['total_score']}pt*\n"
                    f"　義務発生日: {a['obligation_date']} → 提出: {a['submit_date']}\n"
                    f"　{delay_info} / {a['ratio_label']}\n"
                    f"　{a['purpose_note']} / {a['holder_type']}"
                )
            },
            "accessory": {
                "type": "button",
                "text": {"type": "plain_text", "text": "EDINET"},
                "url": a.get("edinet_url", "https://disclosure.edinet-api.go.jp"),
                "action_id": f"edinet_{a['doc_id']}",
            }
        })

    if total > 5:
        blocks.append({
            "type": "context",
            "elements": [{
                "type": "mrkdwn",
                "text": f"他 {total - 5}件は tob_alerts_{scan_date}.json を参照"
            }]
        })

    blocks.append({"type": "divider"})
    blocks.append({
        "type": "context",
        "elements": [{
            "type": "mrkdwn",
            "text": (
                f"🤖 tob-detector 自動スキャン | "
                f"<https://github.com/${{GITHUB_REPOSITORY}}/actions|Actions>"
            )
        }]
    })

    return {
        "text": f"[TOB予兆] {scan_date} — CRITICAL:{len(critical)} HIGH:{len(high)}",
        "attachments": [{"color": header_color, "blocks": blocks}],
    }


def send_to_slack(webhook_url: str, payload: dict) -> bool:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        webhook_url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as res:
            return res.status == 200
    except urllib.error.URLError as e:
        print(f"Slack通知失敗: {e}", file=sys.stderr)
        return False


def main():
    parser = argparse.ArgumentParser(description="Slack通知スクリプト")
    parser.add_argument("--result", required=True, help="tob_alerts_YYYY-MM-DD.json のパス")
    parser.add_argument("--date",   required=True, help="スキャン日 YYYY-MM-DD")
    args = parser.parse_args()

    webhook_url = os.environ.get("SLACK_WEBHOOK")
    if not webhook_url:
        print("SLACK_WEBHOOK が未設定のためスキップ")
        sys.exit(0)

    alerts = load_alerts(args.result)
    payload = build_slack_message(alerts, args.date)

    success = send_to_slack(webhook_url, payload)
    if success:
        print(f"✅ Slack通知完了 ({len(alerts)}件)")
    else:
        print("❌ Slack通知失敗", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
