"""時間割表まわりの回帰テスト。ネットワークもPDFも使わない。

3巡目レビューで「新しく足した3モジュールにテストが1件もない」と書いた。
実際に取りこぼしたのは次の3つで、いずれも数行の入力を固定しておけば防げた。

  ・2026年度版は「〇」（漢数字のゼロ）、2025年度版は「○」（白丸）で字が違う
  ・印が「●※」のように重なる行を、完全一致の判定で落としていた
  ・表の升が空になる行があり、講義コードを取れていなかった

PDFそのものは置けないので、pdfplumber が返す形（表＝入れ子のリスト、
ページ＝テキスト）だけを真似て渡す。
"""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import core_btable as CB
import timetable_rooms as TR

HEADER = ["講義コード", "科目区分", "科目名", "学期", "曜日", "時限",
          "担当クラス", "開講地区", "教室", "事前申請", "担当教員氏名", "備考"]


def row(code, title, mark, room="2404", term="後期", note=""):
    return [code, "【総合】", title, term, "水", "4", "全学年",
            "伊都（センター）", room, mark, "九大 太郎", note]


class FakePage:
    def __init__(self, tables, text=""):
        self._tables, self._text = tables, text

    def extract_tables(self):
        return self._tables

    def extract_text(self):
        return self._text


class FakePDF:
    def __init__(self, pages):
        self.pages = pages

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class 事前申請の印(unittest.TestCase):
    def test_年度で字が違う丸をどちらも拾う(self):
        # 2026年度版は漢数字のゼロ、2025年度版は白丸
        self.assertEqual(CB.marks_of("〇"), "○")
        self.assertEqual(CB.marks_of("○"), "○")
        self.assertEqual(CB.marks_of("◯"), "○")

    def test_印が重なる升を両方拾う(self):
        self.assertEqual(CB.marks_of("●※"), "●※")
        self.assertEqual(CB.marks_of("※●"), "●※")

    def test_印のない升は空(self):
        self.assertEqual(CB.marks_of(""), "")
        self.assertEqual(CB.marks_of("－"), "")

    def test_備考の米印は事前申請にしない(self):
        # 「※5/27は4限はE-109」のような注記が備考にあるだけの行
        page = FakePage([[HEADER, row("26533362", "韓国語Ⅲ", "",
                                      note="※5/27は4限はE‒109")]])
        got = list(self._rows(page))
        self.assertEqual(CB.marks_of(got[0]["事前申請"]), "")

    def test_升が空でも生テキストから講義コードを拾う(self):
        # 表の升だけ空になる行がある（教育学特論）。同じページの本文から補う
        broken = row("", "教育学特論", "○", room="1402", term="前期")
        text = ("26532071 【高年次】 社会包摂とデザインＢ 夏学期 火 1 その他 遠隔/512\n"
                "26532073 【高年次】 教育学特論 前期 火 1 伊都（センター） 1402 ○ 竹熊 尚夫\n"
                "26532080 【健スポ】 身体運動科学実習ⅡA 春学期 火 1 伊都（センター） ‒\n")
        got = list(self._rows(FakePage([[HEADER, broken]], text)))
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["講義コード"], "26532073")

    def test_科目名が合わない行のコードは補わない(self):
        broken = row("", "存在しない科目", "○")
        text = "26532073 【高年次】 教育学特論 前期 火 1 伊都（センター） 1402 ○\n"
        self.assertEqual(list(self._rows(FakePage([[HEADER, broken]], text))), [])

    def _rows(self, page):
        from unittest.mock import patch
        with patch.object(CB.pdfplumber, "open", return_value=FakePDF([page])):
            yield from CB.rows_of(Path("dummy.pdf"))


