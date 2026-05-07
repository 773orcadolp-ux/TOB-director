import argparse
import json
import os
import sys
import urllib.request


def build_message(alerts, scan_date):
    if not alerts:
        return None

    by_level = {'CRITICAL': [], 'HIGH': [], 'MEDIUM': []}
    for a in alerts:
        lv = a.get('risk_level', 'LOW')
        if lv in by_level:
            by_level[lv].append(a)

    lines = [f'*TOB予兆スキャン結果 ({scan_date})*']
    lines.append(f"検出: CRITICAL {len(by_level['CRITICAL'])} / HIGH {len(by_level['HIGH'])} / MEDIUM {len(by_level['MEDIUM'])}")
    lines.append('')

    top = sorted(alerts, key=lambda x: -x.get('total_score', 0))[:10]
    for i, a in enumerate(top, 1):
        emoji = {'CRITICAL': ':red_circle:', 'HIGH': ':large_orange_circle:',
                 'MEDIUM': ':large_yellow_circle:'}.get(a.get('risk_level'), ':white_circle:')
        target = a.get('target_name') or a.get('issuer_edinet_code', '?')
        sec = a.get('target_sec_code') or ''
        sec_str = f' ({sec})' if sec else ''
        ratio = a.get('holding_ratio')
        ratio_str = f"{ratio:.2f}%" if ratio is not None else '?'
        purpose = (a.get('purpose') or '')[:40]
        lines.append(f"{emoji} [{i}] *{target}*{sec_str} score:{a.get('total_score')}")
        lines.append(f"    提出者: {a.get('filer_name','')} / 保有率: {ratio_str} / 遅延: {a.get('biz_days_late','?')}営業日")
        if purpose:
            lines.append(f"    目的: {purpose}")
        lines.append('')

    return {'text': '\n'.join(lines)}


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--result', required=True)
    p.add_argument('--date', required=True)
    args = p.parse_args()

    webhook = os.environ.get('SLACK_WEBHOOK', '') or os.environ.get('SLACK_WEBHOOK_URL', '')
    if not webhook:
        print('SLACK_WEBHOOK not set', file=sys.stderr)
        return

    if not os.path.exists(args.result):
        print(f'No result file: {args.result} (likely 0 alerts)')
        return

    with open(args.result, encoding='utf-8') as f:
        alerts = json.load(f)

    if not alerts:
        print('No alerts to notify')
        return

    payload = build_message(alerts, args.date)
    if not payload:
        return

    req = urllib.request.Request(
        webhook,
        data=json.dumps(payload).encode('utf-8'),
        headers={'Content-Type': 'application/json'},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as res:
            print(f'Slack notified: {res.status}')
    except Exception as e:
        print(f'Slack notification failed: {e}', file=sys.stderr)


if __name__ == '__main__':
    main()
