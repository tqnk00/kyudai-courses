"""検索サイト用のデータ生成。

course_structured / course_slots / syllabus_raw から、ページに埋め込む1個のJSONを作る。
キー名は1〜3文字に詰めてある（2,190件ぶんなのでキー名だけで数十KB変わる）。
"""
import sys, re, json, argparse
import db as DB
import extract as EX
import grading as GR
from config import (KYUSHU, EXPORT_DIR, DATA_DIR, YEAR, TERM_GROUPS, CATEGORY_OTHER,
                    FACULTY_ORDER)

UID = KYUSHU["university_id"]

# 概要として使う項目。様式ごとに名前が違うので、先に見つかったものを採る
OUTLINE_LABELS = ["授業科目の目的（日本語）", "授業概要", "授業科目に関する特筆事項",
                  "授業科目の到達目標（評価の観点）", "全体の教育目標"]
OUTLINE_MAX = 1200

# 成績評価の方法。様式によって見出しが違う
GRADING_LABELS = ["授業科目の成績評価の方法について", "試験／成績評価の方法等"]

# 授業計画は「回・テーマ・内容・事前事後学修」の4項目が繰り返す表として入っている。
# 表の最後の列見出し（事前/事後学修の内容）の下にまとめて落ちてくる。
# 連番表はこの2つのセクションにしか現れない（実測 2,557件 / 例外1件）。
# セクションを限定しないと、割合だけが並ぶ成績評価欄を授業計画として拾う
PLAN_SECTIONS = ("事前/事後学修の内容", "授業計画")
_SESSION_NO = re.compile(r"^\s*(\d{1,2})\s*$")
# 内容セルの末尾に続く事前・事後学修の行。定型文が多いので内容には混ぜない
_PREPOST = re.compile(r"^\s*(事前|事後|予習|復習|授業前|授業後|Moodle|moodle)")
THEME_MAX, DETAIL_MAX = 120, 240

def outline_of(sec):
    for lb in OUTLINE_LABELS:
        v = (sec.get(lb) or "").strip()
        if len(v) >= 20:
            v = " ".join(v.split())
            return v[:OUTLINE_MAX] + ("…" if len(v) > OUTLINE_MAX else "")
    return ""


def faculty_key(name):
    """FACULTY_ORDER の順。載っていない学部は末尾に五十音順で回す。"""
    try:
        return (0, FACULTY_ORDER.index(name), "")
    except ValueError:
        return (1, 0, name)


def grading_of(sections):
    for lb in GRADING_LABELS:
        v = (sections.get(lb) or "").strip()
        if v:
            return v
    return ""


def plan_of(sections):
    """各回の内容を [回, テーマ, 内容] の並びで返す。内容がテーマと重複するときは落とす。"""
    best, best_n = None, 0
    for lb in PLAN_SECTIONS:
        v = sections.get(lb) or ""
        n = sum(1 for ln in v.splitlines() if _SESSION_NO.match(ln))
        if n > best_n:
            best, best_n = v, n
    if not best or best_n < 2:
        return []
    blocks, cur = [], None
    for ln in (x.strip() for x in best.splitlines()):
        if not ln:
            continue
        m = _SESSION_NO.match(ln)
        if m:
            # 回番号の列が2本あって「1 / １」と続く様式がある。中身の無い区切りは繋げる
            if cur and not cur[1]:
                cur = (int(m.group(1)), cur[1])
                continue
            if cur:
                blocks.append(cur)
            cur = (int(m.group(1)), [])
        elif cur:
            cur[1].append(ln)
    if cur:
        blocks.append(cur)

    # 回番号は1から始まって増えていくはず。そうでなければ授業計画の表ではない
    nos = [no for no, _ in blocks]
    if not nos or nos[0] != 1 or any(b <= a for a, b in zip(nos, nos[1:])):
        return []

    out = []
    for no, fields in blocks:
        if not fields:
            continue
        theme = fields[0]
        # 内容セルは複数行に折り返す。末尾に続く事前/事後学修の行だけを落とし、
        # 残りは全部つないで内容にする（1行目だけ採ると本文が欠ける）
        rest = fields[1:]
        while rest and _PREPOST.match(rest[-1]):
            rest.pop()
        detail = " ".join(rest).strip()
        if detail and (detail == theme or detail.startswith(theme) or theme.startswith(detail)):
            detail = ""
        row = [no, theme[:THEME_MAX]]
        if detail:
            row.append(detail[:DETAIL_MAX])
        out.append(row)
    return out


def load_extra(path, what, empty, how):
    """data/ に置いた取り込みファイルを読む。無ければ大きく知らせて空で続ける。

    以前は黙って飛ばしていたので、置き忘れると文学部256件や事前申請155件が
    静かに消え、誰も気づけなかった。
    """
    if not path.exists():
        print(f"[site_data] !! {what} が見つかりません: {path}")
        print(f"[site_data] !! 欠けたまま生成します。作り直すには {how}")
        return empty
    return json.loads(path.read_text(encoding="utf-8"))


