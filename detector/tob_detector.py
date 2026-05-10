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
TARGET_CACHE_FILE = 'target_cache.json'
MIN_DELAY_BIZ_DAYS = 100

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


def parse_csv_zip(zb):
    res = {'obligation_date': None, 'holding_ratio': None,
           'holding_ratio_prev': None, 'purpose': None}
    if not zb:
        return res
    try:
        with zipfile.ZipFile(io.BytesIO(zb)) as zf:
            for n in [x for x in zf.namelist() if x.endswith('.csv') and 'XBRL_TO_CSV' in x]:
                txt = zf.read(n).decode('utf-16', errors='ignore')
                for row in csv.reader(io.StringIO(txt), delimiter='\t'):
                    if len(row) < 9:
                        continue
                    eid, ctx, val = row[0], row[2], row[8].strip()
                    if not val or val in ('-', '\uff0d'):
                        continue
                    if 'DateWhenFilingRequirementAroseCoverPage' in eid:
                        d = parse_date_string(val)
                        if d:
                            res['obligation_date'] = d
                    elif eid.endswith('HoldingRatioOfShareCertificatesEtc') and ctx == 'FilingDateInstant':
                        try:
                            res['holding_ratio'] = float(val) * 100
                        except ValueError:
                            pass
                    elif eid.endswith('HoldingRatioOfShareCertificatesEtcPerLastReport') and ctx == 'FilingDateInstant':
                        try:
                            res['holding_ratio_prev'] = float(val) * 100
                        except ValueError:
                            pass
                    elif 'PurposeOfHolding' in eid and 'FilerLargeVolumeHolder1Member' in ctx:
                        res['purpose'] = val[:200]
    except Exception:
        pass
    return res


def update_cache(cache, docs):
    for d in docs:
        code = d.get('edinetCode')
        if code and code not in cache:
            sec = d.get('secCode') or ''
            if sec.endswith('0'):
                sec = sec[:-1]
            cache[code] = {'name': d.get('filerName', ''), 'sec_code': sec[:4]}


def find_tob_announced(start_str, end_str, cache):
    tob_issuers = set()
    cur = date.fromisoformat(start_str)
    end = date.fromisoformat(end_str)
    while cur <= end:
        if cur.weekday() < 5:
            docs = fetch_documents_by_date(cur.isoformat())
            time.sleep(RATE_LIMIT_SLEEP)
            update_cache(cache, docs)
            for d in docs:
                if d.get('docTypeCode') in TOB_CODES:
                    target = d.get('subjectEdinetCode') or d.get('issuerEdinetCode')
                    if target:
                        tob_issuers.add(target)
        cur += timedelta(days=1)
    return tob_issuers


def get_pbr_dividend(sec_code):
    if not sec_code:
        return None, None
    try:
        import yfinance as yf
        ticker = yf.Ticker(f'{sec_code}.T')
        info = ticker.info
        pbr = info.get('priceToBook')
        div = info.get('dividendYield')
        if div and div > 1:
            div = div / 100
        return pbr, div
    except Exception:
        return None, None


def analyze(doc, cd, cache):
    submit_str = doc.get('submitDateTime', '')
    if not submit_str:
        return None
    submit_date = datetime.strptime(submit_str[:10], '%Y-%m-%d').date()
    obligation = cd.get('obligation_date')
    if not obligation:
        return None
    biz_late = max(0, calc_business_days(obligation, submit_date) - LEGAL_LIMIT_BIZ_DAYS)
    if biz_late < MIN_DELAY_BIZ_DAYS:
        return None

    issuer_code = doc.get('issuerEdinetCode') or ''
    target = cache.get(issuer_code, {})
    sec_code = target.get('sec_code', '')
    pbr, div = get_pbr_dividend(sec_code) if sec_code else (None, None)

    return {
        'doc_id': doc.get('docID'),
        'submit_date': submit_str[:10],
        'obligation_date': obligation.isoformat(),
        'filer_name': doc.get('filerName', ''),
        'issuer_edinet_code': issuer_code,
        'target_name': target.get('name', '?'),
        'target_sec_code': sec_code,
        'biz_days_late': biz_late,
        'holding_ratio': cd.get('holding_ratio'),
        'holding_ratio_prev': cd.get('holding_ratio_prev'),
        'purpose': cd.get('purpose'),
        'pbr': pbr,
        'dividend_yield': div,
    }


def scan_date(target_date, cache, tob_announced):
    print(f'scan: {target_date}')
    docs = fetch_documents_by_date(target_date)
    update_cache(cache, docs)
    print(f'  total: {len(docs)}')
    time.sleep(RATE_LIMIT_SLEEP)
    targets = [d for d in docs if d.get('docTypeCode') in LARGE_HOLDING_CODES
               and (d.get('issuerEdinetCode') or '') not in tob_announced]
    print(f'  large holding: {len(targets)}')

    alerts = []
    for doc in targets:
        csv_bytes = fetch_document_csv(doc['docID'])
        time.sleep(RATE_LIMIT_SLEEP)
        cd = parse_csv_zip(csv_bytes) if csv_bytes else {}
        r = analyze(doc, cd, cache)
        if r:
            print(f"  [長期遅延 {r['biz_days_late']}d] {r['target_name'][:20]} <- {doc.get('filerName','')[:20]}")
            alerts.append(r)
    return alerts


def build_message(alerts, scan_date):
    if not alerts:
        return None
    lines = [f'*【大量保有遅延】長期遅延検知 ({scan_date})*', f'{len(alerts)}件', '']
    for i, a in enumerate(sorted(alerts, key=lambda x: -x['biz_days_late'])[:15], 1):
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


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--date')
    p.add_argument('--output', default='tob_alerts')
    p.add_argument('--tob-cache-days', type=int, default=365)
    args = p.parse_args()

    if not EDINET_API_KEY:
        print('EDINET_API_KEY not set', file=sys.stderr)
        sys.exit(1)

    target = args.date or date.today().isoformat()

    cache = {}
    if os.path.exists(TARGET_CACHE_FILE):
        try:
            with open(TARGET_CACHE_FILE, encoding='utf-8') as f:
                cache = json.load(f)
        except Exception:
            pass
    print(f'Target cache: {len(cache)}')

    print(f'Building TOB-announced list...')
    end_d = date.fromisoformat(target)
    start_d = end_d - timedelta(days=args.tob_cache_days)
    tob_announced = find_tob_announced(start_d.isoformat(), end_d.isoformat(), cache)
    print(f'TOB-announced: {len(tob_announced)}')

    alerts = scan_date(target, cache, tob_announced)

    with open(TARGET_CACHE_FILE, 'w', encoding='utf-8') as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)

    print(f'\n=== detected: {len(alerts)} ===')
    if alerts:
        os.makedirs(os.path.dirname(args.output) or '.', exist_ok=True)
        with open(f'{args.output}.json', 'w', encoding='utf-8') as f:
            json.dump(alerts, f, ensure_ascii=False, indent=2, default=str)
        msg = build_message(alerts, target)
        if msg:
            post_to_slack('SLACK_WEBHOOK_URL', msg)


if __name__ == '__main__':
    main()
