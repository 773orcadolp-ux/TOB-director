

import os
import requests
import json
import csv
import re
import argparse
import sys
import zipfile
import io
from datetime import datetime, timedelta, date
from typing import Optional

# EDINET APIキー（環境変数 or GitHub Secrets から取得）
EDINET_API_KEY = os.environ.get("EDINET_API_KEY", "")

# ============================================================
# 日本の祝日計算（外部ライブラリ不要・内製）
# ============================================================

def get_jp_holidays(year: int) -> set:
    
    holidays = set()

    def add(m, d):
        try:
            holidays.add(date(year, m, d))
        except ValueError:
            pass

    # 固定祝日
    add(1, 1)   # 元日
    add(2, 11)  # 建国記念の日
    add(2, 23)  # 天皇誕生日
    add(4, 29)  # 昭和の日
    add(5, 3)   # 憲法記念日
    add(5, 4)   # みどりの日
    add(5, 5)   # こどもの日
    add(8, 11)  # 山の日
    add(11, 3)  # 文化の日
    add(11, 23) # 勤労感謝の日

    # 移動祝日（ハッピーマンデー等）
    # 成人の日: 1月第2月曜
    add(1, _nth_weekday(year, 1, 0, 2))
    # 海の日: 7月第3月曜
    add(7, _nth_weekday(year, 7, 0, 3))
    # 敬老の日: 9月第3月曜
    add(9, _nth_weekday(year, 9, 0, 3))
    # スポーツの日: 10月第2月曜
    add(10, _nth_weekday(year, 10, 0, 2))

    # 春分・秋分（簡易計算）
    shunbun = _shunbun(year)
    add(3, shunbun)
    add(9, _shubun(year))

    # 振替休日
    extra = set()
    for h in sorted(holidays):
        if h.weekday() == 6:  # 日曜
            candidate = h + timedelta(days=1)
            while candidate in holidays or candidate in extra:
                candidate += timedelta(days=1)
            extra.add(candidate)

    # 国民の休日（祝日に挟まれた平日）
    all_h = holidays | extra
    for h in sorted(all_h):
        prev_day = h - timedelta(days=2)
        next_day = h
        mid = h - timedelta(days=1)
        if prev_day in all_h and mid.weekday() not in (5, 6) and mid not in all_h:
            extra.add(mid)

    return holidays | extra

def _nth_weekday(year: int, month: int, weekday: int, n: int) -> int:
    
    d = date(year, month, 1)
    count = 0
    while True:
        if d.weekday() == weekday:
            count += 1
            if count == n:
                return d.day
        d += timedelta(days=1)

def _shunbun(year: int) -> int:
    
    if year <= 1979: return int(20.8357 + 0.242194 * (year - 1980) - int((year - 1980) / 4))
    if year <= 2099: return int(20.8431 + 0.242194 * (year - 1980) - int((year - 1980) / 4))
    return 21

def _shubun(year: int) -> int:
    
    if year <= 1979: return int(23.2588 + 0.242194 * (year - 1980) - int((year - 1980) / 4))
    if year <= 2099: return int(23.2488 + 0.242194 * (year - 1980) - int((year - 1980) / 4))
    return 23

def calc_business_days(start: date, end: date) -> int:
    
    if end < start:
        return 0

    holidays = set()
    for y in range(start.year, end.year + 1):
        holidays |= get_jp_holidays(y)

    count = 0
    current = start
    while current <= end:
        if current.weekday() < 5 and current not in holidays:
            count += 1
        current += timedelta(days=1)
    return count

# ============================================================
# スコアリングロジック
# ============================================================

LEGAL_LIMIT_BIZ_DAYS = 5  # 法定提出期限（営業日）

