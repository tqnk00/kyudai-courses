"""基幹教育のB表（開講科目一覧）から、講義コードごとの教室と事前申請を取り出す。

A表は時間割の「枠」だけで教室が無いが、B表は科目の一覧で、
講義コード・教室・事前申請・担当クラスがそろった表になっている。
1年生用（2026年度入学者用）と2年生以上用（2025年度入学者用）の両方を読む。

事前申請の印（B表の凡例と注記より）
  ○ Moodleから指定日時までに事前申請が必要。抽選で通った人だけ履修登録される
  ● Campusmate-Jで履修登録しておく。人数次第で抽選があり、漏れると登録が消える
  ※ 教員が抽選する。申請は○の科目と同じMoodleページで行う（●※のように重なることもある）

取りこぼしを防ぐために気をつけていること
  ・2026年度版は「〇」（漢数字のゼロ）、2025年度版は「○」（白丸）と字が違う
  ・印は「●※」のように重なるので、完全一致ではなく1文字ずつ見る
  ・表として読むと講義コードの升が空になる行がある（教育学特論など）。
    その場合は同じページの生テキストから拾い直す
  ・印は入学年度で違うことがある（プログラミング演習は2026年度入学者だけ●）
"""
import json, re, collections
import pdfplumber

from config import DATA_DIR, PDF_DIR, YEAR
CODE = re.compile(r"^(2\d{7})$")
LINE_CODE = re.compile(r"^\s*(2\d{7})\s")
URL = "https://www.artsci.kyushu-u.ac.jp/campus_life/course.html"
MARKS = {"○": "○", "〇": "○", "◯": "○", "●": "●", "※": "※"}

FILES = [
    ("2026年度入学者用", "前期", "coreB-26-z.pdf"),
    ("2026年度入学者用", "後期", "coreB-26-k.pdf"),
    ("2025年度入学者用", "前期", "coreB-25-z.pdf"),
    ("2025年度入学者用", "後期", "coreB-25-k.pdf"),
]
WANT = ("講義コード", "科目区分", "科目名", "学期", "曜日", "時限",
        "担当クラス", "開講地区", "教室", "事前申請", "担当教員氏名", "備考")


def clean(v):
    return re.sub(r"\s+", " ", (v or "").replace("\n", " ")).strip()


ORDER = "○●※"


def marks_of(cell):
    """升の中にある印を返す。「●※」のように重なることがあるので1文字ずつ見る。

    並びは書かれた順ではなく ORDER に揃える。表によって前後するため。
    """
    found = {MARKS[ch] for ch in cell or "" if ch in MARKS}
    return "".join(ch for ch in ORDER if ch in found)


def header_map(row):
    """見出し行なら {列名: 位置} を返す。表ごとに列がずれることがあるので毎回作る。"""
    vals = [clean(c) for c in row]
    if "講義コード" not in vals or "事前申請" not in vals:
        return None
    return {name: i for i, name in enumerate(vals) if name in WANT}


def code_from_text(lines, rec):
    """升が空だった行の講義コードを、同じページの生テキストから拾い直す。

    科目名と教室と学期がそろって一致する行だけを採る。
    """
    title, room, term = rec.get("科目名", ""), rec.get("教室", ""), rec.get("学期", "")
    if not title:
        return ""
    hit = [m.group(1) for line in lines
           if (m := LINE_CODE.match(line)) and title in line
           and (not room or room in line) and (not term or term in line)]
    return hit[0] if len(set(hit)) == 1 else ""


def rows_of(path):
    """B表の1行を {列名: 値} で順に返す。コードが空の行は生テキストから補う。"""
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            lines = (page.extract_text() or "").split("\n")
            for table in page.extract_tables():
                idx = None
                for row in table:
                    h = header_map(row)
                    if h:
                        idx = h
                        continue
                    if not idx:
                        continue
                    rec = {k: (clean(row[i]) if i < len(row) else "")
                           for k, i in idx.items()}
                    code = rec.get("講義コード", "")
                    if not CODE.match(code):
                        code = code_from_text(lines, rec)
                        if not CODE.match(code):
                            continue
                        rec["講義コード"] = code
                    yield rec


def run():
    out = {}
    for cohort, term, fname in FILES:
        p = PDF_DIR / fname
        if not p.exists():
            print(f"  (無し) {fname}")
            continue
        n = 0
        for r in rows_of(p):
            n += 1
            code = r["講義コード"]
            rec = out.setdefault(code, {
                "room": "", "klass": "", "area": "", "note": "",
                "apply": {},          # 入学年度 -> 印
                "source_url": URL,
            })
            for key, col in (("room", "教室"), ("klass", "担当クラス"),
                             ("area", "開講地区"), ("note", "備考")):
                if not rec[key]:
                    rec[key] = r.get(col, "")
            mark = marks_of(r.get("事前申請", ""))
            if mark:
                cur = rec["apply"].get(cohort, "")
                # 同じ年度に複数行あるときは、印の種類を足し合わせる
                rec["apply"][cohort] = "".join(
                    ch for ch in ORDER if ch in cur or ch in mark)
            else:
                rec["apply"].setdefault(cohort, "")
        print(f"  {cohort} {term}  {fname:18} {n:5}行")
    return out


def build(year=YEAR):
    """B表を読んで data/core-b-<year>.json を書き出す。"""
    rows = run()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / f"core-b-{year}.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    return rows


if __name__ == "__main__":
    rows = build()
    marked = {c: v for c, v in rows.items() if any(v["apply"].values())}
    cnt = collections.Counter(ch for v in marked.values()
                              for m in v["apply"].values() for ch in m)
    print()
    print(f"講義コード {len(rows)}件 / 教室あり {sum(1 for v in rows.values() if v['room'])}")
    print(f"事前申請あり {len(marked)}件  （延べ ○{cnt['○']} ●{cnt['●']} ※{cnt['※']}）")
    both = [c for c, v in marked.items() if len(v["apply"]) > 1]
    diff = [c for c in both if len(set(marked[c]["apply"].values())) > 1]
    print(f"両方の年度の表に載る {len(both)}件 / うち印が年度で違う {len(diff)}件")
    for c in diff:
        print(f"    {c} {marked[c]['apply']}")
