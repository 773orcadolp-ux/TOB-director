import os
import sys
import time
import json
import argparse
import requests
import re
from datetime import datetime, date, timedelta

EDINET_BASE = 'https://api.edinet-fsa.go.jp/api/v2'
EDINET_API_KEY = os.environ.get('EDINET_API_KEY', '')

EARNINGS_REPORTS = {
    '120': ('有価証券報告書', 90),
    '121': ('有価証券報告書(訂正)', 90),
    '130': ('四半期報告書', 45),
    '131': ('四半期報告書(訂正)', 45),
    '140': ('半期報告書', 90),
    '141': ('半期報告書(訂正)', 90),
}

MIN_DELAY_DAYS = 30  # 30日以上の遅延を通知対象に
TARGET_CACHE_FILE = 'target_cache.json'


def parse_date(text):
    if not text:
        return None
    m = re.search(r'(\d{4})[-/](\d{1,2})[-/](\d{1,2})', str(text))
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass
    return None


def fetch_documents(target_date):
    url = f'{EDINET_BASE}/documents.json'
    params = {'date': target_date, 'type': 2, 'Subscription-Key': EDINET_API_KEY}
    try:
        res = requests.get(url, params=params, timeout=30)
        res.raise_for_status()
        return res.json().get('results', [])
    except Exception as e:
        print(f'  warn: {e}', file=sys.stderr)
        return []


def analyze(doc):
    code = doc.get('docTypeCode')
    if code not in EARNINGS_REPORTS:
        return None

    period_end = parse_date(doc.get('periodEnd'))
    if not period_end:
        return None

    submit_str = doc.get('submitDateTime', '')
    if not submit_str:
        return None
    submit_date = datetime.strptime(submit_str[:10], '%Y-%m-%d').date()

    name, deadline_days = EARNINGS_REPORTS[code]
    deadline = period_end + timedelta(days=deadline_days)
    delay = (submit_date - deadline).days

    if delay < MIN_DELAY_DAYS:
        return None

    sec = doc.get('secCode') or ''
    if sec.endswith('0'):
        sec = sec[:-1]

    return {
        'doc_id': doc.get('docID'),
        'doc_type': name,
        'submit_date': submit_str[:10],
        'period_end': period_end.isoformat(),
        'deadline': deadline.isoformat(),
        'delay_days': delay,
        'filer_name': doc.get('filerName', ''),
        'edinet_code': doc.get('edinetCode', ''),
        'sec_code': sec[:4],
    }


def scan(target_date):
    print(f'earnings delay scan: {target_date}')
    docs = fetch_documents(target_date)
    print(f'  total: {len(docs)}')

    targets = [d for d in docs if d.get('docTypeCode') in EARNINGS_REPORTS]
    print(f'  earnings reports: {len(targets)}')

    alerts = []
    for d in targets:
        r = analyze(d)
        if r:
            print(f"  [{r['delay_days']}d delay] {r['doc_type']}: {r['filer_name'][:25]}")
            alerts.append(r)
    return alerts


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--date')
    p.add_argument('--output', default='earnings_alerts')
    args = p.parse_args()

    if not EDINET_API_KEY:
        print('EDINET_API_KEY not set', file=sys.stderr)
        sys.exit(1)

    target = args.date or date.today().isoformat()
    alerts = scan(target)

    print(f'\n=== detected: {len(alerts)} ===')
    for a in sorted(alerts, key=lambda x: -x['delay_days'])[:10]:
        print(f"[{a['delay_days']}d] {a['doc_type']}: {a['filer_name']} ({a['sec_code']})")

    if alerts:
        os.makedirs(os.path.dirname(args.output) or '.', exist_ok=True)
        with open(f'{args.output}.json', 'w', encoding='utf-8') as f:
            json.dump(alerts, f, ensure_ascii=False, indent=2, default=str)


if __name__ == '__main__':
    main()