def score_delay(biz_days_late: int) -> tuple[int, str]:
    
    if biz_days_late <= 0:
        return 0, "NORMAL"
    elif biz_days_late <= 5:
        return 1, "MINOR"       # 軽微（1週間以内）
    elif biz_days_late <= 22:
        return 2, "LOW"         # 1ヶ月以内
    elif biz_days_late <= 65:
        return 3, "MEDIUM"      # 3ヶ月以内
    elif biz_days_late <= 130:
    	return 4, "HIGH"        # 6ヶ月以内
    else:
        return 5, "CRITICAL"    # 6ヶ月超（Olympic事例相当）

def score_holding_ratio(ratio: float) -> tuple[int, str]:
    
    if ratio >= 33.34:
        return 5, "特別決議阻止ライン超（33.4%+）"
    elif ratio >= 30.0:
        return 4, "経営支配力（30%+）"
    elif ratio >= 20.0:
        return 3, "持分法適用ライン（20%+）"
    elif ratio >= 10.0:
        return 2, "大株主ライン（10%+）"
    elif ratio >= 5.0:
        return 1, "大量保有（5%+）"
    else:
        return 0, "5%未満"

def score_purpose(purpose_text: str) -> tuple[int, str]:
    
    if not purpose_text:
        return 0, "記載なし"

    t = purpose_text.lower()

    # 高リスクキーワード
    high_risk = ["経営参加", "重要提案", "支配", "議決権行使", "経営権",
                 "買収", "合併", "株式交換", "TOB", "公開買付"]
    for kw in high_risk:
        if kw in purpose_text:
            return 4, f"高リスクキーワード検出: 「{kw}」"

    # 中リスクキーワード
    mid_risk = ["業務提携", "資本提携", "協議", "シナジー", "連携"]
    for kw in mid_risk:
        if kw in purpose_text:
            return 2, f"中リスクキーワード検出: 「{kw}」"

    # 低リスク（通常の表現）
    if any(kw in purpose_text for kw in ["純投資", "長期保有", "資産運用"]):
        return 0, "通常の保有目的"

    return 1, "目的が曖昧（要精査）"

def score_holder_type(holder_info: dict) -> tuple[int, str]:
    
    biz = holder_info.get("business", "")
    name = holder_info.get("name", "")

    # 創業家系の特徴的なキーワード
    founding_keywords = ["不動産", "資産管理", "投資管理", "持株", "ホールディング",
                         "財産管理", "有価証券投資"]
    for kw in founding_keywords:
        if kw in biz:
            return 3, f"創業家系法人の可能性（{kw}）"

    # 事業会社（同業・異業種）
    business_keywords = ["製造", "販売", "小売", "卸売", "商事", "産業"]
    for kw in business_keywords:
        if kw in biz:
            return 2, f"事業会社（{kw}）"

    # 投資ファンド系
    fund_keywords = ["ファンド", "投資顧問", "アセット", "キャピタル", "パートナーズ"]
    for kw in fund_keywords + [kw.lower() for kw in fund_keywords]:
        if kw in name.lower() or kw in biz:
            return 1, "投資ファンド系"

    return 1, "属性不明（要確認）"

def classify_total_score(score: int) -> dict:
    
    if score >= 10:
        return {"level": "CRITICAL", "label": "🔴 即時精査推奨",
                "comment": "TOB/MBO/株式交換の強い予兆。複数シグナルが重複。"}
    elif score >= 7:
        return {"level": "HIGH", "label": "🟠 要注意",
                "comment": "水面下での交渉進行の可能性。業績・適時開示と突合を。"}
    elif score >= 4:
        return {"level": "MEDIUM", "label": "🟡 継続監視",
                "comment": "単独では弱いが、他シグナルと組み合わせて追跡。"}
    else:
        return {"level": "LOW", "label": "⬜ 通常",
                "comment": "現時点では特段のシグナルなし。"}

# ============================================================
# 和暦 → 西暦 変換
# ============================================================

