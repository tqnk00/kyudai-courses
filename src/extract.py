"""規則ベース抽出（仕様書2.1「成績評価は…AIに投げないこと」/ 実装順序4・5）。

syllabus_raw.body だけを入力にする。再抽出でネットワークアクセスは発生しない。
"""
import re, sys, argparse
import db as DB
import grading as GR
from config import (KYUSHU, YEAR, GRADUATE_MARKERS, DEPARTMENT_CODES,
                    CATEGORY_ALIASES, CATEGORY_MERGES,
                    CATEGORY_SPLIT_FACULTIES, CATEGORY_MIN_COUNT,
                    CATEGORY_OTHER, TERM_GROUPS)

UID = KYUSHU["university_id"]

# 各様式のラベル集合。行がこのいずれかと完全一致したら新しい項目の始まりとみなす
LABELS_A = ["科目名称", "講義題目", "科目ナンバリング・コード", "担当教員", "更新日付",
    "授業科目区分", "学部カテゴリ", "使用言語", "対象学部等", "対象学年", "必修選択",
    "単位数", "開講年度", "開講学期", "曜日時限", "教室", "開講地区",
    "授業科目に関する特筆事項", "授業科目の目的・目標・履修条件について",
    "授業科目の目的（日本語）", "授業科目の目的（英語）", "キーワード", "履修条件",
    "学位プログラムの学修目標", "授業科目の到達目標（評価の観点）",
    "授業科目の実施方法について", "授業の方法", "遠隔授業", "参考書等", "教科書",
    "授業計画", "授業のテーマ", "授業の内容（90分授業＝2時間）", "事前/事後学修の内容",
    "授業科目の成績評価の方法について", "授業科目に関する学習相談について",
    "担当教員による学習相談", "備考"]

LABELS_B = ["講義科目名", "科目ナンバリングコード", "講義題目", "授業科目区分", "開講年度",
    "開講学期", "曜日時限", "必修選択", "単位数", "担当教員", "開講学部・学府",
    "対象学部等", "対象学年", "開講地区", "その他\n（自由記述欄）", "履修条件", "授業概要",
    "授業形態\n（項目）", "授業形態\n（内容）", "使用する教材等", "全体の教育目標",
    "個別の教育目標", "授業計画", "キーワード", "授業の進め方", "テキスト", "参考書",
    "学習相談", "試験／成績評価の方法等", "その他", "添付ファイル", "更新日付"]

# 様式C: 医学部の統合科目（ユニット）。構成科目の一覧と学修目標だけを持つ
LABELS_C = ["授業科目名", "講義題目", "科目ナンバリング・コード", "担当教員", "更新日付",
    "授業科目区分", "対象学部等", "対象学年", "必修選択", "単位数", "開講年度",
    "開講学期", "曜日時限", "開講地区", "授業概要", "ユニット名", "No.", "科目名称",
    "備考", "学修目標", "ルーブリック", "添付ファイル"]

LABELS = {"A": LABELS_A, "B": LABELS_B, "C": LABELS_C}

# 様式ごとの読み替え。左が共通名、右が各様式でのラベル
MAP = {
    "A": {"title": "科目名称", "subtitle": "講義題目", "numbering": "科目ナンバリング・コード",
          "instructors": "担当教員", "category": "授業科目区分", "faculty_cat": "学部カテゴリ",
          "language": "使用言語", "faculty": "対象学部等", "target_grade": "対象学年",
          "required": "必修選択", "credits": "単位数", "term": "開講学期",
          "slots": "曜日時限", "room": "教室", "campus": "開講地区",
          "keywords": "キーワード", "prereq": "履修条件",
          "goals": "授業科目の到達目標（評価の観点）", "method": "授業の方法",
          "remote": "遠隔授業", "grading": "授業科目の成績評価の方法について"},
    "B": {"title": "講義科目名", "subtitle": "講義題目", "numbering": "科目ナンバリングコード",
          "instructors": "担当教員", "category": "授業科目区分", "faculty_cat": "開講学部・学府",
          "language": None, "faculty": "対象学部等", "target_grade": "対象学年",
          "required": "必修選択", "credits": "単位数", "term": "開講学期",
          "slots": "曜日時限", "room": None, "campus": "開講地区",
          "keywords": "キーワード", "prereq": "履修条件",
          "goals": "全体の教育目標", "method": "授業の進め方",
          "remote": None, "grading": "試験／成績評価の方法等"},
    "C": {"title": "授業科目名", "subtitle": "講義題目", "numbering": "科目ナンバリング・コード",
          "instructors": "担当教員", "category": "授業科目区分", "faculty_cat": None,
          "language": None, "faculty": "対象学部等", "target_grade": "対象学年",
          "required": "必修選択", "credits": "単位数", "term": "開講学期",
          "slots": "曜日時限", "room": None, "campus": "開講地区",
          "keywords": None, "prereq": None,
          "goals": "学修目標", "method": None,
          "remote": None, "grading": None},
}

