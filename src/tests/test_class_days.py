"""授業日の計算のテスト。config.CALENDAR の写し間違いを見つけるのが主な役目。

公式の授業日程表は、月〜金のどの曜日もセメスター15回・クォーター8回になるよう、
祝日ぶんを振替授業日で埋めてある。祝日や振替を1つ写し忘れると、この回数が合わなくなる。
"""
import sys, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import class_days as CD
from config import CALENDAR


class 授業日の回数(unittest.TestCase):
    def test_どの曜日もセメスター15回クォーター8回(self):
        for year in CALENDAR:
            days = CD.class_days(year)
            for term, want in (("前期", 15), ("後期", 15), ("春学期", 8), ("夏学期", 8),
                               ("秋学期", 8), ("冬学期", 8)):
                for w in "月火水木金":
                    self.assertEqual(len(days[term][w]), want, f"{year} {term} {w}曜")

    def test_振替授業日は指定の曜日に入り本来の曜日からは外れる(self):
        days = CD.class_days(2026)
        # 11/5(木) は月曜日の授業
        self.assertIn("2026-11-05", days["後期"]["月"])
        self.assertNotIn("2026-11-05", days["後期"]["木"])
        # 1/13(水) は金曜日の授業。1/15(金) は休講
        self.assertIn("2027-01-13", days["冬学期"]["金"])
        self.assertNotIn("2027-01-15", days["冬学期"]["金"])

    def test_休業日と祝日には授業が無い(self):
        days = CD.class_days(2026)
        every = {d for term in days.values() for ds in term.values() for d in ds}
        for d in ("2026-10-12", "2026-10-30", "2026-11-02", "2026-11-03", "2026-12-28",
                  "2027-01-01", "2027-01-04", "2027-01-11", "2026-05-05", "2026-07-20"):
            self.assertNotIn(d, every, d)

    def test_日付は順に並び開講期の中に収まる(self):
        for year, cal in CALENDAR.items():
            for term, by_w in CD.class_days(year).items():
                start, end = cal["terms"][term]
                for ds in by_w.values():
                    self.assertEqual(ds, sorted(ds))
                    self.assertTrue(all(start <= d <= end for d in ds))

    def test_学部ごとの違い(self):
        w5 = "月火水木金"
        # 工学部: セメスター科目は1週早く終わる（14回 + 定期試験）
        eng = CD.class_days(2026, "工学部")
        self.assertTrue(all(len(eng[t][w]) == 14 for t in ("前期", "後期") for w in w5))
        self.assertEqual(eng["後期"]["水"][-1], "2027-01-27")
        self.assertTrue(all(len(eng[t][w]) == 8 for t in ("春学期", "冬学期") for w in w5))
        # 法学部: 定期試験の週を飛ばし、補習期間に15回目。10/2 は授業あり、12/25 は無し
        law = CD.class_days(2026, "法学部")
        self.assertTrue(all(len(law[t][w]) == 15 for t in ("前期", "後期") for w in w5))
        self.assertIn("2026-10-02", law["後期"]["金"])
        self.assertNotIn("2026-12-25", law["後期"]["金"])
        self.assertNotIn("2027-02-01", law["後期"]["月"])      # 定期試験期間
        self.assertEqual(law["後期"]["月"][-1], "2027-02-08")  # 補習期間
        self.assertIn("2026-06-09", law["前期"]["金"])         # 火曜だが金曜日の授業
        self.assertNotIn("2026-05-01", law["前期"]["金"])
        # 経済学部: 1/13 は休講（全学は金曜日の授業）、2/12 は授業あり
        econ = CD.class_days(2026, "経済学部")
        self.assertNotIn("2027-01-13", econ["冬学期"]["金"] + econ["冬学期"]["水"])
        self.assertIn("2027-02-12", econ["冬学期"]["金"])
        self.assertEqual(len(econ["冬学期"]["金"]), 8)
        # 農学部: 1/19 は休講、2/12（金）に火曜日の授業
        agr = CD.class_days(2026, "農学部")
        self.assertNotIn("2027-01-19", agr["冬学期"]["火"])
        self.assertIn("2027-02-12", agr["冬学期"]["火"])
        self.assertEqual(len(agr["冬学期"]["火"]), 8)
        self.assertEqual(len(agr["後期"]["火"]), 15)
        # 差分の無い学部は全学と同じ
        self.assertEqual(CD.class_days(2026, "理学部"), CD.class_days(2026))

    def test_画面に渡す形(self):
        site = CD.for_site(2026)
        self.assertEqual(sorted(site["by_faculty"]), ["工学部", "法学部", "経済学部", "農学部"])
        self.assertIn("理学部", site["verified"])
        self.assertIn("工学部", site["verified"])
        self.assertNotIn("文学部", site["verified"])

    def test_写していない年度は画面に渡さない(self):
        self.assertIsNone(CD.for_site(1999))
        self.assertEqual(sorted(CD.for_site(2026)["periods"]), ["1", "2", "3", "4", "5", "6"])


if __name__ == "__main__":
    unittest.main()