# 各元号の開始日（西暦）
WAREKI_ERAS = {
    "令和": date(2019, 5, 1),
    "平成": date(1989, 1, 8),
    "昭和": date(1926, 12, 25),
    "大正": date(1912, 7, 30),
    "明治": date(1868, 1, 25),
    # 略称・旧字体も対応
    "R":  date(2019, 5, 1),
    "H":  date(1989, 1, 8),
    "S":  date(1926, 12, 25),
    "T":  date(1912, 7, 30),
    "M":  date(1868, 1, 25),
}

def wareki_to_seireki(era: str, year: int, month: int, day: int) -> Optional[date]:
    
    era_start = WAREKI_ERAS.get(era)
    if era_start is None:
        return None

    western_year = era_start.year + year - 1

    # 元号が変わった年（例: 令和元年=2019年）の月日検証
    # 令和元年5月1日より前はまだ平成なのでエラー
    try:
        result = date(western_year, month, day)
        if result < era_start:
            return None
        return result
    except ValueError:
        return None

def parse_date_string(text: str) -> Optional[date]:
    
    if not text:
        return None

    text = text.strip()

    # ① ISO形式 YYYY-MM-DD
    m = re.search(r'(\d{4})-(\d{2})-(\d{2})', text)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass

    # ② スラッシュ形式 YYYY/MM/DD
    m = re.search(r'(\d{4})/(\d{1,2})/(\d{1,2})', text)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass

    # ③ 8桁数字 YYYYMMDD
    m = re.fullmatch(r'(\d{4})(\d{2})(\d{2})', text)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass

    # ④ 和暦（漢字）: 令和7年2月28日 / 令和元年5月1日
    m = re.search(
        r'(令和|平成|昭和|大正|明治)(元|\d{1,2})年\s*(\d{1,2})月\s*(\d{1,2})日',
        text
    )
    if m:
        era = m.group(1)
        year_str = m.group(2)
        year = 1 if year_str == "元" else int(year_str)
        month = int(m.group(3))
        day = int(m.group(4))
        result = wareki_to_seireki(era, year, month, day)
        if result:
            return result

    # ⑤ 和暦（アルファベット略称）: R7.2.28 / H30.12.1
    m = re.search(r'([RHSTMrhstm])(\d{1,2})[.\-/](\d{1,2})[.\-/](\d{1,2})', text)
    if m:
        era = m.group(1).upper()
        year = int(m.group(2))
        month = int(m.group(3))
        day = int(m.group(4))
        result = wareki_to_seireki(era, year, month, day)
        if result:
            return result

    return None

def test_date_parser():
    
    cases = [
        # (入力文字列, 期待する date)
        ("令和7年2月28日",        date(2025, 2, 28)),
        ("令和元年5月1日",         date(2019, 5, 1)),
        ("平成30年12月31日",       date(2018, 12, 31)),
        ("昭和64年1月7日",        date(1989, 1, 7)),
        ("R7.2.28",               date(2025, 2, 28)),
        ("H30.12.1",              date(2018, 12, 1)),
        ("2025-02-28",            date(2025, 2, 28)),
        ("2025/02/28",            date(2025, 2, 28)),
        ("20250228",              date(2025, 2, 28)),
        # 実際のEDINET表紙テキストを想定
        ("【報告義務発生日】 令和7年2月28日", date(2025, 2, 28)),
        ("報告義務発生日　令和8 1 23",       None),   # スペース区切り（不完全）→ None
    ]

    print("\n" + "=" * 60)
    print("  日付パーサー 単体テスト")
    print("=" * 60)

    passed = 0
    for text, expected in cases:
        result = parse_date_string(text)
        ok = result == expected
        icon = "✅" if ok else "❌"
        print(f"  {icon} 入力: {text!r:40s} → {result} (期待: {expected})")
        if ok:
            passed += 1

    print(f"\n  結果: {passed}/{len(cases)} テスト通過")
    return passed == len(cases)

# ============================================================
# EDINET API クライアント
# ============================================================

