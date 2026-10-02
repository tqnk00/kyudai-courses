"""検索サイト用のデータ生成。

course_structured / course_slots / syllabus_raw から、ページに埋め込むJSONと、
詳細を開いたときに読む分割ファイル（details-年度/NN.json）を作る。
キー名は1〜3文字に詰めてある（数千件ぶんなのでキー名だけで数十KB変わる）。

科目1件のキー（KEYS）。画面 site_template.html も同じ名前で読む。
  ページ本体に入るもの
    c   講義コード              t   科目名                s   副題
    f   学部                    g   対象学年（数の並び）   cr  単位数
    tm  開講学期（原文）        tg  開講期のまとまり（前期・春学期…）
    cat 科目区分                cp  キャンパス             i   担当教員（並び）
    sl  コマの並び [学期, 曜日, 時限]                      iv  集中講義か  ol 遠隔ありか
    kw  キーワード              n   ナンバリング           su  シラバスのURL（文学部だけ）
    rm  教室                    kl  担当クラス             ap  事前申請の印 {入学年度: 印}
    dp  学科・コース・分野（並び。対象が決まっている科目だけ）
  詳細の分割ファイルに入るもの（DETAIL_DEFAULTS。画面は詳細を開いたときに読む）
    d   概要                    pl  授業計画 [回, テーマ, 内容]
    gr  成績評価の行 [方法, 割合, 補足, 印]   gu 実施しない方法   gx どの方法にも付かない注意書き
    gt  割合の合計              ep  成績評価の原文（行に分けられなかったとき）
    ev  評価の有無 [試験, レポート, 小テスト, 出席]
    grraw 対象学年の原文        rq  必修選択               catr 科目区分の原文
    ln  使用言語                rmu 教室の出典URL          apu  事前申請の出典URL
"""
import sys, re, json, argparse, collections, unicodedata
import db as DB
import extract as EX
import grading as GR
import class_days
from config import (KYUSHU, EXPORT_DIR, DATA_DIR, YEAR, TERM_GROUPS, CATEGORY_OTHER,
                    FACULTY_ORDER, SPRING_GROUPS, AUTUMN_GROUPS)

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

# 詳細だけで使う項目と、その既定値。画面は既定値を補ってから描く
DETAIL_DEFAULTS = {
    "d": "", "pl": [], "gr": [], "gu": [], "gx": [], "gt": None, "ep": "",
    "ev": [0, 0, 0, 0], "grraw": "", "rq": "", "catr": "", "ln": "", "rmu": "", "apu": "",
}
# 詳細ファイルの分割数。1つあたり gzip で数十KBになる。画面の shardOf と同じ計算で振り分ける
DETAIL_SHARDS = 64


def shard_of(code):
    """FNV-1a。科目コードは連番に近く、単純な掛け算だと分割先が偏る（最小5件・最大183件）"""
    h = 2166136261
    for ch in code:
        h = ((h ^ ord(ch)) * 16777619) & 0xFFFFFFFF
    return h % DETAIL_SHARDS


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

# 対象学部等の原文から、学科・コース・分野・専攻の名前を拾う。
#   「電気情報工学科（EC） / Department of ...」→ 電気情報工学科
#   「融合基礎工学科（機械電気コース）」→ 融合基礎工学科, 機械電気コース
#   「医学部保健学科看護学専攻（...）」→ 看護学専攻（学部名と同じ部分は落とす）
#   「Ⅰ群 / Group Ⅰ（EE)」→ Ⅰ群（工学部の1・2年次の区分）
_DEPT = re.compile(r"[ⅠⅡⅢⅣⅤⅥ]群|[一-龥ぁ-んァ-ヶー－-]+?(?:学科|コース|分野|専攻)")
# 件数がこれ未満の名前は選択肢にしない。表記ゆれや記入ミスが選択肢に並ぶのを防ぐ
DEPT_MIN_COUNT = 5


def departments_of(faculty, raw):
    out = []
    for t in _DEPT.findall(raw or ""):
        t = unicodedata.normalize("NFKC", t) if "群" not in t else t   # Ⅰ群 はローマ数字のまま
        t = re.sub(r"[－-]", "ー", t)              # 「コ－ス」の表記ゆれ
        t = re.sub(r"^.*?学部", "", t)             # 「薬学部臨床薬学科」→「臨床薬学科」
        if not t or faculty.endswith(t) or t == "各コース" or t in out:
            continue
        out.append(t)
    return out


