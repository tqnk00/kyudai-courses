"""実測値のレポートとエクスポート（仕様書6章「検証すべき数値」）。

DB本体は OneDrive 外に置き、成果物だけ OneDrive 配下へ書き出す（仕様書1章）。
"""
import sys, csv, json, argparse, datetime as dt
import db as DB
from config import KYUSHU, EXPORT_DIR, YEAR, KAIKO

UID = KYUSHU["university_id"]


def q1(con, sql, args=()):
    r = con.execute(sql, args).fetchone()
    return r[0] if r else None


def report(year=YEAR):
    with DB.session() as con:
        a = lambda sql, args=(): q1(con, sql, (UID, year) + args)
        lines = []
        P = lines.append

        P(f"# 九大シラバスDB 実測レポート  ({dt.datetime.now():%Y-%m-%d %H:%M})")
        P("")
        P("## 件数")
        idx = a("SELECT COUNT(*) FROM course_index WHERE university_id=? AND year=?")
        raw = a("SELECT COUNT(*) FROM syllabus_raw WHERE university_id=? AND year=?")
        st = a("SELECT COUNT(*) FROM course_structured WHERE university_id=? AND year=?")
        ug = a("SELECT COUNT(*) FROM course_structured WHERE university_id=? AND year=? AND is_undergrad=1")
        gr = a("SELECT COUNT(*) FROM course_structured WHERE university_id=? AND year=? AND is_undergrad=0")
        sm = a("SELECT COUNT(*) FROM course_summary WHERE university_id=? AND year=?")
        P(f"- 一覧スイープ（この年度の収集済み科目, ユニーク）: **{idx}件**")
        P(f"- 本文取得済み: **{raw}件**")
        P(f"- 規則ベース抽出済み: {st}件")
        unk = a("SELECT COUNT(*) FROM course_structured WHERE university_id=? AND year=? "
                "AND is_undergrad IS NULL")
        P(f"- うち **学部 {ug}件** / 学府（大学院） {gr}件 / 判定不能 {unk}件")
        P(f"- AI要約済み: {sm}件")
        P("")
        P("判定不能は、学部・学府・対象学年・ナンバリングコードのいずれも空で材料が無い科目。")
        P("")

        P("## 開講時期の内訳（一覧スイープ）")
        P("")
        P("| コード | 開講時期 | 件数 |")
        P("|---|---|---|")
        for kc, (label, _) in KAIKO.items():
            n = con.execute(
                "SELECT COUNT(*) FROM course_index WHERE university_id=? AND year=? "
                "AND (kaiko_cd=? OR kaiko_cd LIKE ? OR kaiko_cd LIKE ? OR kaiko_cd LIKE ?)",
                (UID, year, kc, f"{kc},%", f"%,{kc},%", f"%,{kc}")).fetchone()[0]
            if n:
                P(f"| `{kc}` | {label} | {n} |")
        P("")

        P("## 学部別（学部のみ）")
        P("")
        P("| 学部 | 件数 |")
        P("|---|---|")
        for r in con.execute(
                "SELECT COALESCE(faculty,'(不明)') f, COUNT(*) n FROM course_structured "
                "WHERE university_id=? AND year=? AND is_undergrad=1 "
                "GROUP BY f ORDER BY n DESC", (UID, year)):
            P(f"| {r['f']} | {r['n']} |")
        P("")

        P("## ページ様式の内訳")
        P("")
        for r in con.execute("SELECT COALESCE(layout,'(不明)') l, COUNT(*) n FROM syllabus_raw "
                             "WHERE university_id=? AND year=? GROUP BY l ORDER BY n DESC",
                             (UID, year)):
            P(f"- 様式 {r['l']}: {r['n']}件")
        P("")

        P("## 成績評価の内訳（学部のみ・規則ベース抽出）")
        P("")
        for col, label in [("eval_exam", "試験"), ("eval_report", "レポート"),
                           ("eval_quiz", "小テスト"), ("eval_attend", "出席・平常点")]:
            n = a(f"SELECT COUNT(*) FROM course_structured WHERE university_id=? AND year=? "
                  f"AND is_undergrad=1 AND {col}=1")
            P(f"- {label}: {n}件 ({n/max(ug,1)*100:.0f}%)")
        P("")

        P("## 更新日付の分布（本文取得済み）")
        P("")
        P("| 年月 | 件数 |")
        P("|---|---|")
        for r in con.execute(
                "SELECT substr(updated_at,1,7) m, COUNT(*) n FROM syllabus_raw "
                "WHERE university_id=? AND year=? AND updated_at IS NOT NULL "
                "GROUP BY m ORDER BY m", (UID, year)):
            P(f"| {r['m']} | {r['n']} |")
        P("")

        P("## クロール実績")
        P("")
        P("| job | 状態 | 開始 | 対象 | 取得 | 変化 | 失敗 | 転送 | 所要 |")
        P("|---|---|---|---|---|---|---|---|---|")
        for r in con.execute("SELECT * FROM crawl_runs ORDER BY run_id"):
            gb = f"{r['bytes']/1e9:.2f} GB" if r["bytes"] else "-"
            st = r["status"] or ("ok" if r["ended_at"] else "中断または実行中")
            P(f"| {r['job']} | {st} | {r['started_at']} | {r['n_target'] or '-'} | "
              f"{r['n_fetched'] or '-'} | {r['n_changed'] if r['n_changed'] is not None else '-'} | "
              f"{r['n_error'] or 0} | {gb} | {r['note'] or '-'} |")
        P("")

        P("## DBサイズ")
        from config import DB_PATH
        P(f"- {DB_PATH}: {DB_PATH.stat().st_size/1e6:.1f} MB")

        text = "\n".join(lines)
        EXPORT_DIR.mkdir(parents=True, exist_ok=True)
        out = EXPORT_DIR / f"report-{year}.md"
        out.write_text(text, encoding="utf-8")
        print(text)
        print(f"\n-> {out}")
        return out