EDINET_BASE = "https://disclosure.edinet-api.go.jp/api/v2"

# 変更報告書の書類種別コード
CHANGE_REPORT_CODES = {
    "30": "変更報告書",
    "31": "変更報告書（特例）",
    "38": "訂正報告書（変更報告書）",
}

def fetch_documents_by_date(target_date: str) -> list:
    
    url = f"{EDINET_BASE}/documents.json"
    params = {"date": target_date, "type": 2}
    if EDINET_API_KEY:
        params["Subscription-Key"] = EDINET_API_KEY

    try:
        res = requests.get(url, params=params, timeout=15)
        res.raise_for_status()
        data = res.json()
        return data.get("results", [])
    except requests.exceptions.ConnectionError:
        print("  ⚠️  EDINET APIに接続できません。ネットワーク設定を確認してください。")
        return []
    except Exception as e:
        print(f"  ⚠️  API取得エラー ({target_date}): {e}")
        return []

def fetch_xbrl_zip(doc_id: str) -> Optional[bytes]:
    
    url = f"{EDINET_BASE}/documents/{doc_id}"
    params = {"type": 1}
    if EDINET_API_KEY:
        params["Subscription-Key"] = EDINET_API_KEY
    try:
        res = requests.get(url, params=params, timeout=30)
        res.raise_for_status()
        return res.content
    except Exception:
        return None

def extract_obligation_date_from_zip(zip_bytes: bytes) -> Optional[date]:
    
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            names = zf.namelist()

            # ── 戦略1: XBRLファイル ──────────────────────────────
            xbrl_files = [n for n in names if n.endswith(".xbrl")]
            for xbrl_name in xbrl_files:
                text = zf.read(xbrl_name).decode("utf-8", errors="ignore")

                # EDINETの大量保有報告書XBRLタグ（複数パターン存在）
                xbrl_patterns = [
                    # 標準タグ
                    r'ReportingObligationOccurrenceDateOfMajorShareholder'
                    r'Cover[^>]*>\s*([^<]+)',
                    # 短縮タグ
                    r'reportingObligationOccurrenceDate[^>]*>\s*([^<]+)',
                    # 汎用
                    r'ObligationOccurrenceDate[^>]*>\s*([^<]+)',
                ]
                for pat in xbrl_patterns:
                    m = re.search(pat, text, re.IGNORECASE)
                    if m:
                        d = parse_date_string(m.group(1).strip())
                        if d:
                            return d

            # ── 戦略2: 表紙HTMLファイル ───────────────────────────
            # EDINETの表紙HTMLは "PublicDoc/0000xxx-xxxxxxxxxx-ind.htm" 形式
            htm_files = [
                n for n in names
                if (n.endswith(".htm") or n.endswith(".html"))
                and "PublicDoc" in n
            ]
            for htm_name in htm_files:
                try:
                    text = zf.read(htm_name).decode("utf-8", errors="ignore")
                except Exception:
                    try:
                        text = zf.read(htm_name).decode("shift_jis", errors="ignore")
                    except Exception:
                        continue

                # 「報告義務発生日」の直後の日付を探す
                # 表紙テーブルの典型パターン:
                #   <td>報告義務発生日</td><td>令和7年2月28日</td>
                obligation_patterns = [
                    r'報告義務発生日[^<]*</[^>]+>\s*<[^>]+>\s*([^<]{5,30})',
                    r'報告義務発生日\s*[:：]\s*([^\n<]{5,30})',
                    r'報告義務発生日\s+(令和|平成|昭和|大正|明治|\d{4})[\s\S]{0,20}?'
                    r'(\d{1,2})年\s*(\d{1,2})月\s*(\d{1,2})日',
                ]
                for pat in obligation_patterns:
                    m = re.search(pat, text)
                    if m:
                        # グループ1が元号で始まる場合と日付文字列の場合で分岐
                        candidate = m.group(1).strip() if m.lastindex == 1 else m.group(0)
                        d = parse_date_string(candidate)
                        if d:
                            return d

            # ── 戦略3: 全ファイルのフレーズ検索（フォールバック）──
            for fname in names:
                if any(fname.endswith(ext) for ext in [".xbrl", ".htm", ".html", ".txt", ".csv"]):
                    try:
                        text = zf.read(fname).decode("utf-8", errors="ignore")
                    except Exception:
                        continue

                    # 「報告義務発生日」の前後200文字を抽出して日付を探す
                    idx = text.find("報告義務発生日")
                    if idx >= 0:
                        window = text[idx:idx + 200]
                        # 和暦・西暦どちらでも対応
                        date_patterns = [
                            r'(令和|平成|昭和|大正|明治)(元|\d{1,2})年\s*(\d{1,2})月\s*(\d{1,2})日',
                            r'(\d{4})[-/](\d{2})[-/](\d{2})',
                            r'([RHSTMrhstm])(\d{1,2})[.\-/](\d{1,2})[.\-/](\d{1,2})',
                        ]
                        for dp in date_patterns:
                            dm = re.search(dp, window)
                            if dm:
                                d = parse_date_string(dm.group(0))
                                if d:
                                    return d

    except zipfile.BadZipFile:
        pass
    except Exception:
        pass

    return None