def prune_departments(courses):
    """学部の中で件数の少ない学科名を落とし、空になった科目からは dp を外す。"""
    count = collections.Counter((c["f"], d) for c in courses for d in c.get("dp", ()))
    for c in courses:
        if "dp" in c:
            c["dp"] = [d for d in c["dp"] if count[(c["f"], d)] >= DEPT_MIN_COUNT]
            if not c["dp"]:
                del c["dp"]


# ---- build() の各段 ---------------------------------------------------------
# build() は下の関数を順に呼ぶだけ。科目1件の形は KEYS（このファイルの先頭）を参照

def load_rows(con, year, undergrad_only):
    """DBから科目の行と、科目コードごとのコマを読む。直近の巡回で消えた科目は除く。"""
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
    return rows, slots


def campusmate_course(r, sl):
    """Campusmate の1科目（DBの1行とコマの並び）を、サイト用の形にする。"""
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
    course = {
        "c": r["course_code"],
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
    }
    dp = departments_of(r["faculty"] or "", r["faculty_raw"])
    if dp:
        course["dp"] = dp
    return course


def lit_course(c):
    """文学部の1科目（lit.py が取ってきた dict）を、サイト用の形にする。取れない項目は空。

    campusmate_course と同じキーを返す。サイト用の形を決める場所をこのファイルに集めてある。
    """
    faculty = "文学部"
    # ルーブリックの記号は読めないので落とす。評価方法の名前だけ残す
    grading_text = re.sub(r"U_[A-Za-z0-9\-]+\s*\[[^\]]*\]|観点→成績評価方法↓?|→|↓", " ",
                          c.get("grading") or "")
    grading_text = re.sub(r"\s+", " ", grading_text).strip()
    flags, _ = EX.eval_flags(grading_text)
    g = GR.parse(grading_text)
    # 文学部は「秋クォータ」表記。Campusmate 側の「秋学期」に寄せてチップを共通にする
    term = (c.get("term") or "").replace("クォータ", "学期")
    room = c.get("room") or ""
    course = {
        "c": c["course_code"],
        "t": c.get("title") or "",
        "s": c.get("subtitle") or "",
        "f": faculty,
        "g": [int(x) for x in EX.grades_of(c.get("target_grade") or "", faculty).split(",") if x],
        "grraw": c.get("target_grade") or "",
        "cr": c.get("credits"),
        "rq": c.get("required") or "",
        "tm": term,
        "tg": EX.term_group_of(term),
        "cat": c.get("category") or "",
        "catr": "",
        "cp": "伊都地区" if room or c.get("course_name") else "",
        "ln": c.get("language") or "",
        "i": [x for x in [(c.get("instructors") or "").strip()] if x],
        "sl": [[term, d, p] for _t, d, p in (c.get("slots") or [])],
        "iv": 1 if "集中" in term else 0,
        "ol": 0,
        "ev": [flags["eval_exam"], flags["eval_report"], flags["eval_quiz"], flags["eval_attend"]],
        "gr": g["rows"], "gu": g["unused"], "gx": g["extra"],
        "gt": GR.total_pct(g["rows"]),
        "ep": "" if g["rows"] else grading_text,
        "pl": [[n, t] for n, t in (c.get("plan") or [])],
        "kw": " ".join((c.get("keywords") or "").split())[:120],
        "d": " ".join((c.get("outline") or "").split())[:1200],
        "n": c.get("numbering") or "",
        # 文学部は科目ごとにURLが違うので、ここに持たせる
        "su": c.get("url") or "",
        "rm": room,
    }
    # 専門分野（心理・哲学…）。時間割ページの欄に入っている
    if c.get("dept"):
        course["dp"] = [c["dept"]]
    return course


def merge_lit(courses, year):
    """文学部の科目を足す。Campusmate に同じコードがあればそちらを残す。"""
    extra = load_extra(DATA_DIR / f"lit-courses-{year}.json", "文学部の科目", [], "python lit.py")
    have = {c["c"] for c in courses}
    added = [c for c in extra if c["c"] not in have]
    for c in added:
        c.pop("u", None)        # 古い取り込みファイルに残っている更新日。画面では使わない
    courses.extend(added)
    courses.sort(key=lambda c: c["c"])
    print(f"[site_data] 文学部 {len(added)}件を追加")