class 教室の突き合わせ(unittest.TestCase):
    def test_行の途中にある科目名でも当たる(self):
        # 理学部は「3 数学特論11 坂本 祥太 W1-C-513」と前後を数字と教員名に挟まれる
        names = {TR.norm("数学特論11"): [{"c": "1", "t": "数学特論11"}]}
        self.assertEqual(TR.find_name("3 数学特論11 坂本 祥太 ", names),
                         TR.norm("数学特論11"))

    def test_教室に近いほうの科目名を採る(self):
        names = {TR.norm("情報構造論"): [{"c": "1", "t": "情報構造論"}],
                 TR.norm("機械学習"): [{"c": "2", "t": "機械学習"}]}
        # 1行に2科目が並ぶとき、教室の直前にあるほうが正しい
        self.assertEqual(TR.find_name("情報構造論 稲永 俊介 W1-B-313 機械学習 畑埜 晃平 ",
                                      names), TR.norm("機械学習"))

    def test_短すぎる名前は使わない(self):
        # 「英語」のような3文字以下は、どこにでも当たるので採らない
        names = {TR.norm("英語"): [{"c": "1", "t": "英語"}]}
        self.assertIsNone(TR.find_name("専門英語（森林機能学分野）", names))

    def test_記号つきの科目名も正規化して当たる(self):
        # 法学部は「◆中国法演習」のように記号が付く
        self.assertEqual(TR.norm("◆中国法演習"), TR.norm("中国法演習"))
        self.assertEqual(TR.norm("■ローマ法Ⅰ"), TR.norm("ローマ法Ⅰ"))

    def test_教室でない語をはじく(self):
        self.assertTrue(TR.NOT_ROOM.search("文・時間割参照"))
        self.assertTrue(TR.NOT_ROOM.search("基幹教育"))
        self.assertFalse(TR.NOT_ROOM.search("A104"))


class 農学部の教室(unittest.TestCase):
    def test_隣の科目の教室を拾わない(self):
        # 「26342323 土壌物理学 岩田 幸良 229 田村 和彦 228」で末尾を採ると
        # 隣の科目の228になる。コードの直後に最初に出る番号を採る
        idx = {"26342323": {"c": "26342323", "t": "土壌物理学"}}
        line = "3 26342323 土壌物理学 岩田 幸良 229 田村 和彦 228 26342204 水理学Ⅰ"
        from unittest.mock import patch
        with patch.object(TR, "lines_of", return_value=[(None, line)]):
            got, mention, disagree = TR.agr_rooms(idx)
        self.assertEqual(got["26342323"]["room"], "229")

    def test_科目名が食い違う行は捨てる(self):
        idx = {"26342323": {"c": "26342323", "t": "土壌物理学"}}
        line = "3 26342323 まったく別の科目名 岩田 幸良 229"
        from unittest.mock import patch
        with patch.object(TR, "lines_of", return_value=[(None, line)]):
            got, mention, disagree = TR.agr_rooms(idx)
        self.assertNotIn("26342323", got)
        self.assertEqual(disagree, 1)


