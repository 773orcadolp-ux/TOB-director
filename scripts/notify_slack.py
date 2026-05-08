import argparse
import json
import os
import sys
import urllib.request


def build_holding_section(alerts):
    if not alerts:
        return None
    lines = ['*【大量保有報告書 長期遅延】*']
    lines.append(f'検出: {len(alerts)}件')
    lines.append('')
    top = sorted(alerts, key=lambda x: -x.get('biz_days_late', 0))[:10]
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
    return '\n'.join(lines)


def build_earnings_section(alerts):
    if not alerts:
        return None
    lines = ['*【決算遅延】*']
    lines.append(f'検出: {len(alerts)}件')
    lines.append('')
    top = sorted(alerts, key=lambda x: -x.get('delay_days', 0))[:10]
    for i, a in enumerate(top, 1):
        sec = a.get('sec_code') or ''
        sec_str = f' ({sec})' if sec else ''
        lines.append(f":calendar: *[{i}] {a.get('delay_days')}日遅延* {a.get('filer_name','')}{sec_str}")
        lines.append(f"    {a.get('doc_type','')} / 期末:{a.get('period_end','')} / 提出:{a.get('submit_date','')}")
        lines.append('')
    return '\n'.join(lines)


def build_spc_section(alerts):
    if not alerts:
        return None
    lines = ['*【SPC設立検知】*']
    lines.append(f'検出: {len(alerts)}件')
    lines.append('')
    for i, a in enumerate(alerts[:10], 1):
        flag = ':red_circle:' if a.get('is_suspicious_name') else ':large_orange_circle:'
        lines.append(f"{flag} *[{i}] [{a.get('category')}] {a.get('name')}*")
        lines.append(f"    住所: {a.get('matched_org')} / 設立: {a.get('change_date')}")
        lines.append(f"    法人番号: {a.get('corp_num')}")
        lines.append('')
    return '\n'.join(lines)


def post_to_slack(webhook, text):
    payload = {'text': text}
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


def load_json(path):
    if not path or not os.path.exists(path):
        return []
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return []


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--result', help='大量保有JSONファイル')
    p.add_argument('--earnings', help='決算遅延JSONファイル')
    p.add_argument('--spc', help='SPC検知JSONファイル')
    p.add_argument('--date', required=True)
    args = p.parse_args()

    webhook = os.environ.get('SLACK_WEBHOOK', '') or os.environ.get('SLACK_WEBHOOK_URL', '')
    if not webhook:
        print('SLACK_WEBHOOK not set', file=sys.stderr)
        return

    holding_alerts = load_json(args.result)
    earnings_alerts = load_json(args.earnings)
    spc_alerts = load_json(args.spc)

    sections = [f'*TOB予兆スキャン ({args.date})*', '']
    has_content = False

    for builder, data in [
        (build_holding_section, holding_alerts),
        (build_earnings_section, earnings_alerts),
        (build_spc_section, spc_alerts),
    ]:
        section = builder(data)
        if section:
            sections.append(section)
            has_content = True

    if not has_content:
        print('No alerts to notify')
        return

    post_to_slack(webhook, '\n'.join(sections))


if __name__ == '__main__':
    main()
