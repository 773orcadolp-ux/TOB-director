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

LARGE_HOLDING_CODES = {'350', '351', '360', '361'}
TOB_CODES = {'240', '250', '270', '290', '300'}
LEGAL_LIMIT_BIZ_DAYS = 5
RATE_LIMIT_SLEEP = 1.0

WAREKI_START = {
    '\u4ee4\u548c': date(2019, 5, 1),
    '\u5e73\u6210': date(1989, 1, 8),
    '\u662d\u548c': date(1926, 12, 25),
}


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
    return None


def calc_business_days(start, end):
    if end < start:
        return 0
    count = 0
    cur = start
    while cur <= end:
        if cur.weekday() < 5:
            count += 1
        cur += timedelta(days=1)
    return count


def fetch_documents_by_date(target_date):
    url = f'{EDINET_BASE}/documents.json'
    params = {'date': target_date, 'type': 2, 'Subscription-Key': EDINET_API_KEY}
    try:
        res = requests.get(url, params=params, timeout=30)
        res.raise_for_status()
        return res.json().get('results', [])
    except Exception as e:
        print(f'  warn: {e}', file=sys.stderr)
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


def parse_csv_zip(zip_bytes):
    result = {
        'obligation_date': None,
        'holding_ratio': None,
        'purpose': None,
        'filer_business': None,
    }
    if not zip_bytes:
        return result
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            for csv_name in [n for n in zf.namelist() if n.endswith('.csv') and 'XBRL_TO_CSV' in n]:
                text = zf.read(csv_name).decode('utf-16', errors='ignore')
                for row in csv.reader(io.StringIO(text), delimiter='\t'):
                    if len(row) < 9:
                        continue
                    eid, ctx, val = row[0], row[2], row[8].strip()
                    if not val or val in ('-', '\uff0d'):
                        continue
                    if 'DateWhenFilingRequirementAroseCoverPage' in eid:
                        d = parse_date_string(val)
                        if d:
                            result['obligation_date'] = d
                    elif eid.endswith('HoldingRatioOfShareCertificatesEtc') and ctx == 'FilingDateInstant':
                        try:
                            result['holding_ratio'] = float(val) * 100
                        except ValueError:
                            pass
                    elif 'PurposeOfHolding' in eid and 'FilerLargeVolumeHolder1Member' in ctx:
                        result['purpose'] = val[:200]
                    elif 'DescriptionOfBusiness' in eid and 'FilerLargeVolumeHolder1Member' in ctx:
                        result['filer_business'] = val[:200]
    except Exception:
        pass
    return result


def find_tob_announced(start_str, end_str):
    tob_issuers = set()
    cur = date.fromisoformat(start_str)
    end = date.fromisoformat(end_str)
    while cur <= end:
        if cur.weekday() < 5:
            docs = fetch_documents_by_date(cur.isoformat())
            time.sleep(RATE_LIMIT_SLEEP)
            for d in docs:
                if d.get('docTypeCode') in TOB_CODES:
                    target = d.get('subjectEdinetCode') or d.get('issuerEdinetCode')
                    if target:
                        tob_issuers.add(target)
        cur += timedelta(days=1)
    return tob_issuers


def score_delay(d):
    if d <= 0: return 0
    if d <= 22: return 2
    if d <= 65: return 3
    if d <= 130: return 4
    return 5


def score_ratio(r):
    if r is None: return 0
    if r >= 33.34: return 5
    if r >= 30: return 4
    if r >= 20: return 3
    if r >= 10: return 2
    if r >= 5: return 1
    return 0


def score_purpose(p):
    if not p:
        return 0
    excluded = ['\u516c\u958b\u8cb7\u4ed8', 'TOB', '\u682a\u5f0f\u4ea4\u63db', '\u5408\u4f75', '\u8cb7\u53ce']
    for kw in excluded:
        if kw in p:
            return 0
    high = ['\u7d4c\u55b6\u53c2\u52a0', '\u91cd\u8981\u63d0\u6848', '\u7d4c\u55b6\u6a29', '\u652f\u914d\u6a29']
    for kw in high:
        if kw in p:
            return 4
    return 0


