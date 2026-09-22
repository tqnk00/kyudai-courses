"""文学部（人文学部）シラバスの取得。

Campusmate ではなく文学部独自のCGI（Shift_JIS）で、シラバスと時間割が同じページに載る。
  https://www3.lit.kyushu-u.ac.jp/~syllabus/cgi-bin/table-even.cgi

2段構えで取る。
  1. 時間割ページ … 曜日時限・教室・科目IDの一覧。コマの情報はここにしかない
  2. 個別ページ   … 科目ごとのシラバス本文（1件あたり約11KB）

取れない項目は空にする。Campusmate 側と同じ列に詰めて course_structured へ入れる。
"""
import re, time, pathlib, html as H
import httpx
from config import USER_AGENT

BASE = "https://www3.lit.kyushu-u.ac.jp/~syllabus/cgi-bin/"
FACULTY = "文学部"
# table-even は偶数年度、table-odd は奇数年度の入れ物になっている
def script_for(year: int) -> str:
    return "table-even.cgi" if year % 2 == 0 else "table-odd.cgi"

# gakki: 1前期 / 2後期 / 3春クォータ / 4夏クォータ / 5秋クォータ / 6冬クォータ / 0すべて
GAKKI = {"spring": ["1", "3", "4"], "autumn": ["2", "5", "6"], "all": ["0"]}

_TAGS = re.compile(r"<[^>]+>")
_CELL = re.compile(r"<td[^>]*>(.*?)</td>", re.S | re.I)
_LINK = re.compile(r'num=([0-9A-Za-z]+)[^"]*"[^>]*>(.*?)</a>', re.S | re.I)
_PERIOD = re.compile(r"^\s*(\d)\s*\(")          # 「1 (8:40 - 10:10)」
_DAYS = ["月", "火", "水", "木", "金", "土", "日"]


def fetch(client, url):
    r = client.get(url)
    r.raise_for_status()
    return r.content.decode("cp932", "replace")


def to_lines(raw_html: str):
    """HTMLを行の並びに落とす。Campusmate 側の extract_body と同じ考え方。"""
    t = re.sub(r"<script.*?</script>|<style.*?</style>|<!--.*?-->", "", raw_html,
               flags=re.S | re.I)
    t = re.sub(r"<br\s*/?>", "\n", t, flags=re.I)
    t = re.sub(r"</(td|tr|div|p|table|h[1-6]|li)>", "\n", t, flags=re.I)
    t = _TAGS.sub("", t)
    t = H.unescape(t).replace("　", " ")
    return [re.sub(r"\s+", " ", l).strip() for l in t.split("\n")]


# ---- 時間割ページ --------------------------------------------------------
# 1コマのセルは入れ子のテーブルで、1科目が2行の組になっている。
#   <TR><TD rowspan=2>専攻</TD><TD>教員</TD><TD>教室</TD></TR>
#   <TR><TD colspan=2><b>開講期</b><br><A ...num=ID...>科目名</A></TD></TR>
_DAYCELL = re.compile(r"<TD BGCOLOR='#FFFFDD'>", re.I)
_OUTER_ROW = re.compile(r'<TR VALIGN="top">', re.I)
_TH_PERIOD = re.compile(r'<TH valign="middle">\s*(\d)', re.I)
_INNER_ROW = re.compile(r"<TR[^>]*>(.*?)</TR>", re.S | re.I)
_ANUM = re.compile(r'num=([0-9A-Za-z]+)[^"]*"[^>]*>(.*?)</A>', re.S | re.I)
_ROOM = re.compile(r"^[A-Za-z]?[-–]?\d{2,4}$")


def _txt(frag):
    return re.sub(r"\s+", " ", H.unescape(_TAGS.sub("", frag)).replace("　", " ")).strip()


