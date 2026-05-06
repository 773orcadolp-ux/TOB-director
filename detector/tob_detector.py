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
from typing import Optional

EDINET_BASE = “https://api.edinet-fsa.go.jp/api/v2”
EDINET_API_KEY = os.environ.get(“EDINET_API_KEY”, “”)

LARGE_HOLDING_CODES = {
“350”: “Large holding report”,
“351”: “Large holding report (correction)”,
“360”: “Large holding report (special)”,
“361”: “Large holding report (special, correction)”,
}

LEGAL_LIMIT_BIZ_DAYS = 5
RATE_LIMIT_SLEEP = 1.0

WAREKI_START = {
“Reiwa”: date(2019, 5, 1),
“Heisei”: date(1989, 1, 8),
“Showa”: date(1926, 12, 25),
}
WAREKI_KANJI = {
chr(20196) + chr(21644): “Reiwa”,
chr(24179) + chr(25104): “Heisei”,
chr(26157) + chr(21644): “Showa”,
}

def parse_date_string(text):
if not text:
return None
text = str(text).strip()

```
m = re.search(r'(\d{4})[-/](\d{1,2})[-/](\d{1,2})', text)
if m:
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        pass

for kanji, name in WAREKI_KANJI.items():
    pat = kanji + r'(\u5143|\d{1,2})\u5e74\s*(\d{1,2})\u6708\s*(\d{1,2})\u65e5'
    m = re.search(pat, text)
    if m:
        y = 1 if m.group(1) == chr(20803) else int(m.group(1))
        try:
            start = WAREKI_START[name]
            result = date(start.year + y - 1, int(m.group(2)), int(m.group(3)))
            if result >= start:
                return result
        except ValueError:
            pass
return None
```

def get_jp_holidays(year):
holidays = set()
for m, d in [(1, 1), (2, 11), (2, 23), (4, 29), (5, 3), (5, 4), (5, 5),
(8, 11), (11, 3), (11, 23)]:
try:
holidays.add(date(year, m, d))
except ValueError:
pass

```
def nth_monday(month, n):
    d = date(year, month, 1)
    while d.weekday() != 0:
        d += timedelta(days=1)
    return d.day + 7 * (n - 1)

for m, n in [(1, 2), (7, 3), (9, 3), (10, 2)]:
    try:
        holidays.add(date(year, m, nth_monday(m, n)))
    except ValueError:
        pass

try:
    holidays.add(date(year, 3, int(20.8431 + 0.242194 * (year - 1980) - int((year - 1980) / 4))))
    holidays.add(date(year, 9, int(23.2488 + 0.242194 * (year - 1980) - int((year - 1980) / 4))))
except ValueError:
    pass

extra = set()
for h in sorted(holidays):
    if h.weekday() == 6:
        c = h + timedelta(days=1)
        while c in holidays or c in extra:
            c += timedelta(days=1)
        extra.add(c)
return holidays | extra
```

def calc_business_days(start, end):
if end < start:
return 0
holidays = set()
for y in range(start.year, end.year + 1):
holidays |= get_jp_holidays(y)
count = 0
cur = start
while cur <= end:
if cur.weekday() < 5 and cur not in holidays:
count += 1
cur += timedelta(days=1)
return count

def fetch_documents_by_date(target_date):
url = f”{EDINET_BASE}/documents.json”
params = {“date”: target_date, “type”: 2, “Subscription-Key”: EDINET_API_KEY}
try:
res = requests.get(url, params=params, timeout=30)
res.raise_for_status()
return res.json().get(“results”, [])
except Exception as e:
print(f”  warn: {e}”, file=sys.stderr)
return []

def fetch_document_csv(doc_id):
url = f”{EDINET_BASE}/documents/{doc_id}”
params = {“type”: 5, “Subscription-Key”: EDINET_API_KEY}
try:
res = requests.get(url, params=params, timeout=60)
res.raise_for_status()
return res.content
except Exception:
return None

def parse_csv_zip(zip_bytes):
result = {
“obligation_date”: None,
“holding_ratio”: None,
“holding_ratio_solo”: None,
“holding_ratio_prev”: None,
“purpose”: None,
“filer_business”: None,
“change_reason”: None,
}
if not zip_bytes:
return result