EMPTY = {"", "-", "－", "ー", "―", " ", "※"}


def parse_sections(body: str, layout: str) -> dict:
    labels = set(LABELS.get(layout, LABELS_A))
    # 改行を含むラベル（'その他\n（自由記述欄）'）は先に1行へ潰す
    for lb in [l for l in labels if "\n" in l]:
        body = body.replace(lb, lb.replace("\n", ""))
    labels = {l.replace("\n", "") for l in labels}
    out, cur, buf = {}, None, []
    for line in body.split("\n"):
        s = line.strip()
        if s in labels:
            if cur is not None:
                out.setdefault(cur, "\n".join(buf).strip())
            cur, buf = s, []
        elif cur is not None:
            buf.append(s)
    if cur is not None:
        out.setdefault(cur, "\n".join(buf).strip())
    return {k: ("" if v in EMPTY else v) for k, v in out.items()}


def get(sec, layout, key):
    lb = MAP[layout].get(key)
    if not lb:
        return ""
    return sec.get(lb.replace("\n", ""), "").strip()


# ---- 個別項目 -------------------------------------------------------------

_DEPT = re.compile(r"[（(]([^（）()]{1,6})[）)]\s*$")


# 括弧書きでも学科指定でないもの。担当教員名（山下先生）やクラス指定を弾く
_NOT_DEPT = re.compile(r"(先生|教員|教授|講師|クラス|再履|再履修|集中|限)$")


def title_suffix_of(title: str) -> str:
    """講義名の末尾括弧の生値を返す。学科指定・副題・英語表記が混在する。

    担当教員名（山下先生）やクラス指定は弾く。Phase 2 の名寄せで使えるよう残しておく。
    """
    m = _DEPT.search((title or "").strip())
    if not m or _NOT_DEPT.search(m.group(1)):
        return ""
    return m.group(1)


def department_of(title: str) -> str:
    """学科指定（仕様書2.1）。例: マクロ経済学Ⅰ（経経）

    括弧書きの大半は副題なので、既知の学科略号に一致したものだけを学科とみなす。
    """
    sfx = title_suffix_of(title)
    return sfx if sfx in DEPARTMENT_CODES else ""


_CREDIT = re.compile(r"(\d+(?:\.\d+)?)")


def credits_of(s):
    m = _CREDIT.search(s or "")
    return float(m.group(1)) if m else None


_SLOT = re.compile(r"^(\S+?)\s+(月|火|水|木|金|土|日|その他)(?:曜日)?\s+(\S+?)(?:時限)?$")
_KANSUJI = {"１": "1", "２": "2", "３": "3", "４": "4",
            "５": "5", "６": "6", "７": "7", "８": "8", "９": "9"}


def parse_slots(raw: str):
    """'後期 月曜日 ２時限' の行を (term, weekday, period) に割る。2限連続は2行になる。"""
    out = []
    for line in (raw or "").split("\n"):
        s = re.sub(r"\s+", " ", line.strip())
        if not s:
            continue
        m = _SLOT.match(s)
        if m:
            term, wd, per = m.group(1), m.group(2), m.group(3)
            per = "".join(_KANSUJI.get(ch, ch) for ch in per)
            out.append((term, wd, per))
        else:
            parts = s.split(" ")
            out.append((parts[0] if parts else "", "その他", "その他"))
    return out


