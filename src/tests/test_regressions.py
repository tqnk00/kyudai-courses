"""Offline regression tests; never access the configured production database."""
import csv
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace as NS

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import db
import config
import campusmate as cm
import detail
import extract
import grading
import report
import site_data
import build_site
import summarize
import sweep

UID = config.KYUSHU['university_id']
BODY = """科目名称
統計入門
学部カテゴリ
経済学部
対象学部等
学部2年
対象学年
2年～5年
授業科目区分
（経済工学科）選択必修科目／（経済・経営学科）自由選択科目
単位数
2
開講学期
春学期
曜日時限
春学期 土曜日 ２時限
授業科目の目的（日本語）
統計の基礎を学び、データを使って演習するための授業です。
授業科目の成績評価の方法について
定期試験
実施しない。
小テスト
30%
レポート
70%
出席
なし。
"""


def result_page(lo, hi, total, codes):
    rows = ''.join(f'<tr class="column_odd"><td></td><td>{c}</td><td>統計</td><td>春 月2</td><td>教員</td></tr>' for c in codes)
    return f'<h1>講義一覧</h1>{lo}-{hi}件表示 / {total}件中{rows}'


class ParsingTests(unittest.TestCase):
    def test_term_groups_cover_all_search_codes(self):
        for label, season in config.KAIKO.values():
            if season != 'full':
                self.assertNotEqual(extract.term_group_of(label), '通年・その他', label)

    def test_economics_uses_faculty_category(self):
        row, slots = extract.build_row('x', 2027, BODY, 'A')
        self.assertEqual(row['category'], '選択必修科目')
        self.assertIn('経済・経営学科', row['category_raw'])
        self.assertEqual(slots, [('春学期', '土', '2')])
        self.assertEqual(row['grades'], '2,3,4,5')

    def test_unused_grading_not_flagged(self):
        row, _ = extract.build_row('x', 2027, BODY, 'A')
        self.assertEqual([row[k] for k in ('eval_exam', 'eval_report', 'eval_quiz', 'eval_attend')], [0, 1, 1, 0])

    def test_free_text_negation_and_quiz(self):
        flags, raw = extract.eval_flags('期末試験は実施しない。確認テストとレポートで評価する。')
        self.assertEqual(flags['eval_exam'], 0)
        self.assertEqual(flags['eval_quiz'], 1)
        self.assertEqual(flags['eval_report'], 1)
        self.assertIn('実施しない', raw)

    def test_grading_punctuation(self):
        result = grading.parse('定期試験\nなし。\nレポート\n100%')
        self.assertEqual(result['unused'], ['定期試験'])
        self.assertEqual(grading.total_pct(result['rows']), 100)

    def test_detail_layout_c_starts_before_nested_title(self):
        body, layout = detail.extract_body('<script>科目名称</script><div>授業科目名</div><div>ユニット</div><div>科目名称</div><div>構成科目</div><div>PAGE TOP</div>')
        self.assertEqual(layout, 'C')
        self.assertTrue(body.startswith('授業科目名'))
        self.assertNotIn('PAGE TOP', body)

    def test_layout_b(self):
        body, layout = detail.extract_body('<div>講義科目名</div><p>薬学</p><div>PAGE TOP</div>')
        self.assertEqual(layout, 'B')
        self.assertIn('薬学', body)

    def test_url_encodes_course_code(self):
        url = detail.url_for('A&B#C', 2027)
        self.assertIn('A%26B%23C', url)

    def test_unrecognized_count_is_error(self):
        with self.assertRaises(RuntimeError):
            cm.hit_range('<h1>講義一覧</h1>システムメンテナンス')

    def test_explicit_zero(self):
        self.assertEqual(cm.hit_range('講義一覧 該当するデータはありません'), (0, 0, 0))

    def test_two_pages(self):
        first = result_page(1, 2, 3, ['a', 'b'])
        sess = NS(search=lambda *a: first, page=lambda prev, page, per: first if page == '' else result_page(3, 3, 3, ['c']))
        rows, total = cm.sweep_kaiko(sess, 2027, '10', per_page=2, pause=0, log=lambda *a: None)
        self.assertEqual([r['course_code'] for r in rows], ['a', 'b', 'c'])
        self.assertEqual(total, 3)

    def test_repeated_page_is_error(self):
        page = result_page(1, 2, 3, ['a', 'b'])
        sess = NS(search=lambda *a: page, page=lambda *a: page)
        with self.assertRaises(RuntimeError):
            cm.sweep_kaiko(sess, 2027, '10', per_page=2, pause=0, log=lambda *a: None)

    def test_first_resize_error_not_zero_success(self):
        sess = NS(search=lambda *a: result_page(1, 1, 1, ['a']), page=lambda *a: 'システムが期待しない操作')
        with self.assertRaises(RuntimeError):
            cm.sweep_kaiko(sess, 2027, '10', pause=0, log=lambda *a: None)

    def test_missing_row_is_error(self):
        page = result_page(1, 2, 2, ['a'])
        sess = NS(search=lambda *a: page, page=lambda *a: page)
        with self.assertRaises(RuntimeError):
            cm.sweep_kaiko(sess, 2027, '10', pause=0, log=lambda *a: None)

    def test_worker_validation_before_database(self):
        for workers in (0, 6, -1):
            with self.assertRaises(ValueError):
                detail.run(workers=workers)

    def test_csv_formula_protection(self):
        for value in ('=1+1', ' @SUM(A1)', '+cmd', '\tformula', '-1+1'):
            self.assertTrue(report.csv_cell(value).startswith("'"))
        self.assertEqual(report.csv_cell(-2), -2)
        self.assertEqual(report.csv_cell('普通の科目'), '普通の科目')


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='syllabus-review-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for module, attr, value in ((db, 'DB_PATH', self.root / 'test.db'),
                                    (config, 'DB_PATH', self.root / 'test.db'),
                                    (site_data, 'EXPORT_DIR', self.root),
                                    (build_site, 'EXPORT_DIR', self.root),
                                    (report, 'EXPORT_DIR', self.root)):
            p = patch.object(module, attr, value)
            p.start()
            self.addCleanup(p.stop)
        with db.session():
            pass

    def seed(self, code='TEST001', body=BODY, year=2027):
        with db.session() as con:
            con.execute('INSERT INTO syllabus_raw(university_id,year,course_code,body,body_sha256,fetched_at,layout) VALUES (?,?,?,?,?,?,?)',
                        (UID, year, code, body, hashlib.sha256(body.encode()).hexdigest(), '2027-01-01', 'A'))
            con.execute('INSERT INTO course_index(university_id,year,course_code) VALUES (?,?,?)', (UID, year, code))

    def test_migrations_idempotent(self):
        with db.session() as con:
            db.migrate(con)
            db.migrate(con)
            self.assertIn('category_raw', {r[1] for r in con.execute('PRAGMA table_info(course_structured)')})

    def test_failed_extraction_rolls_back(self):
        self.seed()
        extract.run(2027)
        with patch.object(extract, 'build_row', side_effect=ValueError('bad layout')):
            with self.assertRaises(ValueError):
                extract.run(2027)
        with db.session() as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM course_structured').fetchone()[0], 1)

    def test_repeat_extraction_does_not_duplicate_slots(self):
        self.seed()
        self.seed('OLD', year=2026)
        extract.run(2026)
        extract.run(2027)
        extract.run(2027)
        with db.session() as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM course_slots').fetchone()[0], 2)

    def test_summary_null_and_model_changes_regenerated(self):
        self.seed()
        extract.run(2027)
        with db.session() as con:
            con.execute('INSERT INTO course_summary(university_id,year,course_code) VALUES (?,?,?)', (UID, 2027, 'TEST001'))
            self.assertEqual(len(summarize.targets(con, 2027)), 1)
            con.execute('UPDATE course_summary SET source_sha256=(SELECT body_sha256 FROM syllabus_raw), prompt_version=?,model=?', (summarize.PROMPT_VERSION, 'old-model'))
            self.assertEqual(len(summarize.targets(con, 2027)), 1)
            con.execute('UPDATE course_summary SET model=?', (summarize.MODEL,))
            self.assertEqual(summarize.targets(con, 2027), [])
            self.assertEqual(summarize.targets(con, 2027, limit=0), [])

    def test_stale_summary_not_exported(self):
        self.seed()
        extract.run(2027)
        with db.session() as con:
            con.execute('INSERT INTO course_summary(university_id,year,course_code,summary,source_sha256) VALUES (?,?,?,?,?)', (UID, 2027, 'TEST001', '古い要約', 'old'))
        _, path = report.export(2027)
        self.assertIsNone(json.loads(path.read_text(encoding='utf-8'))[0]['summary'])

    def test_empty_export_has_headers(self):
        path, _ = report.export(2027)
        with path.open(encoding='utf-8-sig', newline='') as f:
            rows = list(csv.reader(f))
        self.assertEqual(len(rows), 1)
        self.assertIn('course_code', rows[0])

    def test_sweep_failure_recorded_and_raised(self):
        sess = NS(close=lambda: None)
        with patch.object(cm, 'SearchSession', return_value=sess), patch.object(cm, 'sweep_kaiko', side_effect=RuntimeError('offline')), patch.dict(sweep.SETS, {'test': ['10']}):
            with self.assertRaises(RuntimeError):
                sweep.run(2027, 'test', pause=0)
        with db.session() as con:
            row = con.execute('SELECT ended_at, n_error FROM crawl_runs').fetchone()
            self.assertTrue(row['ended_at'])
            self.assertEqual(row['n_error'], 1)

    def test_detail_failures_do_not_publish_success(self):
        self.seed()
        with patch.object(detail, 'fetch_one', return_value=((None, None), 0, 'offline')), patch.object(detail, 'client', return_value=None):
            with self.assertRaises(RuntimeError):
                detail.run(2027, workers=1, pause=0, refetch=True)
        with db.session() as con:
            self.assertEqual(con.execute('SELECT n_error FROM crawl_runs').fetchone()[0], 1)
            self.assertEqual(con.execute('SELECT body FROM syllabus_raw').fetchone()[0], BODY)

    def test_summary_invalid_json_types(self):
        self.seed()
        payloads = ['[]', '{"summary": 1, "topics": []}', '{"summary":"a", "topics":[2]}', '{"summary":"ok", "topics":["topic"]}']
        results = [NS(custom_id='TEST001', result=NS(type='succeeded', message=NS(stop_reason='end_turn', content=[NS(type='text', text=p)]))) for p in payloads]
        batches = NS(retrieve=lambda _: NS(processing_status='ended'), results=lambda _: results)
        fake = NS(Anthropic=lambda: NS(messages=NS(batches=batches)))
        with patch.dict(sys.modules, {'anthropic': fake}), db.session() as con:
            self.assertEqual(summarize.collect(con, 2027, ['batch'], {'TEST001': 'hash'}), (1, 3))

    def test_end_to_end_html_and_json_injection(self):
        attack = '\"><img src=x onerror=alert(1)><!--<script></script>'
        self.seed('TEST001', BODY.replace('統計入門', '統計入門' + attack))
        self.seed('X' + attack, BODY.replace('春学期', '後期').replace('土曜日', '月曜日'))
        extract.run(2027)
        path = build_site.build(2027, undergrad_only=False)
        html = path.read_text(encoding='utf-8')
        self.assertTrue(html.startswith('<!doctype html>'))
        payload = re.search(r'<script type="application/json" id="data">(.*?)</script>', html, re.S).group(1)
        data = json.loads(payload)
        self.assertEqual(data['meta']['year'], 2027)
        self.assertFalse(data['meta']['undergrad_only'])
        self.assertEqual(data['meta']['term_groups'], ['春学期', '後期'])
        self.assertNotIn('<', payload)
        self.assertIn(attack, data['courses'][0]['t'])
        self.assertEqual(data['courses'][0]['grraw'], '2年～5年')
        if os.environ.get('SYLLABUS_TEST_HTML'):
            Path(os.environ['SYLLABUS_TEST_HTML']).write_text(html, encoding='utf-8')