def latest_sweep_by_kaiko(con):
    """開講時期コードごとに、それを対象にした最後の成功した一覧巡回の開始時刻を返す。

    crawl_runs.scope は「phase1:20,23,...」の形。巡回ごとに対象の開講時期が違うので、
    全体の最新時刻ひとつで比べると、対象外だった開講時期の科目まで消えてしまう。
    """
    latest = {}
    for r in con.execute("SELECT started_at, scope FROM crawl_runs "
                         "WHERE job='sweep' AND status='ok'"):
        codes = (r["scope"] or "").partition(":")[2].split(",")
        for kc in filter(None, codes):
            if r["started_at"] > latest.get(kc, ""):
                latest[kc] = r["started_at"]
    return latest


def is_stale(kaiko_cd, last_seen, latest):
    """直近の巡回で見えなくなった科目か。

    科目は複数の開講時期コードで拾われることがある（kaiko_cd はカンマ区切りで追記される）。
    そのどれかの巡回に一度でも出ていれば残す。つまり、自分の開講時期コードを対象にした
    巡回のうち、いちばん古い「最新巡回」より前にしか見えていなければ消えたとみなす。
    """
    times = [latest[k] for k in (kaiko_cd or "").split(",") if k in latest]
    if not times or not last_seen:
        return False
    return last_seen < min(times)