# 授業科目区分は「専攻教育科目 Specialized Education」「アプローチ科目/Approach Subjects」
# のように和英が並記される。英語表記の側を落として1つに寄せる
# 英語表記の始まり。区切りの空白・スラッシュ・開き括弧ごと切る
_EN_START = re.compile(r"[\s/／]*[（(]?[A-Za-z]")
_HAS_JA = re.compile(r"[ぁ-んァ-ヶ一-龥]")
_TRAIL_PUNCT = re.compile(r"[\s:：/／、,\-（(]+$")
CATEGORY_MAX = 40
_TERM_GROUP = {t: g for g, terms in TERM_GROUPS for t in terms}
OTHER_TERM_GROUP = TERM_GROUPS[-1][0]


def normalize_category(raw: str) -> str:
    """授業科目区分の表記ゆれを寄せる。例: '専攻教育科目 Specialized Education' -> '専攻教育科目'"""
    v = " ".join((raw or "").split())
    if not v:
        return ""
    if v in CATEGORY_ALIASES:
        return CATEGORY_ALIASES[v]
    # 最初の英語表記の手前で切る。'専攻教育科目 Specialized Education' も
    # '専門教育科目（Major subject）' も 'アプローチ科目/Approach Subjects' も同じ扱いになる。
    # 英語だけの区分（'JTW Core Courses' など）は切ると消えるので、日本語が前にある時だけ切る
    m = _EN_START.search(v)
    if m and _HAS_JA.search(v[:m.start()]):
        v = v[:m.start()]
    v = _TRAIL_PUNCT.sub("", v).strip()
    return v[:CATEGORY_MAX] if len(v) > CATEGORY_MAX else v


# 「（経済工学科）選択必修科目／（経済・経営学科）自由選択科目」→「選択必修科目」
_LEAD_DEPT = re.compile(r"^[（(][^）)]*[）)]\s*")


def merge_category(cat: str, faculty: str) -> str:
    """正規化済みの区分をさらに統合する。まとめ先が空文字なら区分なしにする。"""
    if not cat:
        return ""
    if faculty in CATEGORY_SPLIT_FACULTIES:
        cat = cat.split("／")[0].split("/")[0].strip()
        cat = _LEAD_DEPT.sub("", cat).strip()
        if cat.startswith("GProE"):
            cat = cat[len("GProE"):].strip()
    return CATEGORY_MERGES.get(cat, cat)


def term_group_of(term: str) -> str:
    return _TERM_GROUP.get((term or "").strip(), OTHER_TERM_GROUP)


_ZEN_DIGIT = str.maketrans("１２３４５６７８９０", "1234567890")
_ENROLL_YEAR = re.compile(r"\d{4}\s*年")          # 「2023年以前入学者」の入学年度
# 医歯薬は6年制。学年は1〜6で扱う
_OR_MORE = re.compile(r"([1-6])\s*年生?以上")
_RANGE_SEP = re.compile(r"[-~〜～–—]|\.\.")   # 「1年～4年」の区切り。間に注釈が挟まる
MAX_GRADE = 6
ALL_GRADES = "1,2,3,4"   # 既定は4年制。5・6年次は生値に現れたときだけ付く
# 6年制。ここの科目は「2年生以上」「全学年」が6年次まで届く
SIX_YEAR_FACULTIES = ("医学部医学科", "歯学部", "薬学部")


def top_grade_of(faculty: str) -> int:
    return MAX_GRADE if any(f in (faculty or "") for f in SIX_YEAR_FACULTIES) else 4


def grades_of(target_grade: str, faculty: str = "") -> str:
    """対象学年の生値を '1,2,3' の形に正規化する。

    生値は67通りある（`１年生` `3,4年` `学部2年（The 2nd year）` `２年生以上` `ZZ` …）。
    判定できないもの・全学年は全学年扱いにする。絞り込みで取りこぼすより出しすぎる方が安全。
    """
    cap = top_grade_of(faculty)
    s = (target_grade or "").translate(_ZEN_DIGIT)
    s = _ENROLL_YEAR.sub(" ", s)               # 入学年度を学年と読み違えないよう先に消す
    if not s.strip() or s.strip() in {"ZZ", "-", "全学年"}:
        return ",".join(str(g) for g in range(1, cap + 1))
    m = _OR_MORE.search(s)
    if m:
        top = MAX_GRADE if int(m.group(1)) > 4 else cap
        return ",".join(str(g) for g in range(int(m.group(1)), top + 1))
    got = {int(d) for d in re.findall(r"[1-6]", s)}
    # 「学部1年（The 1st year）～学部4年（The 4th year）」のように区切りと数字が離れる。
    # 区切りがあれば最小〜最大を埋める
    if len(got) > 1 and _RANGE_SEP.search(s):
        got = set(range(min(got), max(got) + 1))
    if not got:
        return ",".join(str(g) for g in range(1, cap + 1))
    return ",".join(str(g) for g in sorted(got))