def get_obligation_date_precise(doc_id: str) -> tuple[Optional[date], str]:
    
    zip_bytes = fetch_xbrl_zip(doc_id)
    if not zip_bytes:
        return None, "failed"

    result = extract_obligation_date_from_zip(zip_bytes)
    if result:
        return result, "xml_precise"

    return None, "failed"

# ============================================================
# メイン検出ロジック（2段階パイプライン）
# ============================================================

def analyze_document(doc: dict, precise: bool = False) -> Optional[dict]:
    
    doc_type = doc.get("docTypeCode", "")
    if doc_type not in CHANGE_REPORT_CODES:
        return None

    submit_str = doc.get("submitDateTime", "")
    if not submit_str:
        return None

    # 提出日
    try:
        submit_date = datetime.strptime(submit_str[:10], "%Y-%m-%d").date()
    except ValueError:
        return None

    # ── 義務発生日の取得 ──────────────────────────────────────
    date_source = "periodEnd_approx"
    obligation_date = None

    if precise:
        # 2次精査: ZIPから正確に取得
        doc_id = doc.get("docID", "")
        if doc_id:
            obligation_date, date_source = get_obligation_date_precise(doc_id)

    if obligation_date is None:
        # フォールバック: periodEndを近似値として使用
        period_end_str = doc.get("periodEnd", "")
        if period_end_str:
            obligation_date = parse_date_string(period_end_str)
            date_source = "periodEnd_approx"

    if obligation_date is None:
        return None

    # ── 遅延計算 ─────────────────────────────────────────────
    biz_days_elapsed = calc_business_days(obligation_date, submit_date)
    biz_days_late = max(0, biz_days_elapsed - LEGAL_LIMIT_BIZ_DAYS)
    calendar_days_late = (submit_date - obligation_date).days

    # 1次スクリーニングは暦日30日超を候補とする（偽陽性を許容）
    if not precise and calendar_days_late < 10:
        return None

    delay_score, delay_level = score_delay(biz_days_late)

    # 遅延なしは1次では通過させない
    if delay_score == 0 and not precise:
        return None

    # ── 各シグナルスコア ──────────────────────────────────────
    holding_ratio = float(doc.get("_holding_ratio", 0) or 0)
    ratio_score, ratio_label = score_holding_ratio(holding_ratio)

    purpose_text = doc.get("_purpose", "")
    purpose_score, purpose_note = score_purpose(purpose_text)

    holder_info = {
        "name": doc.get("filerName", ""),
        "business": doc.get("_business", ""),
    }
    holder_score, holder_type = score_holder_type(holder_info)

    has_correction = doc.get("_has_correction", False)
    correction_score = 2 if has_correction else 0

    total_score = delay_score + ratio_score + purpose_score + holder_score + correction_score
    risk = classify_total_score(total_score)

    sec_code = doc.get("secCode", "")
    if sec_code.endswith("0") and len(sec_code) == 5:
        sec_code = sec_code[:-1]  # 末尾0を除去（表示用）

    return {
        "doc_id": doc.get("docID", ""),
        "submit_date": submit_str[:10],
        "obligation_date": obligation_date.isoformat(),
        "date_source": date_source,
        "doc_type": CHANGE_REPORT_CODES.get(doc_type, doc_type),
        "filer_name": doc.get("filerName", ""),
        "sec_code": sec_code,

        "calendar_days_late": calendar_days_late,
        "biz_days_late": biz_days_late,
        "delay_level": delay_level,
        "delay_score": delay_score,

        "ratio_score": ratio_score,
        "ratio_label": ratio_label,
        "purpose_score": purpose_score,
        "purpose_note": purpose_note,
        "holder_score": holder_score,
        "holder_type": holder_type,
        "correction_score": correction_score,

        "total_score": total_score,
        "risk_level": risk["level"],
        "risk_label": risk["label"],
        "risk_comment": risk["comment"],

        "edinet_url": (
            "https://disclosure.edinet-api.go.jp/E01EW/BLMainController.jsp"
            f"?uji.verb=W1E62071CXW1E62071DSP&uji.bean=ee.bean.parent.EEParentBean"
            f"&TID=W1E62071&PID=W1E62071&SESSIONKEY=&lgKbn=2&dflg=0&iflg=0"
            f"&dispKbn=1&docID={doc.get('docID', '')}"
        ),
    }

