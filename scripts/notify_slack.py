import argparse
import json
import os
import sys
import urllib.request


def build_message(alerts, scan_date):
    if not alerts:
        return None

    lines = [f'*TOB予兆スキャン ({scan_date})*']
    lines.append(f'長期遅延（100営業日超）: {len(alerts)}件')
    lines.append('')

    top = sorted(alerts, key=lambda x: -x.get('biz_days_late', 0))[:15]
    for i, a in enumerate(top, 1):
        target = a.get('target_name') or a.get('issuer_edinet_code', '?')
        sec = a.get('target_sec_code') or ''
        sec_str = f' ({sec})' if sec else ''

        ratio = a.get('holding_ratio')
        ratio_str = f"{ratio:.2f}%" if ratio is not None else '?'
        prev = a.get('holding_ratio_prev')
        prev_str = f' (前回{prev:.2f}%)' if prev is not None else ''

        pbr = a.get('pbr')
        pbr_str = f"PBR {pbr:.2f}" if pbr is not None else 'PBR ?'

        div = a.get('dividend_yield')
        div_str = f"配当{div*100:.2f}%" if div is not None else '配当 ?'

        purpose = (a.get('purpose') or '')[:40]

        lines.append(f":alarm_clock: *[{i}] {a.get('biz_days_late')}営業日遅延* {target}{sec_str}")
        lines.append(f"    提出者: {a.get('filer_name','')}")
        lines.append(f"    保有率: {ratio_str}{prev_str} / {pbr_str} / {div_str}")
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
        print(f'No result file: {args.result}')
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