# 成績評価は半構造化。ラベル行の有無で判定する（AIには投げない）
EVAL_PATTERNS = {
    "eval_exam":   r"(定期試験|期末試験|中間試験|試験|テスト|筆記)",
    "eval_report": r"(レポート|課題|提出物|作品|論文)",
    "eval_quiz":   r"(小テスト|小試験|クイズ|確認テスト)",
    "eval_attend": r"(出席|受講態度|授業への貢献度|平常点|参加度|貢献)",
}


def eval_flags(grading: str):
    g = grading or ""
    parsed = GR.parse(g)
    # 半構造化された欄は有効な方法のみ数える。「定期試験\n実施しない」を除外。
    if parsed["rows"] or parsed["unused"]:
        active = "\n".join(row[0] for row in parsed["rows"] if row[1] != 0)
        flags = {k: int(bool(re.search(pat, re.sub(r"小テスト|小試験|確認テスト", "", active)
                                       if k == "eval_exam" else active)))
                 for k, pat in EVAL_PATTERNS.items()}
        return flags, g[:2000]
    # 自由記述は節単位で、明示的な非実施を除く。条件の推測はしない。
    g = "\n".join(part for part in re.split(r"[。；;\n]", g)
                  if not re.search(r"(?:実施しない|行わない|評価しない|課さない|なし|無し|ありません)", part))
    flags = {}
    for k, pat in EVAL_PATTERNS.items():
        if k == "eval_exam":
            # 「小テスト」を「試験」で拾わないよう、小テストを先に除く
            g2 = re.sub(r"小テスト|小試験|確認テスト", "", g)
            flags[k] = 1 if re.search(pat, g2) else 0
        else:
            flags[k] = 1 if re.search(pat, g) else 0
    other = (grading or "")[:2000]
    return flags, other


ONLINE = re.compile(r"(オンライン|遠隔|Zoom|ZOOM|zoom|Teams|オンデマンド)")
INTENSIVE = re.compile(r"集中")


# 科目ナンバリングのレベル桁（例 KED-LCB2124W の '2'）。5以上が大学院。
# 学部/学府がはっきりしている科目で照合したところ例外なく一致した（実測 n=2531）
_LEVEL = re.compile(r"^[A-Z]{2,4}-[A-Z]{2,4}(\d)")
_GRAD_GRADE = re.compile(r"(修士|博士|大学院|専門職)")


def undergrad_flag(faculty: str, faculty_cat: str, category: str,
                   target_grade: str = "", numbering: str = ""):
    """1=学部 / 0=大学院 / None=判定不能。

    材料が一つも無い科目が283件ある（多くは卓越大学院プログラムの科目で、
    学部・学府も学年もナンバリングも空）。学部と決めつけずNULLで分ける。
    """
    blob = " ".join(x or "" for x in (faculty, faculty_cat, category))
    if any(m in blob for m in GRADUATE_MARKERS):
        return 0
    if _GRAD_GRADE.search(target_grade or ""):
        return 0
    m = _LEVEL.match(numbering or "")
    if m:
        return 0 if int(m.group(1)) >= 5 else 1
    if blob.strip():
        return 1
    return None


# ---- 本体 -----------------------------------------------------------------