```
try:
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        csv_names = [n for n in zf.namelist() if n.endswith(".csv") and "XBRL_TO_CSV" in n]
        for csv_name in csv_names:
            text = zf.read(csv_name).decode("utf-16", errors="ignore")
            reader = csv.reader(io.StringIO(text), delimiter="\t")
            for row in reader:
                if len(row) < 9:
                    continue
                eid = row[0]
                context = row[2]
                val = row[8].strip()
                if not val or val in ("-", chr(65293)):
                    continue

                if "DateWhenFilingRequirementAroseCoverPage" in eid:
                    d = parse_date_string(val)
                    if d:
                        result["obligation_date"] = d

                elif eid.endswith("HoldingRatioOfShareCertificatesEtc"):
                    try:
                        ratio = float(val) * 100
                        if context == "FilingDateInstant":
                            result["holding_ratio"] = ratio
                        elif "FilerLargeVolumeHolder1Member" in context:
                            result["holding_ratio_solo"] = ratio
                    except ValueError:
                        pass

                elif eid.endswith("HoldingRatioOfShareCertificatesEtcPerLastReport"):
                    if context == "FilingDateInstant":
                        try:
                            result["holding_ratio_prev"] = float(val) * 100
                        except ValueError:
                            pass

                elif "PurposeOfHolding" in eid and "FilerLargeVolumeHolder1Member" in context:
                    result["purpose"] = val[:200]

                elif "DescriptionOfBusiness" in eid and "FilerLargeVolumeHolder1Member" in context:
                    result["filer_business"] = val[:200]

                elif "ReasonForFilingChangeReport" in eid:
                    result["change_reason"] = val[:200]
except Exception:
    pass
return result
```

def score_delay(d):
if d <= 0: return 0, “NORMAL”
if d <= 5: return 1, “MINOR”
if d <= 22: return 2, “LOW”
if d <= 65: return 3, “MEDIUM”
if d <= 130: return 4, “HIGH”
return 5, “CRITICAL”

def score_ratio(r):
if r is None: return 0, “unknown”
if r >= 33.34: return 5, “33.4%+”
if r >= 30: return 4, “30%+”
if r >= 20: return 3, “20%+”
if r >= 10: return 2, “10%+”
if r >= 5: return 1, “5%+”
return 0, “<5%”

def score_purpose(p):
if not p: return 0, “none”
high = [chr(32076)+chr(21942)+chr(21442)+chr(21152), chr(37325)+chr(35201)+chr(25552)+chr(26696),
chr(25903)+chr(37197), chr(35211)+chr(25910), chr(36023)+chr(21454),
chr(21512)+chr(20341), chr(26666)+chr(24335)+chr(20132)+chr(25563),
chr(20844)+chr(38283)+chr(36023)+chr(20184), “TOB”]
for kw in high:
if kw in p: return 4, f”high: {kw}”
mid = [chr(26989)+chr(21209)+chr(25552)+chr(25658), chr(36039)+chr(26412)+chr(25552)+chr(25658)]
for kw in mid:
if kw in p: return 2, f”mid: {kw}”
normal = [chr(32020)+chr(25237)+chr(36039), chr(38263)+chr(26399)+chr(20445)+chr(26377)]
if any(kw in p for kw in normal): return 0, “normal”
return 1, “vague”

def score_holder(name, biz):
text = (name or “”) + “ “ + (biz or “”)
founding = [chr(19981)+chr(21205)+chr(29987), chr(36039)+chr(29987)+chr(31649)+chr(29702),
chr(25345)+chr(26666), chr(26377)+chr(20385)+chr(35388)+chr(21048)+chr(25237)+chr(36039)]
for kw in founding:
if kw in text: return 3, f”founding-family-like ({kw})”
fund = [chr(12501)+chr(12449)+chr(12531)+chr(12489), chr(25237)+chr(36039)+chr(38996)+chr(21839)]
for kw in fund:
if kw in text: return 1, “fund”
bizkw = [chr(35069)+chr(36896), chr(36009)+chr(22770), chr(23567)+chr(22770), chr(21830)+chr(20107)]
for kw in bizkw:
if kw in text: return 2, f”business ({kw})”
return 1, “unknown”

def analyze(doc, csv_data=None):
if doc.get(“docTypeCode”) not in LARGE_HOLDING_CODES:
return None
submit_str = doc.get(“submitDateTime”, “”)
if not submit_str:
return None
try:
submit_date = datetime.strptime(submit_str[:10], “%Y-%m-%d”).date()
except ValueError:
return None

```
obligation = None
if csv_data and csv_data.get("obligation_date"):
    obligation = csv_data["obligation_date"]
elif doc.get("periodEnd"):
    obligation = parse_date_string(doc["periodEnd"])
if not obligation:
    return None

biz_late = max(0, calc_business_days(obligation, submit_date) - LEGAL_LIMIT_BIZ_DAYS)
cal_late = (submit_date - obligation).days
delay_s, delay_l = score_delay(biz_late)

ratio = csv_data.get("holding_ratio") if csv_data else None
purpose = csv_data.get("purpose") if csv_data else None
biz = csv_data.get("filer_business") if csv_data else None
rs, rl = score_ratio(ratio)
ps, pn = score_purpose(purpose)
hs, ht = score_holder(doc.get("filerName"), biz)

total = delay_s + rs + ps + hs
level = "CRITICAL" if total >= 10 else "HIGH" if total >= 7 else "MEDIUM" if total >= 4 else "LOW"

return {
    "doc_id": doc.get("docID"),
    "submit_date": submit_str[:10],
    "obligation_date": obligation.isoformat(),
    "filer_name": doc.get("filerName", ""),
    "issuer_edinet_code": doc.get("issuerEdinetCode") or "",
    "calendar_days_late": cal_late,
    "biz_days_late": biz_late,
    "delay_level": delay_l,
    "holding_ratio": ratio,
    "ratio_label": rl,
    "purpose": purpose,
    "purpose_note": pn,
    "filer_business": biz,
    "holder_type": ht,
    "change_reason": csv_data.get("change_reason") if csv_data else None,
    "total_score": total,
    "risk_level": level,
}
```

