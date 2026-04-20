"""
tests/test_date_parser.py
日付パーサー・和暦変換の単体テスト（pytest形式）
"""

import sys
import os
from datetime import date

# detector パッケージをパスに追加
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from detector.tob_detector import parse_date_string, wareki_to_seireki


# ============================================================
# wareki_to_seireki
# ============================================================

class TestWarekiToSeireki:

    def test_reiwa_basic(self):
        assert wareki_to_seireki("令和", 7, 2, 28) == date(2025, 2, 28)

    def test_reiwa_first_year(self):
        """令和元年 = 2019年"""
        assert wareki_to_seireki("令和", 1, 5, 1) == date(2019, 5, 1)

    def test_reiwa_before_start_returns_none(self):
        """令和元年5月1日より前はNone"""
        assert wareki_to_seireki("令和", 1, 4, 30) is None

    def test_heisei_basic(self):
        assert wareki_to_seireki("平成", 30, 12, 31) == date(2018, 12, 31)

    def test_showa_last_day(self):
        """昭和最終日"""
        assert wareki_to_seireki("昭和", 64, 1, 7) == date(1989, 1, 7)

    def test_alphabet_reiwa(self):
        assert wareki_to_seireki("R", 7, 2, 28) == date(2025, 2, 28)

    def test_alphabet_heisei(self):
        assert wareki_to_seireki("H", 30, 12, 1) == date(2018, 12, 1)

    def test_unknown_era_returns_none(self):
        assert wareki_to_seireki("XX", 1, 1, 1) is None

    def test_invalid_date_returns_none(self):
        """存在しない日付"""
        assert wareki_to_seireki("令和", 6, 2, 30) is None


# ============================================================
# parse_date_string
# ============================================================

class TestParseDateString:

    # 西暦フォーマット
    def test_iso_format(self):
        assert parse_date_string("2025-02-28") == date(2025, 2, 28)

    def test_slash_format(self):
        assert parse_date_string("2025/02/28") == date(2025, 2, 28)

    def test_8digit_format(self):
        assert parse_date_string("20250228") == date(2025, 2, 28)

    # 和暦（漢字）
    def test_reiwa_kanji(self):
        assert parse_date_string("令和7年2月28日") == date(2025, 2, 28)

    def test_reiwa_first_year_kanji(self):
        assert parse_date_string("令和元年5月1日") == date(2019, 5, 1)

    def test_heisei_kanji(self):
        assert parse_date_string("平成30年12月31日") == date(2018, 12, 31)

    def test_showa_kanji(self):
        assert parse_date_string("昭和64年1月7日") == date(1989, 1, 7)

    # 和暦（アルファベット略称）
    def test_reiwa_alpha(self):
        assert parse_date_string("R7.2.28") == date(2025, 2, 28)

    def test_heisei_alpha(self):
        assert parse_date_string("H30.12.1") == date(2018, 12, 1)

    # EDINET表紙テキストを含む文字列
    def test_edinet_cover_text(self):
        text = "【報告義務発生日】 令和7年2月28日"
        assert parse_date_string(text) == date(2025, 2, 28)

    def test_edinet_cover_text_heisei(self):
        text = "報告義務発生日　平成30年3月15日"
        assert parse_date_string(text) == date(2018, 3, 15)

    # 不完全・不正なケース
    def test_none_input(self):
        assert parse_date_string(None) is None

    def test_empty_string(self):
        assert parse_date_string("") is None

    def test_space_separated_wareki_returns_none(self):
        """スペース区切りの和暦（令和8 1 23形式）はパース不可"""
        assert parse_date_string("令和8 1 23") is None

    def test_random_text(self):
        assert parse_date_string("該当事項なし") is None


# ============================================================
# Olympic事例の再現テスト
# ============================================================

class TestOlympicCase:
    """
    実際のOlympic変更報告書から確認した値での統合テスト。
    義務発生日: 令和7年2月28日 = 2025-02-28
    提出日:     令和8年1月23日 = 2026-01-23
    """

    def test_obligation_date(self):
        assert parse_date_string("令和7年2月28日") == date(2025, 2, 28)

    def test_submit_date(self):
        assert parse_date_string("令和8年1月23日") == date(2026, 1, 23)

    def test_calendar_days_delay(self):
        obligation = parse_date_string("令和7年2月28日")
        submit = parse_date_string("令和8年1月23日")
        assert (submit - obligation).days == 329

    def test_business_days_delay(self):
        """329暦日 = 218営業日（祝日・土日除外）"""
        from detector.tob_detector import calc_business_days
        obligation = date(2025, 2, 28)
        submit = date(2026, 1, 23)
        biz_days = calc_business_days(obligation, submit)
        # 法定5営業日を超えた遅延
        assert biz_days > 5
        # 218営業日前後（祝日計算の実装により±2日の誤差を許容）
        assert 216 <= biz_days <= 220