def score_holder(name, biz):
    text = (name or '') + (biz or '')
    founding = ['\u4e0d\u52d5\u7523', '\u8cc7\u7523\u7ba1\u7406', '\u6709\u4fa1\u8a3c\u5238\u6295\u8cc7']
    for kw in founding:
        if kw in text:
            return 3
    return 1


def analyze(doc, csv_data):
    if doc.get('docTypeCode') not in LARGE_HOLDING_CODES:
        return None
    submit_str = doc.get('submitDateTime', '')
    if not submit_str:
        return None
    submit_date = datetime.strptime(submit_str[:10], '%Y-%m-%d').date()
    obligation = csv_data.get('obligation_date') if csv_data else None
    if not obligation:
        return None

    biz_late = max(0, calc_business_days(obligation, submit_date) - LEGAL_LIMIT_BIZ_DAYS)
    delay_s = score_delay(biz_late)
    rs = score_ratio(csv_data.get('holding_ratio'))
    ps = score_purpose(csv_data.get('purpose'))
    hs = score_holder(doc.get('filerName'), csv_data.get('filer_business'))
    total = delay_s + rs + ps + hs
    level = 'CRITICAL' if total >= 10 else 'HIGH' if total >= 7 else 'MEDIUM' if total >= 4 else 'LOW'

    return {
        'doc_id': doc.get('docID'),
        'submit_date': submit_str[:10],
        'obligation_date': obligation.isoformat(),
        'filer_name': doc.get('filerName', ''),
        'issuer_edinet_code': doc.get('issuerEdinetCode') or '',
        'biz_days_late': biz_late,
        'holding_ratio': csv_data.get('holding_ratio'),
        'purpose': csv_data.get('purpose'),
        'filer_business': csv_data.get('filer_business'),
        'total_score': total,
        'risk_level': level,
    }


def scan_date(target_date, threshold=4, tob_announced=None):
    print(f'scan: {target_date}')
    docs = fetch_documents_by_date(target_date)
    print(f'  total: {len(docs)}')
    time.sleep(RATE_LIMIT_SLEEP)
    targets = [d for d in docs if d.get('docTypeCode') in LARGE_HOLDING_CODES]

    if tob_announced:
        before = len(targets)
        targets = [d for d in targets if (d.get('issuerEdinetCode') or '') not in tob_announced]
        print(f'  large holding: {before} (TOB excluded {before - len(targets)} -> {len(targets)})')
    else:
        print(f'  large holding: {len(targets)}')

    alerts = []
    for i, doc in enumerate(targets, 1):
        csv_bytes = fetch_document_csv(doc['docID'])
        time.sleep(RATE_LIMIT_SLEEP)
        cd = parse_csv_zip(csv_bytes) if csv_bytes else {}
        r = analyze(doc, cd)
        if r and r['total_score'] >= threshold:
            print(f"  [{r['risk_level']}] {doc.get('filerName','')[:25]} score:{r['total_score']}")
            alerts.append(r)
    return alerts


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--date')
    p.add_argument('--threshold', type=int, default=7)
    p.add_argument('--output', default='tob_alerts')
    p.add_argument('--tob-cache-days', type=int, default=365)
    args = p.parse_args()

    if not EDINET_API_KEY:
        print('EDINET_API_KEY not set', file=sys.stderr)
        sys.exit(1)

    target = args.date or date.today().isoformat()

    print(f'Building TOB-announced cache (last {args.tob_cache_days} days)...')
    end_d = date.fromisoformat(target)
    start_d = end_d - timedelta(days=args.tob_cache_days)
    tob_announced = find_tob_announced(start_d.isoformat(), end_d.isoformat())
    print(f'TOB-announced companies: {len(tob_announced)}')

    alerts = scan_date(target, args.threshold, tob_announced)

    print(f'\n=== detected: {len(alerts)} ===')
    for a in sorted(alerts, key=lambda x: -x['total_score'])[:10]:
        print(f"[{a['risk_level']}] {a['filer_name']} score:{a['total_score']}")

    if alerts:
        os.makedirs(os.path.dirname(args.output) or '.', exist_ok=True)
        with open(f'{args.output}.json', 'w', encoding='utf-8') as f:
            json.dump(alerts, f, ensure_ascii=False, indent=2, default=str)
        print(f'\nSaved: {args.output}.json')


if __name__ == '__main__':
    main()
