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
        with patch.object(TR, "lines_of", return_value=[line]):
            got, mention, disagree = TR.agr_rooms(idx)
        self.assertEqual(got["26342323"]["room"], "229")

    def test_科目名が食い違う行は捨てる(self):
        idx = {"26342323": {"c": "26342323", "t": "土壌物理学"}}
        line = "3 26342323 まったく別の科目名 岩田 幸良 229"
        from unittest.mock import patch
        with patch.object(TR, "lines_of", return_value=[line]):
            got, mention, disagree = TR.agr_rooms(idx)
        self.assertNotIn("26342323", got)
        self.assertEqual(disagree, 1)


if __name__ == "__main__":
    unittest.main()