def csv_cell(value):
    """表計算ソフトに文字列を数式として実行させない。JSONは生値を保つ。"""
    if isinstance(value, str) and (value.startswith(("\t", "\r", "\n"))
                                   or value.lstrip().startswith(("=", "+", "-", "@"))):
        return "'" + value
    return value


def export(year=YEAR, undergrad_only=True):
    """履修選択に使える形で CSV / JSON を OneDrive 配下へ出す。"""
    with DB.session() as con:
        DB.assert_extracted(con, UID, year)
        EXPORT_DIR.mkdir(parents=True, exist_ok=True)
        where = "AND st.is_undergrad=1" if undergrad_only else ""
        cursor = con.execute(f"""
            SELECT st.course_code, st.title, st.subtitle, st.faculty, st.department,
                   st.title_suffix,
                   st.target_grade, st.credits, st.required, st.term, st.campus,
                   st.language, st.instructors, st.is_intensive, st.is_online,
                   st.eval_exam, st.eval_report, st.eval_quiz, st.eval_attend,
                   st.keywords, st.numbering,
                   (SELECT GROUP_CONCAT(cs.weekday || cs.period, ' ')
                      FROM course_slots cs
                     WHERE cs.university_id=st.university_id AND cs.year=st.year
                       AND cs.course_code=st.course_code) AS slots,
                   (SELECT sm.summary FROM course_summary sm
                     WHERE sm.university_id=st.university_id AND sm.year=st.year
                       AND sm.course_code=st.course_code
                       AND sm.source_sha256 = (SELECT sr.body_sha256 FROM syllabus_raw sr
                         WHERE sr.university_id=st.university_id AND sr.year=st.year
                           AND sr.course_code=st.course_code)) AS summary
              FROM course_structured st
             WHERE st.university_id=? AND st.year=? {where}
             ORDER BY st.faculty, st.course_code""", (UID, year))
        headers = [col[0] for col in cursor.description]
        rows = cursor.fetchall()
        tag = "undergrad" if undergrad_only else "all"
        csv_path = EXPORT_DIR / f"courses-{year}-{tag}.csv"
        with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(headers)
            for r in rows:
                w.writerow([csv_cell(v) for v in r])
        json_path = EXPORT_DIR / f"courses-{year}-{tag}.json"
        json_path.write_text(json.dumps([dict(r) for r in rows], ensure_ascii=False, indent=1),
                             encoding="utf-8")
        print(f"[export] {len(rows)}件 -> {csv_path}")
        print(f"[export] {len(rows)}件 -> {json_path}")
        return csv_path, json_path


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", type=int, default=YEAR)
    ap.add_argument("--export", action="store_true")
    ap.add_argument("--all", action="store_true")
    a = ap.parse_args()
    report(a.year)
    if a.export:
        export(a.year, not a.all)