def merge_core_b(courses, year):
    """基幹教育のB表（開講科目一覧）から、教室・担当クラス・事前申請の印を載せる。

    講義コードで確実に引ける表なので、教室はこちらを優先する。
    """
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


def merge_rooms(courses, year):
    """各学部の時間割PDFから拾った教室を、まだ教室の無い科目に載せる。

    Campusmateの本文には教室が7.9%しか入っていないため、PDF側が主な出どころになる。
    """
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
    print(f"[site_data] 教室 {n_room}件を追加（教室が分かる科目は計 "
          f"{sum(1 for c in courses if c.get('rm'))}件）")


def build_meta(con, courses, year, undergrad_only):
    """画面が使う設定と集計。選択肢（学部・キャンパス・開講期）もここで決める。"""
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
    # 前期側・後期側の開講期。画面の「今の学期」と時間割ページの切り替えが使う
    meta["seasons"] = {"spring": [g for g, _ in TERM_GROUPS if g in SPRING_GROUPS],
                       "autumn": [g for g, _ in TERM_GROUPS if g in AUTUMN_GROUPS]}
    # 授業日（祝日・休業を除き、振替授業日を入れた日付の並び）。カレンダーへの書き出し用
    meta["calendar"] = class_days.for_site(year)
    return meta


def split_details(courses, meta, year):
    """詳細でしか使わない項目を科目から抜き、分割ファイルごとの dict にまとめて返す。

    授業計画・概要・成績評価だけで全体の8割（16MBのうち13MB）あり、
    一覧と時間割を出すだけのために全部を読ませていた。
    """
    meta["detail_dir"] = f"details-{year}"
    meta["detail_shards"] = DETAIL_SHARDS
    meta["detail_defaults"] = DETAIL_DEFAULTS
    shards = [{} for _ in range(DETAIL_SHARDS)]
    for c in courses:
        det = {}
        for k, empty in DETAIL_DEFAULTS.items():
            v = c.pop(k, empty)
            if v != empty:           # 既定値の項目は書かない（ファイルを小さくする）
                det[k] = v
        if det:
            shards[shard_of(c["c"])][c["c"]] = det
    return shards


def write_out(meta, courses, shards, year):
    """ページに埋め込むJSONと、詳細の分割ファイルを EXPORT_DIR に書き出す。"""
    def dump(path, obj):
        path.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")),
                        encoding="utf-8")
        return path.stat().st_size

    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    out = EXPORT_DIR / f"site-data-{year}.json"
    size = dump(out, {"meta": meta, "courses": courses})
    # 前回の分割数が違っていても古いファイルが残らないよう、作り直す
    det_dir = EXPORT_DIR / meta["detail_dir"]
    det_dir.mkdir(parents=True, exist_ok=True)
    for old in det_dir.glob("*.json"):
        old.unlink()
    det_size = sum(dump(det_dir / f"{n:02d}.json", shard) for n, shard in enumerate(shards))
    print(f"[site_data] {len(courses)}件 -> {out} ({size/1e6:.2f} MB)"
          f" ／ 詳細 {DETAIL_SHARDS}分割 -> {det_dir} ({det_size/1e6:.2f} MB)")
    return out


def build(year=YEAR, undergrad_only=True):
    """サイト用のデータを作る。流れは上から順に読めば分かるようにしてある。"""
    with DB.session() as con:
        DB.assert_extracted(con, UID, year)
        rows, slots = load_rows(con, year, undergrad_only)
        courses = [campusmate_course(r, slots.get(r["course_code"], [])) for r in rows]
        # Campusmateに無い情報を data/ から混ぜる。
        # 置き忘れると機能がまるごと消えるので、無いときは黙らずに知らせる（load_extra）
        merge_lit(courses, year)
        merge_core_b(courses, year)
        merge_rooms(courses, year)
        prune_departments(courses)
        meta = build_meta(con, courses, year, undergrad_only)
        shards = split_details(courses, meta, year)
        return write_out(meta, courses, shards, year)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", type=int, default=YEAR)
    ap.add_argument("--all", action="store_true")
    a = ap.parse_args()
    build(a.year, not a.all)