def parse_timetable(raw_html: str):
    """{科目ID: {slots, room, instructor, dept, title, term_label}} を返す。"""
    out = {}
    blocks = _OUTER_ROW.split(raw_html)
    for block in blocks[1:]:
        m = _TH_PERIOD.search(block)
        if not m:
            continue
        period = m.group(1)
        cells = _DAYCELL.split(block)[1:]
        for i, cell in enumerate(cells):
            if i >= len(_DAYS):
                break
            dept = inst = room = ""
            for row in _INNER_ROW.findall(cell):
                tds = _CELL.findall(row)
                link = _ANUM.search(row)
                if link:
                    num = link.group(1)
                    title = _txt(link.group(2))
                    term_label = ""
                    b = re.search(r"<b>(.*?)</b>", row, re.S | re.I)
                    if b:
                        term_label = _txt(b.group(1))
                    rec = out.setdefault(num, {"slots": [], "room": room,
                                               "instructor": inst, "dept": dept,
                                               "title": title, "term_label": term_label})
                    rec["slots"].append((_DAYS[i], period))
                    rec["room"] = rec["room"] or room
                    rec["instructor"] = rec["instructor"] or inst
                elif tds:
                    vals = [_txt(x) for x in tds]
                    # 3列そろう行が「専攻 / 教員 / 教室」。教室は空のことがある
                    if len(vals) >= 2:
                        dept = vals[0] or dept
                        inst = vals[1] or inst
                        room = ""
                        for v in vals[2:]:
                            if v and (_ROOM.match(v) or "教室" in v or "講義室" in v):
                                room = re.sub(r"\s*(教室|講義室)$", "", v)
                    for v in vals:
                        if v and (_ROOM.match(v) or v.endswith("教室")):
                            room = re.sub(r"\s*教室$", "", v)
    return out


# ---- 個別シラバス --------------------------------------------------------

# 左右の欄は同じ背景色なので、右側は align="right" が付くことで見分ける
_LEFT = re.compile(r"<TD(?![^>]*align=\"right\")[^>]*bgcolor='#EEFFBB'[^>]*>"
                   r"<SMALL>(.*?)</SMALL>", re.S | re.I)
_RIGHT = re.compile(r"<TD[^>]*align=\"right\"[^>]*bgcolor='#EEFFBB'[^>]*>"
                    r"<SMALL>(.*?)</SMALL>", re.S | re.I)
_TITLE = re.compile(r"<BIG><B>(.*?)</B></BIG><BR>\s*(.*?)<BR>", re.S | re.I)
_SUBTITLE = re.compile(r"講義題目\s*</TD>\s*<TD[^>]*><BIG><B>(.*?)</B>", re.S | re.I)
_INST = re.compile(r"<TABLE bgcolor='#FFEEDD'.*?<TD align=\"right\">(.*?)</TD>\s*<TD>(.*?)</TD>",
                   re.S | re.I)
_UPDATED = re.compile(r"更新日\s*</B>\s*:?\s*([0-9/]{6,12}[^<]*)", re.S | re.I)
_SECTION = re.compile(
    r"<TD align=\"center\"[^>]*>\s*([^<]{2,20}?)\s*</TD>(.*?)(?=<TD align=\"center\"|</TABLE>\s*</DIV>|$)",
    re.S | re.I)
_NUMBERING = re.compile(r"科目ナンバリングコード:\s*([A-Z0-9\-]+)")
_CREDIT = re.compile(r"単位数\s*(\d+(?:\.\d+)?)")
_SLOTLINE = re.compile(r"(毎週|隔週|集中)?\s*([月火水木金土日])曜\s*(\d)限")
_TERMWORD = re.compile(r"20\d{2}\s*(通年|前期|後期|春学期|夏学期|秋学期|冬学期|"
                       r"春クォータ|夏クォータ|秋クォータ|冬クォータ|[^\s<]*集中)")



_KEYWORDS = re.compile(r"<B>キーワード</B>\s*:\s*([^<]*)", re.I)
_PLAN_TABLE = re.compile(r"<B>授業計画</B>.*?(<TABLE[^>]*>.*?</TABLE>)", re.S | re.I)
_ROW = re.compile(r"<TR[^>]*>(.*?)</TR>", re.S | re.I)


