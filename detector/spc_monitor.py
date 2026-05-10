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

sys.path.insert(0, 'scripts')
from slack_notifier import post_to_slack


def load_target_locations():
    sys.path.insert(0, '.')
    from pe_addresses import TARGET_LOCATIONS
    return TARGET_LOCATIONS


def load_spc_patterns():
    sys.path.insert(0, '.')
    from pe_spc_patterns import match_spc_name
    return match_spc_name


def fetch_new_corps(start, end):
    params = {'id': NTA_API_KEY, 'from': start, 'to': end, 'type': '12', 'divide': '0'}
    res = requests.get(f'{NTA_BASE}/diff', params=params, timeout=120)
    res.raise_for_status()
    return ET.fromstring(res.content.decode('utf-8'))


def normalize_alphanumeric(text):
    return text.translate(str.maketrans(
        'ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺ'
        'ａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ'
        '０１２３４５６７８９',
        'ABCDEFGHIJKLMNOPQRSTUVWXYZ'
        'abcdefghijklmnopqrstuvwxyz'
        '0123456789'
    ))


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
    pattern_matcher = load_spc_patterns()
    alerts = []

    for corp in root.findall('corporation'):
        if corp.findtext('process') != '01':
            continue
        if corp.findtext('kind') != '301':
            continue

        change_date = corp.findtext('changeDate') or ''
        try:
            cd = date.fromisoformat(change_date)
            if cd < start_date:
                continue
        except Exception:
            continue

        name = corp.findtext('name') or ''
        norm_name = normalize_alphanumeric(name)

        spc_pattern = pattern_matcher(norm_name)
        addr_match, full_addr = match_location(corp, locations)

        if not spc_pattern and not addr_match:
            continue

        alerts.append({
            'corp_num': corp.findtext('corporateNumber'),
            'name': name,
            'matched_org': addr_match['name'] if addr_match else (spc_pattern['fund'] if spc_pattern else '?'),
            'category': addr_match['category'] if addr_match else 'PE',
            'pattern_match': spc_pattern['fund'] if spc_pattern else None,
            'change_date': change_date,
            'address': full_addr,
        })

    return alerts


def build_message(alerts):
    if not alerts:
        return None
    lines = [f'*【ハコ通知】SPC設立検知 ({len(alerts)}件)*', '']
    for i, a in enumerate(alerts[:20], 1):
        flag = ':red_circle:' if a.get('pattern_match') else ':large_orange_circle:'
        lines.append(f"{flag} *[{i}] {a['name']}*")
        if a.get('pattern_match'):
            lines.append(f"    命名パターン一致: {a['pattern_match']}")
        lines.append(f"    住所マッチ: {a['matched_org']} ({a['category']})")
        lines.append(f"    設立: {a['change_date']} / 法人番号: {a['corp_num']}")
        lines.append('')
    return '\n'.join(lines)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--days', type=int, default=1)
    p.add_argument('--output', default='spc_alerts')
    args = p.parse_args()

    if not NTA_API_KEY:
        print('NTA_API_KEY not set', file=sys.stderr)
        sys.exit(1)

    alerts = scan(days_back=args.days)
    print(f'\nDetected: {len(alerts)}')

    if alerts:
        os.makedirs(os.path.dirname(args.output) or '.', exist_ok=True)
        with open(f'{args.output}.json', 'w', encoding='utf-8') as f:
            json.dump(alerts, f, ensure_ascii=False, indent=2)

        msg = build_message(alerts)
        if msg:
            post_to_slack('SLACK_WEBHOOK_BOX', msg)


if __name__ == '__main__':
    main()
