import os
import sys
import time
import io
import csv
import json
import zipfile
import argparse
import requests
import re
from datetime import datetime, date, timedelta

EDINET_BASE = 'https://api.edinet-fsa.go.jp/api/v2'
EDINET_API_KEY = os.environ.get('EDINET_API_KEY', '')

CORRECTION_CODES = {'351', '361'}  # 大量保有報告書(訂正) + 変更報告書(訂正)
MIN_LAG_DAYS = 60
RATE_LIMIT_SLEEP = 1.0

sys.path.insert(0, 'scripts')
from slack_notifier import post_to_slack


def parse_date_string(text):
    if not text:
        return None
    text = str(text).strip()
    m = re.search(r'(\d{4})[-/](\d{1,2})[-/](\d{1,2})', text)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass
    m = re.search(r'(令和|平成)(\d+)年(\d+)月(\d+)日', text)
    if m:
        era, y, mo, d = m.groups()
        year_offset = 2018 if era == '令和' else 1988
        try:
            return date(int(y) + year_offset, int(mo), int(d))
        except ValueError:
            pass
    return None


def fetch_documents_by_date(target_date):
    url = f'{EDINET_BASE}/documents.json'
    params = {'date': target_date, 'type': 2, 'Subscription-Key': EDINET_API_KEY}
    try:
        res = requests.get(url, params=params, timeout=30)
        res.raise_for_status()
        return res.json().get('results', [])
    except Exception:
        return []


def fetch_document_csv(doc_id):
    url = f'{EDINET_BASE}/documents/{doc_id}'
    params = {'type': 5, 'Subscription-Key': EDINET_API_KEY}
    try:
        res = requests.get(url, params=params, timeout=60)
        res.raise_for_status()
        return res.content
    except Exception:
        return None


def parse_correction_csv(zb):
    info = {'original_submit_date': None}
    if not zb:
        return info
    try:
        with zipfile.ZipFile(io.BytesIO(zb)) as zf:
            for n in [x for x in zf.namelist() if x.endswith('.csv') and 'XBRL_TO_CSV' in x]:
                txt = zf.read(n).decode('utf-16', errors='ignore')
                for row in csv.reader(io.StringIO(txt), delimiter='\t'):
                    if len(row) < 9:
                        continue
                    eid, val = row[0], row[8].strip()
                    if not val or val in ('-', '\uff0d'):
                        continue
                    if any(k in eid for k in [
                        'SubmittedDateOfDocumentBeingAmended',
                        'OriginalSubmissionDate',
                        'PreviouslySubmittedDate',
                    ]):
                        d = parse_date_string(val)
                        if d:
                            info['original_submit_date'] = d
    except Exception:
        pass
    return info


def find_parent_submit_date_via_api(parent_doc_id, current_submit_date, max_days_back=730):
    """逆算で元報告書の提出日を探す"""
    cur = current_submit_date - timedelta(days=1)
    end = current_submit_date - timedelta(days=max_days_back)
    while cur >= end:
        if cur.weekday() < 5:
            docs = fetch_documents_by_date(cur.isoformat())
            time.sleep(RATE_LIMIT_SLEEP)
            for d in docs:
                if d.get('docID') == parent_doc_id:
                    return cur
        cur -= timedelta(days=1)
    return None


def analyze_correction(doc):
    parent_id = doc.get('parentDocID')
    if not parent_id:
        return None

    submit_str = doc.get('submitDateTime', '')
    if not submit_str:
        return None
    submit_date = datetime.strptime(submit_str[:10], '%Y-%m-%d').date()

    # CSV から元提出日取得を試みる
    csv_bytes = fetch_document_csv(doc['docID'])
    time.sleep(RATE_LIMIT_SLEEP)
    info = parse_correction_csv(csv_bytes)
    original_date = info.get('original_submit_date')

    # CSVで取れない場合のみAPI逆算
    if not original_date:
        original_date = find_parent_submit_date_via_api(parent_id, submit_date)

    if not original_date:
        return None

    lag_days = (submit_date - original_date).days
    if lag_days < MIN_LAG_DAYS:
        return None

    sec = doc.get('secCode') or ''
    if sec.endswith('0'):
        sec = sec[:-1]

    return {
        'doc_id': doc['docID'],
        'parent_doc_id': parent_id,
        'submit_date': submit_str[:10],
        'original_submit_date': original_date.isoformat(),
        'lag_days': lag_days,
        'filer_name': doc.get('filerName', ''),
        'edinet_code': doc.get('edinetCode', ''),
        'sec_code': sec[:4],
    }


def scan_date(target_date):
    docs = fetch_documents_by_date(target_date)
    time.sleep(RATE_LIMIT_SLEEP)
    corrections = [d for d in docs if d.get('docTypeCode') in CORRECTION_CODES]
    print(f'  corrections: {len(corrections)}')

    alerts = []
    for doc in corrections:
        r = analyze_correction(doc)
        if r:
            print(f"  [遅延 {r['lag_days']}日] {r['filer_name'][:25]}")
            alerts.append(r)
    return alerts


def build_message(alerts, scan_date):
    if not alerts:
        return None
    lines = [f'*【訂正報告書】60日超の長期経過 ({scan_date})*', f'{len(alerts)}件', '']
    for i, a in enumerate(sorted(alerts, key=lambda x: -x.get('lag_days', 0))[:15], 1):
        sec = a.get('sec_code') or ''
        sec_str = f' ({sec})' if sec else ''
        lines.append(f":memo: *[{i}] {a.get('lag_days')}日経過の訂正* {a.get('filer_name','')}{sec_str}")
        lines.append(f"    元提出: {a.get('original_submit_date')} / 訂正提出: {a.get('submit_date')}")
        lines.append('')
    return '\n'.join(lines)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--date')
    p.add_argument('--output', default='correction_alerts')
    args = p.parse_args()

    if not EDINET_API_KEY:
        print('EDINET_API_KEY not set', file=sys.stderr)
        sys.exit(1)

    target = args.date or date.today().isoformat()
    print(f'correction lag scan: {target}')

    alerts = scan_date(target)
    print(f'\n=== detected: {len(alerts)} ===')

    if alerts:
        os.makedirs(os.path.dirname(args.output) or '.', exist_ok=True)
        with open(f'{args.output}.json', 'w', encoding='utf-8') as f:
            json.dump(alerts, f, ensure_ascii=False, indent=2, default=str)
        msg = build_message(alerts, target)
        if msg:
            post_to_slack('SLACK_WEBHOOK_CORRECTION', msg)


if __name__ == '__main__':
    main()