def scan_date(target_date, threshold=4, filter_issuer=None):
print(f”scan: {target_date}”)
docs = fetch_documents_by_date(target_date)
print(f”  total: {len(docs)}”)
time.sleep(RATE_LIMIT_SLEEP)

```
targets = [d for d in docs if d.get("docTypeCode") in LARGE_HOLDING_CODES]
if filter_issuer:
    targets = [d for d in targets if filter_issuer in (d.get("issuerEdinetCode") or "")]
print(f"  large holding reports: {len(targets)}")

alerts = []
for i, doc in enumerate(targets, 1):
    print(f"  [{i}/{len(targets)}] {doc.get('filerName','')[:25]}", end=" ")
    csv_bytes = fetch_document_csv(doc["docID"])
    time.sleep(RATE_LIMIT_SLEEP)
    csv_data = parse_csv_zip(csv_bytes) if csv_bytes else None
    r = analyze(doc, csv_data=csv_data)
    if r:
        print(f"-> {r['total_score']} ({r['risk_level']})")
        if r["total_score"] >= threshold:
            alerts.append(r)
    else:
        print("(failed)")
return alerts
```

def scan_range(start, end, threshold=4, filter_issuer=None):
cur = datetime.strptime(start, “%Y-%m-%d”).date()
last = datetime.strptime(end, “%Y-%m-%d”).date()
all_alerts = []
while cur <= last:
if cur.weekday() < 5:
alerts = scan_date(cur.isoformat(), threshold, filter_issuer)
all_alerts.extend(alerts)
cur += timedelta(days=1)
return all_alerts

def print_alerts(alerts):
print(f”\n{’=’*60}”)
print(f”  detected: {len(alerts)}”)
print(f”{’=’*60}”)
for i, a in enumerate(sorted(alerts, key=lambda x: -x[“total_score”])[:10], 1):
print(f”\n[{i}] {a[‘risk_level’]} score:{a[‘total_score’]}”)
print(f”   filer: {a[‘filer_name’]}”)
print(f”   issuer EDINET: {a[‘issuer_edinet_code’]}”)
print(f”   obligation: {a[‘obligation_date’]} -> submit: {a[‘submit_date’]}”)
print(f”   delay: {a[‘biz_days_late’]} biz days ({a[‘delay_level’]})”)
if a.get(“holding_ratio”) is not None:
print(f”   ratio: {a[‘holding_ratio’]}% ({a[‘ratio_label’]})”)
print(f”   purpose: {a[‘purpose_note’]} / type: {a[‘holder_type’]}”)

def save_alerts(alerts, prefix=“tob_alerts”):
if not alerts:
return
os.makedirs(os.path.dirname(prefix) or “.”, exist_ok=True)
with open(f”{prefix}.json”, “w”, encoding=“utf-8”) as f:
json.dump(alerts, f, ensure_ascii=False, indent=2, default=str)
fields = [“submit_date”, “obligation_date”, “filer_name”, “issuer_edinet_code”,
“calendar_days_late”, “biz_days_late”, “delay_level”,
“holding_ratio”, “ratio_label”, “purpose_note”, “holder_type”,
“total_score”, “risk_level”]
with open(f”{prefix}.csv”, “w”, encoding=“utf-8-sig”, newline=””) as f:
w = csv.DictWriter(f, fieldnames=fields, extrasaction=“ignore”)
w.writeheader()
w.writerows(alerts)

def main():
p = argparse.ArgumentParser()
p.add_argument(”–date”)
p.add_argument(”–range”, nargs=2)
p.add_argument(”–issuer”)
p.add_argument(”–threshold”, type=int, default=4)
p.add_argument(”–output”, default=“tob_alerts”)
args = p.parse_args()

```
if not EDINET_API_KEY:
    print("EDINET_API_KEY not set", file=sys.stderr)
    sys.exit(1)

if args.date:
    alerts = scan_date(args.date, args.threshold, args.issuer)
elif args.range:
    alerts = scan_range(args.range[0], args.range[1], args.threshold, args.issuer)
else:
    p.print_help()
    sys.exit(0)

print_alerts(alerts)
if alerts:
    save_alerts(alerts, args.output)
```

if **name** == “**main**”:
main()