def build(year=YEAR, undergrad_only=True):
    with DB.session() as con:
        DB.assert_extracted(con, UID, year)
        where = "AND st.is_undergrad=1" if undergrad_only else ""
        rows = con.execute(f"""
            SELECT st.*, sr.body, sr.layout, sr.updated_at, ci.kaiko_cd, ci.last_seen
              FROM course_structured st
              JOIN syllabus_raw sr ON sr.university_id=st.university_id
                   AND sr.year=st.year AND sr.course_code=st.course_code
              JOIN course_index ci ON ci.university_id=st.university_id
                   AND ci.year=st.year AND ci.course_code=st.course_code
             WHERE st.university_id=? AND st.year=? {where}
             ORDER BY st.course_code""", (UID, year)).fetchall()
        # 直近の一覧巡回に出てこなかった科目は載せない。シラバスが非公開になった・
        # 開講が取りやめになった科目で、DBには古い本文が残っている（2026-09-14 に5件）
        latest = latest_sweep_by_kaiko(con)
        n_all = len(rows)
        rows = [r for r in rows if not is_stale(r["kaiko_cd"], r["last_seen"], latest)]
        if n_all != len(rows):
            print(f"[site_data] 直近の巡回で見えなくなった科目 {n_all - len(rows)}件を除外")

        slots = {}
        for r in con.execute("SELECT course_code, term, weekday, period, seq FROM course_slots "
                             "WHERE university_id=? AND year=? ORDER BY course_code, seq",
                             (UID, year)):
            slots.setdefault(r["course_code"], []).append(
                [r["term"], r["weekday"], r["period"]])

        courses = []
        for r in rows:
            code = r["course_code"]
            sl = slots.get(code, [])
            sec = EX.parse_sections(r["body"], r["layout"] or "A")
            g = GR.parse(grading_of(sec))
            # 開講学期欄が空の科目が18件ある。そのままだと既定の「通年・その他」に落ち、
            # 実際に開講される学期のチップから消える。曜日時限行の学期で補う
            term = (r["term"] or "").strip()
            if not term:
                term = next((s[0] for s in sl if (s[0] or "").strip()), "")
            catr = r["category_raw"] or ""
            # 和英併記を落としただけの正規化は「シラバス表記」に出さない（実測606/946件が該当）
            if EX.normalize_category(catr) == (r["category"] or ""):
                catr = ""
            courses.append({
                "c": code,
                "t": r["title"] or "",
                "s": r["subtitle"] if r["subtitle"] and r["subtitle"] != r["title"] else "",
                "f": r["faculty"] or "",
                "g": [int(x) for x in (r["grades"] or "").split(",") if x],
                "grraw": r["target_grade"] or "",
                "cr": r["credits"],
                "rq": r["required"] or "",
                "tm": term,
                "tg": EX.term_group_of(term),
                "cat": r["category"] or "",
                # 統合・畳み込みが実際に起きた場合だけ持たせる（詳細に「シラバス表記」として出す）
                "catr": catr,
                "cp": r["campus"] or "",
                "ln": r["language"] or "",
                "i": [x for x in (r["instructors"] or "").split("\n") if x.strip()],
                "sl": sl,
                "iv": r["is_intensive"],
                "ol": r["is_online"],
                "ev": [r["eval_exam"], r["eval_report"], r["eval_quiz"], r["eval_attend"]],
                "gr": g["rows"], "gu": g["unused"], "gx": g["extra"],
                "gt": GR.total_pct(g["rows"]),
                # 行が立たない科目だけ生テキストを持たせる（実測4件）
                "ep": "" if g["rows"] else grading_of(sec),
                "pl": plan_of(sec),
                "kw": " ".join((r["keywords"] or "").split())[:120],
                "d": outline_of(sec),
                "n": r["numbering"] or "",
                "u": (r["updated_at"] or "")[:10],
            })

        # Campusmateに無い情報を data/ から混ぜる。
        # 置き忘れると機能がまるごと消えるので、無いときは黙らずに知らせる
        lit_path = DATA_DIR / f"lit-courses-{year}.json"
        extra = load_extra(lit_path, "文学部の科目", [], "python lit.py")
        have = {c["c"] for c in courses}
        added = [c for c in extra if c["c"] not in have]
        courses.extend(added)
        courses.sort(key=lambda c: c["c"])
        print(f"[site_data] 文学部 {len(added)}件を追加")

        # 基幹教育のB表（開講科目一覧）。講義コードで確実に引ける表なので、
        # 教室はこちらを優先する。事前申請の印も同時に載せる
        coreb = load_extra(DATA_DIR / f"core-b-{year}.json", "基幹教育B表", {},
                           "python run.py rooms")
        n_core = n_apply = 0
        for c in courses:
            got = coreb.get(c["c"])
            if not got:
                continue
            if got["room"]:
                c["rm"] = got["room"]
                c["rmu"] = got["source_url"]
                n_core += 1
            if got["klass"]:
                c["kl"] = got["klass"]
            # 印が付いた入学年度だけを残す。年度で扱いが違う科目がある
            ap = {k: v for k, v in (got["apply"] or {}).items() if v}
            if ap:
                c["ap"] = ap
                c["apu"] = got["source_url"]
                n_apply += 1
        print(f"[site_data] 基幹教育B表 教室 {n_core}件 / 事前申請 {n_apply}件")

        # 各学部の時間割PDFから拾った教室。Campusmateの本文には
        # 教室が7.9%しか入っていないため、PDF側が主な出どころになる
        rooms = load_extra(DATA_DIR / f"timetable-rooms-{year}.json", "各学部の教室", {},
                           "python run.py rooms")
        rooms.pop("_meta", None)          # 抽出時の統計。科目ではない
        n_room = 0
        for c in courses:
            got = rooms.get(c["c"])
            if got and not c.get("rm"):
                c["rm"] = got["room"]
                c["rmu"] = got["source_url"]
                n_room += 1
        print(f"[site_data] 教室 {n_room}件を追加")

        # 時間割には載っているのにCampusmateに無い科目（法学部などで多い）
        missing = load_extra(DATA_DIR / f"timetable-missing-{year}.json",
                             "時間割のみの科目", [], "python run.py rooms")
        print(f"[site_data] 時間割のみの科目 {len(missing)}件")

        meta = {
            "year": year,
            "undergrad_only": undergrad_only,
            "generated_at": con.execute("SELECT datetime('now','localtime')").fetchone()[0],
            "n": len(courses),
            "faculties": sorted({c["f"] for c in courses if c["f"]}, key=faculty_key),
            # {code} だけ差し替えれば公式のシラバスページに飛べる形にしておく
            "detail_url": KYUSHU["detail_url"].format(
                year=year, code="{code}", crclumcd=KYUSHU["crclumcd"]),
            "campuses": sorted({c["cp"] for c in courses if c["cp"] and c["cp"] != "＿"}),
            "category_other": CATEGORY_OTHER,
            "grading_methods": GR.METHODS,
            # 教室を持つ科目の総数。文学部ぶんも含む
            "n_room": sum(1 for c in courses if c.get("rm")),
            "n_apply": n_apply,
            # シラバスが無いので科目としては出せないが、存在は知らせる
            "missing": missing,
        }
        # 曜日時限行が持つ学期をグループへ引くための表。画面はこれで、
        # 通年科目の前期コマと後期コマを開講期チップごとに描き分ける
        tgmap = {}
        for c in courses:
            for s in c["sl"]:
                if s[0] and s[0] not in tgmap:
                    tgmap[s[0]] = EX.term_group_of(s[0])
            if c["tm"] and c["tm"] not in tgmap:
                tgmap[c["tm"]] = c["tg"]
        meta["term_group_map"] = tgmap
        used = {c["tg"] for c in courses} | set(tgmap.values())
        meta["term_groups"] = [g for g, _ in TERM_GROUPS if g in used]
        data = {"meta": meta, "courses": courses}
        EXPORT_DIR.mkdir(parents=True, exist_ok=True)
        out = EXPORT_DIR / f"site-data-{year}.json"
        out.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")),
                       encoding="utf-8")
        print(f"[site_data] {len(courses)}件 -> {out} ({out.stat().st_size/1e6:.2f} MB)")
        return out


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", type=int, default=YEAR)
    ap.add_argument("--all", action="store_true")
    a = ap.parse_args()
    build(a.year, not a.all)