def parse_plan(raw_html: str):
    """授業計画の表を [回, 内容] の並びにする。見出し行と空行は落とす。"""
    m = _PLAN_TABLE.search(raw_html)
    if not m:
        return None
    out, n = [], 0
    for row in _ROW.findall(m.group(1)):
        vals = [_txt(x) for x in _CELL.findall(row)]
        if not vals:
            continue
        # 見出し行（進度・内容・行動目標等 / 講義 / 演習…）は飛ばす
        if any(v.startswith("進度") for v in vals):
            continue
        body = next((v for v in vals[1:] if len(v) > 1), "")
        if not body:
            continue
        n += 1
        out.append([n, body[:240]])
    return out or None


def parse_course(raw_html: str, num: str, year: int):
    """個別ページ1件を dict にする。取れない項目は None。"""
    course = {"course_code": "LIT" + num, "lit_num": num, "year": year,
              "faculty": FACULTY,
              "url": f"{BASE}{script_for(year)}?thisyear={year}&num={num}&each=1"}

    m = _TITLE.search(raw_html)
    course["title"] = _txt(m.group(1)) if m else None
    course["title_en"] = _txt(m.group(2)) if m else None
    m = _SUBTITLE.search(raw_html)
    course["subtitle"] = (_txt(m.group(1)) or None) if m else None

    m = _INST.search(raw_html)
    course["instructors"] = (_txt(m.group(2)) or None) if m else None

    left = _LEFT.search(raw_html)
    lvals = [_txt(x) for x in re.split(r"<BR>", left.group(1), flags=re.I)] if left else []
    lvals = [x for x in lvals if x]
    course["course_name"] = next((x for x in lvals if "コース" in x or "学科" in x), None)
    course["category"] = next((re.sub(r"\s*\(単位数.*", "", x) for x in lvals
                               if "単位数" in x), None)
    course["required"] = next((x for x in lvals if x.endswith("科目")
                               and ("選択" in x or "必修" in x)), None)
    course["target_grade"] = next((x.split(":", 1)[1].strip() for x in lvals
                                   if x.startswith("対象学年")), "") or None
    m = _CREDIT.search(" ".join(lvals))
    course["credits"] = float(m.group(1)) if m else None

    right = _RIGHT.search(raw_html)
    rvals = [_txt(x) for x in re.split(r"<br>", right.group(1), flags=re.I)] if right else []
    rvals = [x for x in rvals if x]
    m = _NUMBERING.search(" ".join(rvals))
    course["numbering"] = m.group(1) if m else None
    m = _TERMWORD.search(" ".join(rvals))
    course["term"] = m.group(1) if m else None
    course["language"] = next((x for x in rvals if re.match(r"^[A-Z/]+科目", x)), None)
    course["room"] = next((re.sub(r"\s*教室$", "", x) for x in rvals
                           if x.endswith("教室") and not x.startswith("伊都")), None)
    if course["room"] is None:
        course["room"] = next((re.sub(r"^.*ゾーン\s*", "", x).replace("教室", "").strip()
                               for x in rvals if "ゾーン" in x and "教室" in x), None) or None
    course["slots"] = [(course["term"] or "", mm.group(2), mm.group(3))
                       for mm in _SLOTLINE.finditer(" ".join(rvals))]

    # 更新日は個別ページには載らない（一括表示のページにだけある）
    course["updated_at"] = None

    m = _KEYWORDS.search(raw_html)
    course["keywords"] = (_txt(m.group(1)) or None) if m else None
    course["plan"] = parse_plan(raw_html)

    sections = {}
    for name, blob in _SECTION.findall(raw_html):
        v = _txt(blob)
        if v and name not in sections:
            sections[name] = v
    course["sections"] = sections
    course["outline"] = sections.get("授業の概要")
    course["grading"] = sections.get("成績評価") or sections.get("成績評価方法")
    return course


# ---- 取得 ----------------------------------------------------------------