def scan_date_range(start_date: str, end_date: str,
                    filter_sec_code: Optional[str] = None,
                    precise_threshold: int = 10) -> list:
    
    stage1_candidates = []
    current = datetime.strptime(start_date, "%Y-%m-%d").date()
    end = datetime.strptime(end_date, "%Y-%m-%d").date()
    scanned = 0

    print(f"\n{'─'*60}")
    print(f"  Stage 1: APIスクリーニング  {start_date} → {end_date}")
    print(f"  対象銘柄: {filter_sec_code or '全銘柄'}")
    print(f"{'─'*60}")

    while current <= end:
        if current.weekday() < 5:
            docs = fetch_documents_by_date(current.isoformat())
            for doc in docs:
                if filter_sec_code and filter_sec_code not in doc.get("secCode", ""):
                    continue
                result = analyze_document(doc, precise=False)
                if result:
                    stage1_candidates.append((doc, result))
                    print(f"  📋 候補: {result['submit_date']} | "
                          f"{result['filer_name'][:18]:18s} | "
                          f"暦日{result['calendar_days_late']:4d}日遅延")
            scanned += 1
            if scanned % 20 == 0:
                print(f"  ... {current} 処理完了")
        current += timedelta(days=1)

    print(f"\n  Stage 1 完了: {len(stage1_candidates)}件 候補")

    if not stage1_candidates:
        return []

    print(f"\n{'─'*60}")
    print(f"  Stage 2: XML精査 ({len(stage1_candidates)}件)")
    print(f"{'─'*60}")

    final_alerts = []
    for doc, stage1_result in stage1_candidates:
        doc_id = doc.get("docID", "")
        print(f"  🔍 精査中: {doc_id} ({doc.get('filerName', '')[:20]})")

        result = analyze_document(doc, precise=True)

        if result is None:
            result = stage1_result
            result["date_source"] = "periodEnd_approx(xml_failed)"

        final_alerts.append(result)
        level = result["risk_level"]
        icon = "🔴" if level == "CRITICAL" else "🟠" if level == "HIGH" else "🟡"
        src = "✓XML" if "xml" in result.get("date_source", "") else "～近似"
        print(f"  {icon} {src} | Score:{result['total_score']:2d} | "
              f"遅延{result['biz_days_late']}営業日 | {level}")

    return final_alerts

