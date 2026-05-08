"""PEファンド・法律事務所の住所リスト

NTA APIの「prefectureName + cityName + streetNumber」と照合する。
建物名（building）の部分一致で判定する。
"""

TARGET_LOCATIONS = [
    # === PEファンド ===
    {'name': 'カーライル・ジャパン', 'building': '新丸の内ビル', 'pref': '東京都', 'city': '千代田区', 'category': 'PE'},
    {'name': 'ベインキャピタル・ジャパン', 'building': '赤坂Ｂｉｚタワー', 'pref': '東京都', 'city': '港区', 'category': 'PE'},
    {'name': 'ベインキャピタル・ジャパン', 'building': '赤坂Bizタワー', 'pref': '東京都', 'city': '港区', 'category': 'PE'},
    {'name': 'KKR Japan', 'building': 'アークヒルズ仙石山', 'pref': '東京都', 'city': '港区', 'category': 'PE'},
    {'name': 'アドバンテッジパートナーズ', 'building': 'ニューオータニガーデン', 'pref': '東京都', 'city': '千代田区', 'category': 'PE'},
    {'name': 'ユニゾン・キャピタル', 'building': '有楽町電気ビル', 'pref': '東京都', 'city': '千代田区', 'category': 'PE'},
    {'name': '日本産業パートナーズ', 'building': 'パシフィックセンチュリープレイス', 'pref': '東京都', 'city': '千代田区', 'category': 'PE'},
    {'name': '丸の内キャピタル', 'building': '東京ビル', 'pref': '東京都', 'city': '千代田区', 'category': 'PE'},
    {'name': 'ポラリス・キャピタル・グループ', 'building': '大手町フィナンシャル', 'pref': '東京都', 'city': '千代田区', 'category': 'PE'},
    {'name': 'インテグラル', 'building': '丸の内', 'pref': '東京都', 'city': '千代田区', 'category': 'PE'},
    {'name': '三井物産企業投資', 'building': '大手町', 'pref': '東京都', 'city': '千代田区', 'category': 'PE'},
    {'name': 'MBKパートナーズ・ジャパン', 'building': 'ミッドタウン', 'pref': '東京都', 'city': '港区', 'category': 'PE'},
    {'name': 'CVC Asia Pacific Japan', 'building': '丸の内', 'pref': '東京都', 'city': '千代田区', 'category': 'PE'},
    {'name': 'ペルミラ・アドバイザーズ', 'building': '丸の内', 'pref': '東京都', 'city': '千代田区', 'category': 'PE'},
    {'name': 'TPGキャピタル・ジャパン', 'building': '六本木', 'pref': '東京都', 'city': '港区', 'category': 'PE'},
    {'name': 'ロングリーチグループ', 'building': '大手町', 'pref': '東京都', 'city': '千代田区', 'category': 'PE'},
    {'name': 'シティック・キャピタル', 'building': '虎ノ門', 'pref': '東京都', 'city': '港区', 'category': 'PE'},
    {'name': 'アント・キャピタル・パートナーズ', 'building': '虎ノ門', 'pref': '東京都', 'city': '港区', 'category': 'PE'},
    {'name': 'J-STAR', 'building': '内幸町', 'pref': '東京都', 'city': '千代田区', 'category': 'PE'},
    {'name': 'アイ・シグマ・キャピタル', 'building': '大手町', 'pref': '東京都', 'city': '千代田区', 'category': 'PE'},
    
    # === M&A大手法律事務所 ===
    {'name': '西村あさひ法律事務所', 'building': '大手門タワー', 'pref': '東京都', 'city': '千代田区', 'category': 'Law'},
    {'name': '森・濱田松本法律事務所', 'building': '丸の内パークビル', 'pref': '東京都', 'city': '千代田区', 'category': 'Law'},
    {'name': '長島・大野・常松法律事務所', 'building': '大手町パークビル', 'pref': '東京都', 'city': '千代田区', 'category': 'Law'},
    {'name': 'TMI総合法律事務所', 'building': '六本木ヒルズ森タワー', 'pref': '東京都', 'city': '港区', 'category': 'Law'},
    {'name': 'アンダーソン・毛利・友常法律事務所', 'building': '大手町パークビル', 'pref': '東京都', 'city': '千代田区', 'category': 'Law'},
]