class ReviewFixes(unittest.TestCase):
    """2巡目レビューで直した項目の回帰テスト。"""

    # --- A-3 出席条件の割合を配点にしない ---
    def test_attendance_condition_is_not_a_weight(self):
        raw = ("定期試験\nFinal exam 80%\n授業への貢献度\n20%\n"
               "出席\n80% attendance is expected")
        g = grading.parse(raw)
        by = {r[0]: r for r in g["rows"]}
        self.assertEqual(by["定期試験"][1], 80.0)
        self.assertIsNone(by["出席"][1])
        self.assertEqual(by["出席"][3], 1)                 # 条件の印
        self.assertIn("80%", by["出席"][2])                # 原文を落とさない
        self.assertEqual(grading.total_pct(g["rows"]), 100.0)

    def test_at_least_percent_is_not_a_weight(self):
        g = grading.parse("出席\nStudents must attend at least 60% of the classes.")
        self.assertIsNone(g["rows"][0][1])
        self.assertEqual(g["rows"][0][3], 1)

    def test_char_count_is_not_a_condition(self):
        """「（1500字以上）を課す（50%）」の 50% は配点。以上に引きずられない。"""
        g = grading.parse("レポート\n期末レポート（1500字以上）を課す（50%）")
        self.assertEqual(g["rows"][0][1], 50.0)

    # --- A-4 「25%×2回」 ---
    def test_percent_times_count(self):
        g = grading.parse("小テスト\n復習課題を2回課す（25%×2回）。")
        self.assertEqual(g["rows"][0][1], 50.0)
        self.assertIn("25%", g["rows"][0][2])              # 補足文を壊さない

    # --- A-5 %なしの数字 / 0 の扱い ---
    def test_bare_number_is_a_percent(self):
        g = grading.parse("レポート\n60\n発表\n10\n授業への貢献度\n30")
        self.assertEqual([r[1] for r in g["rows"]], [60.0, 10.0, 30.0])
        self.assertEqual(grading.total_pct(g["rows"]), 100.0)

    def test_zero_row_does_not_set_eval_flag(self):
        flags, _ = extract.eval_flags("定期試験\n100\n授業への貢献度\n0")
        self.assertEqual(flags["eval_exam"], 1)
        self.assertEqual(flags["eval_attend"], 0)

    # --- A-2 授業計画の内容を1行目で切らない ---
    def test_plan_detail_keeps_wrapped_lines(self):
        sec = {"事前/事後学修の内容":
               "1\nオリエンテーション\n第1章 教師像を探る\nワーク「思い出の中の教師像」\n"
               "事前：考えをまとめる\n事後：第1章を読む\n"
               "2\n導入期\n第2章 教師をめざす\n事前：第2章を読む"}
        plan = site_data.plan_of(sec)
        self.assertEqual(len(plan), 2)
        self.assertIn("ワーク「思い出の中の教師像」", plan[0][2])
        self.assertNotIn("事前：", plan[0][2])

    def test_plan_ignores_duplicated_session_column(self):
        """回番号の列が2本ある様式（1 / １）でも回を落とさない。"""
        sec = {"事前/事後学修の内容": "1\n１\nガイダンス\n内容A\n2\n２\n第2回\n内容B"}
        plan = site_data.plan_of(sec)
        self.assertEqual([r[0] for r in plan], [1, 2])

    # --- A-9 授業計画でないセクションを拾わない ---
    def test_plan_ignores_grading_table(self):
        sec = {"授業科目の成績評価の方法について": "発表\n60\n授業への貢献度\n40",
               "授業計画": "授業計画は予定であり、変更することがあります。"}
        self.assertEqual(site_data.plan_of(sec), [])

    # --- B-3 6年制学部の学年上限 ---
    def test_six_year_faculty_grades(self):
        self.assertEqual(extract.grades_of("２年生以上", "医学部医学科"), "2,3,4,5,6")
        self.assertEqual(extract.grades_of("２年生以上", "工学部"), "2,3,4")
        self.assertEqual(extract.grades_of("全学年", "歯学部"), "1,2,3,4,5,6")
        self.assertEqual(extract.grades_of("全学年", "法学部"), "1,2,3,4")

if __name__ == '__main__':
    unittest.main(verbosity=2)