# ============================================================
# デモモード（ネットワーク不要）
# ============================================================

def run_demo():
    
    print("\n" + "=" * 60)
    print("  TOB予兆検出システム — デモモード")
    print("  (Olympic事例を含む架空データで動作確認)")
    print("=" * 60)

    demo_cases = [
        {
            # Olympic事例（実際のケース）
            "docID": "S100XXXX",
            "docTypeCode": "30",
            "submitDateTime": "2026-01-23T00:00:00",
            "periodEnd": "2025-02-28",
            "filerName": "株式会社カネヨシ",
            "secCode": "82890",
            "_holding_ratio": 31.45,
            "_purpose": "長期保有のため",
            "_business": "不動産賃貸・管理、有価証券投資",
            "_has_correction": True,  # 訂正報告書あり
        },
        {
            # 比較ケース1: 軽微遅延・低スコア
            "docID": "S100YYYY",
            "docTypeCode": "30",
            "submitDateTime": "2025-06-20T00:00:00",
            "periodEnd": "2025-06-01",
            "filerName": "△△投資顧問株式会社",
            "secCode": "99990",
            "_holding_ratio": 7.5,
            "_purpose": "純投資",
            "_business": "投資顧問業",
            "_has_correction": False,
        },
        {
            # 比較ケース2: 中程度遅延・保有目的に変化
            "docID": "S100ZZZZ",
            "docTypeCode": "30",
            "submitDateTime": "2025-10-15T00:00:00",
            "periodEnd": "2025-07-01",
            "filerName": "○○産業株式会社",
            "secCode": "55550",
            "_holding_ratio": 22.3,
            "_purpose": "業務提携を検討するため",
            "_business": "食品製造販売",
            "_has_correction": False,
        },
        {
            # 比較ケース3: 保有目的に高リスクワード
            "docID": "S100WWWW",
            "docTypeCode": "30",
            "submitDateTime": "2025-09-30T00:00:00",
            "periodEnd": "2025-08-15",
            "filerName": "□□ホールディングス",
            "secCode": "33330",
            "_holding_ratio": 15.8,
            "_purpose": "経営参加を目的とした株式取得",
            "_business": "資産管理・有価証券投資",
            "_has_correction": False,
        },
    ]

    results = []
    for doc in demo_cases:
        r = analyze_document(doc)
        if r:
            results.append(r)

    print_results(results)
    return results

# ============================================================
# 出力
# ============================================================

def print_results(alerts: list):
    

    print(f"\n{'=' * 60}")
    print(f"  検出結果サマリー: {len(alerts)} 件のアラート")
    print(f"{'=' * 60}")

    # リスクレベル別集計
    from collections import Counter
    level_count = Counter(a["risk_level"] for a in alerts)
    print(f"\n  🔴 CRITICAL : {level_count.get('CRITICAL', 0):3d} 件")
    print(f"  🟠 HIGH     : {level_count.get('HIGH', 0):3d} 件")
    print(f"  🟡 MEDIUM   : {level_count.get('MEDIUM', 0):3d} 件")
    print(f"  ⬜ LOW      : {level_count.get('LOW', 0):3d} 件")

    # スコア上位を詳細表示
    top = sorted(alerts, key=lambda x: x["total_score"], reverse=True)[:10]

    print(f"\n{'─' * 60}")
    print("  スコア上位 (最大10件)")
    print(f"{'─' * 60}")

    for i, a in enumerate(top, 1):
        print(f"\n  [{i}] {a['risk_label']}")
        print(f"      提出者    : {a['filer_name']}")
        print(f"      提出日    : {a['submit_date']}")
        print(f"      義務発生日: {a['obligation_date']}")
        print(f"      遅延      : {a['calendar_days_late']}暦日 / {a['biz_days_late']}営業日 [{a['delay_level']}]")
        print(f"      保有割合  : {a['ratio_label']}")
        print(f"      保有目的  : {a['purpose_note']}")
        print(f"      提出者属性: {a['holder_type']}")
        print(f"      訂正あり  : {'はい' if a['correction_score'] > 0 else 'いいえ'}")
        print(f"      ─────────────────────────────")
        print(f"      総合スコア: {a['total_score']}点 → {a['risk_comment']}")
        if a.get("edinet_url"):
            print(f"      EDINET    : {a['edinet_url'][:70]}...")