def build_row(code, year, body, layout, body_sha256=None):
    layout = layout or "A"
    sec = parse_sections(body, layout)
    g = lambda k: get(sec, layout, k)
    title = g("title")
    faculty, faculty_cat, category = g("faculty"), g("faculty_cat"), g("category")
    flags, other = eval_flags(g("grading"))
    term, slots_raw = g("term"), g("slots")
    target_grade, numbering = g("target_grade"), g("numbering")
    blob = " ".join([term, slots_raw, g("remote"), g("method"), category])
    row = dict(
        university_id=UID, year=year, course_code=code,
        numbering=numbering or None, title=title or None,
        subtitle=g("subtitle") or None,
        # 対象学部等 は実際には学年が入っていることが多い（実測）。
        # 学部名として信頼できるのは A:学部カテゴリ / B:開講学部・学府 のほう。
        faculty=(faculty_cat or faculty or None),
        faculty_raw=faculty or None,
        department=department_of(title) or None,
        title_suffix=title_suffix_of(title) or None,
        target_grade=target_grade or None,
        grades=grades_of(target_grade, faculty_cat or faculty),
        category_raw=category or None,
        category=merge_category(normalize_category(category), faculty_cat or faculty) or None,
        term_group=term_group_of(term),
        credits=credits_of(g("credits")),
        required=g("required") or None,
        term=term or None, language=g("language") or None,
        campus=g("campus") or None,
        instructors=g("instructors") or None,
        is_undergrad=undergrad_flag(faculty, faculty_cat, category,
                                    target_grade, numbering),
        is_intensive=1 if INTENSIVE.search(blob) else 0,
        is_online=1 if ONLINE.search(blob) else 0,
        eval_other=other or None,
        prereq=g("prereq") or None, keywords=g("keywords") or None,
        goals=g("goals") or None,
        plan=sec.get("授業計画", "") or None,
        extracted_sha256=body_sha256,
        **flags,
    )
    return row, parse_slots(slots_raw)


COLS = ["university_id", "year", "course_code", "numbering", "title", "subtitle", "faculty",
        "faculty_raw", "department", "title_suffix", "target_grade", "grades", "category", "category_raw", "term_group", "credits", "required", "term", "language", "campus",
        "instructors", "is_undergrad", "is_intensive", "is_online", "eval_exam", "eval_report",
        "eval_quiz", "eval_attend", "eval_other", "prereq", "keywords", "goals", "plan",
        "extracted_sha256"]


def fold_rare_categories(con, year):
    """件数の少ない区分を「その他」に畳む。区分は全部で60ほどあり、
    そのうち十数個は1〜5件しかない。絞り込みの選択肢としては長すぎる。"""
    rare = [r[0] for r in con.execute(
        "SELECT category FROM course_structured WHERE university_id=? AND year=? "
        "AND is_undergrad=1 AND category IS NOT NULL "
        "GROUP BY category HAVING COUNT(*) < ?", (UID, year, CATEGORY_MIN_COUNT))]
    if not rare:
        return 0
    marks = ",".join("?" * len(rare))
    n = con.execute(
        f"UPDATE course_structured SET category=? WHERE university_id=? AND year=? "
        f"AND is_undergrad=1 AND category IN ({marks})",
        [CATEGORY_OTHER, UID, year] + rare).rowcount
    print(f"[extract] 少数の区分 {len(rare)}種 / {n}件 を「{CATEGORY_OTHER}」に畳んだ")
    return n


def run(year=YEAR):
    with DB.session() as con:
        rows = con.execute("SELECT course_code, body, layout, body_sha256 FROM syllabus_raw "
                           "WHERE university_id=? AND year=?", (UID, year)).fetchall()
        con.execute("DELETE FROM course_structured WHERE university_id=? AND year=?", (UID, year))
        con.execute("DELETE FROM course_slots WHERE university_id=? AND year=?", (UID, year))
        n_slots = 0
        for r in rows:
            row, slots = build_row(r["course_code"], year, r["body"], r["layout"],
                                   r["body_sha256"])
            con.execute(f"INSERT INTO course_structured ({','.join(COLS)}) "
                        f"VALUES ({','.join('?' * len(COLS))})", [row[c] for c in COLS])
            for i, (t, wd, per) in enumerate(slots, 1):
                con.execute("INSERT INTO course_slots (university_id,year,course_code,term,"
                            "weekday,period,seq) VALUES (?,?,?,?,?,?,?)",
                            (UID, year, r["course_code"], t, wd, per, i))
                n_slots += 1
        fold_rare_categories(con, year)
        con.commit()
        cnt = lambda w: con.execute(
            f"SELECT COUNT(*) FROM course_structured WHERE university_id=? AND year=? AND {w}",
            (UID, year)).fetchone()[0]
        ug, gr, unk = cnt("is_undergrad=1"), cnt("is_undergrad=0"), cnt("is_undergrad IS NULL")
        print(f"[extract] {len(rows)}件 -> course_structured / slots {n_slots}行")
        print(f"[extract] 学部 {ug}件 / 大学院 {gr}件 / 判定不能 {unk}件")
        return len(rows), ug


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", type=int, default=YEAR)
    run(ap.parse_args().year)
