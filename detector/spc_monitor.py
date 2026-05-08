import os
import sys
import json
import argparse
import requests
import re
import xml.etree.ElementTree as ET
from datetime import date, timedelta

NTA_API_KEY = os.environ.get('NTA_API_KEY', '')
NTA_BASE = 'https://api.houjin-bangou.nta.go.jp/4'


def load_target_locations():
    sys.path.insert(0, '.')
    from pe_addresses import TARGET_LOCATIONS
    return TARGET_LOCATIONS


def fetch_new_corps(start, end):
    params = {'id': NTA_API_KEY, 'from': start, 'to': end, 'type': '12', 'divide': '0'}
    res = requests.get(f'{NTA_BASE}/diff', params=params, timeout=120)
    res.raise_for_status()
    return ET.fromstring(res.content.decode('utf-8'))


def normalize_alphanumeric(text):
    """全角英数字を半角に変換"""
    return text.translate(str.maketrans(
        'ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺ'
        'ａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ'
        '０１２３４５６７８９',
        'ABCDEFGHIJKLMNOPQRSTUVWXYZ'
        'abcdefghijklmnopqrstuvwxyz'
        '0123456789'
    ))


def is_spc_name(name):
    """SPC的な名前判定。全角・半角両対応"""
    norm = normalize_alphanumeric(name).replace('．', '.').replace('・', '')
    if re.match(r'^[A-Za-z0-9.\s]{1,15}(?:株式会社|合同会社|有限会社)', norm):
        return True
    if 'ホールディング' in name or 'Holdings' in norm or 'ＨＤ' in name:
        return True
    if any(kw in norm for kw in ['投資事業', 'キャピタル', 'パートナーズ', 'Capital', 'Partners']):
        return True
    if name.startswith('合同会社'):
        return True
    return False


def match_location(corp, locations):
    pref = corp.findtext('prefectureName') or ''
    city = corp.findtext('cityName') or ''
    street = corp.findtext('streetNumber') or ''
    full_addr = pref + city + street

    for loc in locations:
        if loc['pref'] != pref or loc['city'] != city:
            continue
        if loc['building'] in street:
            return loc, full_addr
    return None, full_addr


def scan(days_back=7):
    end_date = date.today()
    start_date = end_date - timedelta(days=days_back)

    print(f'Fetching NTA new corps: {start_date} to {end_date}')
    root = fetch_new_corps(start_date.isoformat(), end_date.isoformat())

    total = int(root.findtext('count') or 0)
    print(f'Total corps in period: {total:,}')

    locations = load_target_locations()
    alerts = []

    for corp in root.findall('corporation'):
        if corp.findtext('process') != '01':
            continue
        if corp.findtext('kind') != '301':
            continue
        
        # 設立日が期間外のものを除外（既存法人の再収録）
        change_date = corp.findtext('changeDate') or ''
        try:
            cd = date.fromisoformat(change_date)
            if cd < start_date:
                continue
        except Exception:
            continue

        match, full_addr = match_location(corp, locations)
        if not match:
            continue

        name = corp.findtext('name') or ''
        alerts.append({
            'corp_num': corp.findtext('corporateNumber'),
            'name': name,
            'matched_org': match['name'],
            'category': match['category'],
            'is_suspicious_name': is_spc_name(name),
            'change_date': change_date,
            'address': full_addr,
        })

    return alerts


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--days', type=int, default=7)
    p.add_argument('--output', default='spc_alerts')
    args = p.parse_args()

    if not NTA_API_KEY:
        print('NTA_API_KEY not set', file=sys.stderr)
        sys.exit(1)

    alerts = scan(days_back=args.days)

    print(f'\n=== SPC候補: {len(alerts)}件 ===')
    for a in alerts[:30]:
        flag = '!' if a['is_suspicious_name'] else ' '
        print(f"{flag} [{a['category']}] {a['name']}")
        print(f"    住所: {a['matched_org']} / 設立: {a['change_date']}")
        print(f"    法人番号: {a['corp_num']}")

    if alerts:
        os.makedirs(os.path.dirname(args.output) or '.', exist_ok=True)
        with open(f'{args.output}.json', 'w', encoding='utf-8') as f:
            json.dump(alerts, f, ensure_ascii=False, indent=2)


if __name__ == '__main__':
    main()