def save_results(alerts: list, prefix: str = "tob_alerts"):
    

    # JSON
    json_path = f"{prefix}.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(alerts, f, ensure_ascii=False, indent=2)
    print(f"\n  💾 JSON保存: {json_path}")

    # CSV
    csv_path = f"{prefix}.csv"
    if alerts:
        fieldnames = [
            "submit_date", "obligation_date", "calendar_days_late", "biz_days_late",
            "delay_level", "filer_name", "sec_code",
            "delay_score", "ratio_score", "purpose_score", "holder_score", "correction_score",
            "total_score", "risk_level", "risk_label",
            "ratio_label", "purpose_note", "holder_type", "risk_comment", "edinet_url"
        ]
        with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(alerts)
    print(f"  💾 CSV保存: {csv_path}")

# ============================================================
# CLI エントリーポイント
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="TOB/MBO予兆検出 — EDINET大量保有報告書遅延検出システム"
    )
    parser.add_argument("--date", help="特定日を検査 (YYYY-MM-DD)")
    parser.add_argument("--range", nargs=2, metavar=("START", "END"),
                        help="期間を指定してスキャン (YYYY-MM-DD YYYY-MM-DD)")
    parser.add_argument("--code", help="対象銘柄コードで絞り込み (例: 8289)")
    parser.add_argument("--demo", action="store_true",
                        help="デモデータで動作確認（ネットワーク不要）")
    parser.add_argument("--output", default="tob_alerts",
                        help="出力ファイル名プレフィックス (default: tob_alerts)")
    parser.add_argument("--parse-test", action="store_true",
                        help="日付パーサーの単体テストを実行")
    parser.add_argument("--threshold", type=int, default=4,
                        help="出力するスコアの最低値 (default: 4)")

    args = parser.parse_args()

    print("\n" + "█" * 60)
    print("  TOB / MBO 予兆検出システム v2.0")
    print("  EDINET 大量保有報告書 遅延・異常検出")
    print("█" * 60)

    alerts = []

    if args.parse_test:
        test_date_parser()
        sys.exit(0)

    elif args.demo:
        alerts = run_demo()

    elif args.date:
        print(f"\n📡 {args.date} の書類を検査中...")
        docs = fetch_documents_by_date(args.date)
        for doc in docs:
            if args.code and args.code not in doc.get("secCode", ""):
                continue
            r = analyze_document(doc)
            if r and r["total_score"] >= args.threshold:
                alerts.append(r)
        print_results(alerts)

    elif args.range:
        alerts_raw = scan_date_range(args.range[0], args.range[1], args.code)
        alerts = [a for a in alerts_raw if a["total_score"] >= args.threshold]
        print_results(alerts)

    else:
        print("\n使い方:")
        print("  python tob_detector.py --demo")
        print("  python tob_detector.py --date 2026-01-23")
        print("  python tob_detector.py --range 2025-01-01 2026-04-01")
        print("  python tob_detector.py --range 2025-01-01 2026-04-01 --code 8289")
        sys.exit(0)

    if alerts:
        save_results(alerts, args.output)

    print(f"\n{'=' * 60}")
    print(f"  完了。対象: {len(alerts)}件 / 閾値スコア: {args.threshold}以上")
    print(f"{'=' * 60}\n")

if __name__ == "__main__":
    main()