def crawl(year=2026, which="autumn", pause=1.0, limit=None, log=print, cache_dir=None):
    """時間割 -> 個別ページの順に取る。戻り値は course の dict のリスト。"""
    script = script_for(year)
    with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=60,
                      follow_redirects=True) as c:
        table = {}
        for g in GAKKI[which]:
            url = f"{BASE}{script}?thisyear={year}&gakki={g}&school0=1&show=S2110000&big=B00000"
            log(f"[lit] 時間割 gakki={g}")
            found = parse_timetable(fetch(c, url))
            for k, v in found.items():
                if k in table:
                    table[k]["slots"].extend(v["slots"])
                    table[k]["room"] = table[k]["room"] or v["room"]
                else:
                    table[k] = v
            time.sleep(pause)
        nums = sorted(table)
        if limit:
            nums = nums[:limit]
        log(f"[lit] 科目 {len(nums)}件の個別ページを取得する")

        out, errors = [], []
        for i, num in enumerate(nums, 1):
            url = f"{BASE}{script}?thisyear={year}&num={num}&each=1"
            try:
                page = fetch(c, url)
                if cache_dir:
                    (pathlib.Path(cache_dir) / f"{num}.html").write_text(page, encoding="utf-8")
                course = parse_course(page, num, year)
            except Exception as e:
                errors.append((num, f"{type(e).__name__}: {e}"))
                continue
            tt = table[num]
            # コマは時間割ページのほうが正確。あちらを優先する
            if tt["slots"]:
                course["slots"] = [(course.get("term") or "", d, p)
                                   for d, p in sorted(set(tt["slots"]))]
            course["room"] = tt["room"] or course.get("room")
            out.append(course)
            if i % 25 == 0:
                log(f"  {i}/{len(nums)}")
            time.sleep(pause)
    if errors:
        log(f"[lit] 解析できなかった科目 {len(errors)}件: {errors[:5]}")
    return out


# ---- 既存フォーマットへの変換 --------------------------------------------

def to_site_course(c):
    """site_data が作るのと同じ形の dict にする。取れない項目は空にする。"""
    import extract as EX
    import grading as GR

    # ルーブリックの記号は読めないので落とす。評価方法の名前だけ残す
    grading_text = re.sub(r"U_[A-Za-z0-9\-]+\s*\[[^\]]*\]|観点→成績評価方法↓?|→|↓", " ",
                          c.get("grading") or "")
    grading_text = re.sub(r"\s+", " ", grading_text).strip()
    flags, _ = EX.eval_flags(grading_text)
    g = GR.parse(grading_text)
    # 文学部は「秋クォータ」表記。Campusmate 側の「秋学期」に寄せてチップを共通にする
    term = (c.get("term") or "").replace("クォータ", "学期")
    room = c.get("room") or ""
    plan = [[n, t] for n, t in (c.get("plan") or [])]
    return {
        "c": c["course_code"],
        "t": c.get("title") or "",
        "s": c.get("subtitle") or "",
        "f": FACULTY,
        "g": [int(x) for x in EX.grades_of(c.get("target_grade") or "", FACULTY).split(",") if x],
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
        "pl": plan,
        "kw": " ".join((c.get("keywords") or "").split())[:120],
        "d": " ".join((c.get("outline") or "").split())[:1200],
        "n": c.get("numbering") or "",
        "u": "",
        # 文学部は科目ごとにURLが違うので、ここに持たせる
        "su": c.get("url") or "",
        "rm": room,
    }


if __name__ == "__main__":
    import sys, json, argparse
    from config import EXPORT_DIR
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", type=int, default=2026)
    ap.add_argument("--set", default="autumn", choices=list(GAKKI))
    ap.add_argument("--pause", type=float, default=1.0)
    ap.add_argument("--limit", type=int)
    a = ap.parse_args()
    rows = crawl(a.year, a.set, a.pause, a.limit)
    site = [to_site_course(c) for c in rows]
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    out = EXPORT_DIR / f"lit-courses-{a.year}.json"
    out.write_text(json.dumps(site, ensure_ascii=False, separators=(",", ":")),
                   encoding="utf-8")
    print(f"[lit] {len(site)}件 -> {out}")