class 学期をまたぐ照合(unittest.TestCase):
    """時間割PDFの前期ページと後期ページを取り違えない（法学部で前期18件が未掲載扱いになった）。"""

    def test_ページの学期を見出しから読む(self):
        self.assertEqual(TR.page_season(["令和８年度授業時間割 前期 （2026年４月～2026年９月）",
                                         "◆：通年科目 〇：基幹教育科目"]), "spring")
        self.assertEqual(TR.page_season(["令和８年度授業時間割 後期 （2026年10月～2027年３月）",
                                         "◆：通年科目"]), "autumn")
        # 3行目以降の凡例（前年度後期開始越年科目）は見ない
        self.assertEqual(TR.page_season(["令和８年度授業時間割 前期", "凡例",
                                         "◇：前年度後期開始越年科目"]), "spring")
        self.assertIsNone(TR.page_season(["時間 1 2 3 4 5", "月 火 水"]))

    def test_学期の違う同名科目には教室を付けない(self):
        names = {TR.norm("民法演習"): [{"c": "A", "t": "民法演習", "season": "autumn"}]}
        from unittest.mock import patch
        with patch.object(TR, "lines_of", return_value=[("spring", "◆民法演習 津田 D108 ３・４")]):
            got, _, _ = TR.by_room_marker(["law.pdf"], "法学部", TR.ROOM_LAW, names, "u")
        self.assertEqual(got, {})
        with patch.object(TR, "lines_of", return_value=[("autumn", "◆民法演習 津田 D108 ３・４")]):
            got, _, _ = TR.by_room_marker(["law.pdf"], "法学部", TR.ROOM_LAW, names, "u")
        self.assertEqual(got["A"]["room"], "D108")

    def test_別学期の短い名前に吸われない(self):
        # 後期ページの「国際政治学Ⅰ」の中に、前期の「政治学Ⅰ」も含まれている
        names = {TR.norm("国際政治学Ⅰ"): [{"c": "A", "t": "国際政治学Ⅰ", "season": "autumn"}],
                 TR.norm("政治学Ⅰ"): [{"c": "B", "t": "政治学Ⅰ", "season": "spring"}]}
        line = "●租税法 山田 E104 ３・４ ●国際政治学Ⅰ 椛島洋美 D106 ３・４"
        from unittest.mock import patch
        with patch.object(TR, "lines_of", return_value=[("autumn", line)]):
            got, _, _ = TR.by_room_marker(["law.pdf"], "法学部", TR.ROOM_LAW, names, "u")
        self.assertEqual(got, {"A": {"room": "D106", "source_url": "u"}})

    def test_終わりが同じなら長い名前を採る(self):
        names = {TR.norm("比較政治学Ⅱ"): [{"c": "A"}], TR.norm("政治学Ⅱ"): [{"c": "B"}]}
        self.assertEqual(TR.find_name("●比較政治学Ⅱ 出水 ", names), TR.norm("比較政治学Ⅱ"))

    def test_法学部のゼミは担当教員で演習Ⅰに当てる(self):
        # 時間割は「民事訴訟法演習 上田」、Campusmate は「演習Ⅰ」。同じ先生の民事訴訟法Ⅰもある
        names = {TR.norm("演習Ⅰ"): [{"c": "S1", "t": "演習Ⅰ", "i": ["上田 竹志"], "season": None},
                                    {"c": "S2", "t": "演習Ⅰ", "i": ["西 英昭"], "season": None},
                                    {"c": "S3", "t": "演習Ⅰ", "i": ["西村 友海"], "season": None}],
                 TR.norm("民事訴訟法Ⅰ"): [{"c": "L1", "t": "民事訴訟法Ⅰ", "i": ["上田 竹志"],
                                            "season": "spring"}]}
        self.assertEqual(TR.by_instructor("民事訴訟法演習", "◆民事訴訟法演習 上田 ", names)["c"], "S1")
        # 1文字の姓は「西村」に当てない
        self.assertEqual(TR.by_instructor("中国法演習", "◆中国法演習 西 ", names)["c"], "S2")
        # 担当のゼミが無い先生は当てない（未掲載に回る）
        self.assertIsNone(TR.by_instructor("外交史演習", "◆外交史演習 中島 ", names))

    def test_通年科目はどちらの学期のページとも合う(self):
        self.assertTrue(TR.fits({"season": None}, "spring"))
        self.assertTrue(TR.fits({"season": "spring"}, None))
        self.assertFalse(TR.fits({"season": "autumn"}, "spring"))

    def test_他学部にある科目は未掲載にしない(self):
        known = {TR.norm("学術英語・テーマベース"), TR.norm("Education and Politics Ⅰ")}
        self.assertTrue(TR.is_known("学術英語・テーマベース", known))
        # PDFで末尾が切れた名前も、長ければ前方一致で同じ科目とみなす
        self.assertTrue(TR.is_known("Education and politic", known))
        self.assertFalse(TR.is_known("ローマ法Ⅰ", known))
        # 短い名前の前方一致は採らない（「政治学」が「政治学史」に当たらないように）
        self.assertFalse(TR.is_known("政治学", {TR.norm("政治学史Ⅰ")}))


if __name__ == "__main__":
    unittest.main()
