"""経済学部の時間割PDFを構造化する。

表は26列。左端が曜日（各曜日ブロックの先頭行にだけ入る）、
以降は5時限ぶんの [学年, 科目名, 教室, 担当者] が5列おきに並ぶ。
"""
import json, re, collections
import pdfplumber

from config import DATA_DIR, PDF_DIR
DAYS = "月火水木金土日"


def cell(row, i):
    v = row[i] if i < len(row) else ""
    return re.sub(r"\s+", " ", (v or "").replace("\n", "")).strip()


def parse(page, term):
    tables = page.extract_tables()
    big = max(tables, key=len)
    out, day = [], ""
    for row in big:
        d = cell(row, 0)
        if d and d[0] in DAYS:
            day = d[0]
        if not day:
            continue
        for p in range(1, 6):
            base = 2 + 5 * (p - 1)
            grade, title = cell(row, base), cell(row, base + 1)
            room, inst = cell(row, base + 2), cell(row, base + 3)
            if not title:
                continue
            out.append({
                "faculty": "経済学部", "department": "", "course_code": "",
                "title": title,
                "instructors": [x for x in [inst.strip("（）()")] if x],
                "room": room,
                "term": term,
                "slots": [{"day": day, "period": p}],
                "credits": None,
                "target_grade": "" if grade.startswith("【") else grade,
                "category": "専攻教育科目" if grade.startswith("【基") else "",
                "required": "", "note": "", "confidence": "high",
                "source_url": "https://www.econ.kyushu-u.ac.jp/kyoumu/",
            })
    return out

def build():
    """PDFを読んで data/econ-timetable.json を書き出す。"""
    with pdfplumber.open(PDF_DIR / "econ-2026.pdf") as pdf:
        # 1ページ目が前期、2ページ目が後期
        courses = parse(pdf.pages[0], "前期") + parse(pdf.pages[1], "後期")

    # 同じ科目が連続コマ等で複数行に出る。学期＋科目名＋教室＋担当者でまとめる
    merged = {}
    for c in courses:
        key = (c["term"], c["title"], c["room"], tuple(c["instructors"]))
        if key in merged:
            for s in c["slots"]:
                if s not in merged[key]["slots"]:
                    merged[key]["slots"].append(s)
        else:
            merged[key] = c
    courses = list(merged.values())

    out = {"university": "九州大学", "year": 2026, "scope": "2026年度 前期・後期",
           "sources": [{"faculty": "経済学部",
                        "url": "https://www.econ.kyushu-u.ac.jp/student/schedule",
                        "title": "2026年度時間割", "format": "pdf"}],
           "not_found": [], "courses": courses}
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "econ-timetable.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[econ] 科目 {len(courses)}件 / 教室あり "
          f"{sum(1 for c in courses if c['room'])}件")
    return courses


if __name__ == "__main__":
    courses = build()
    print("コマ合計:", sum(len(c["slots"]) for c in courses))
    print("曜日の内訳:", collections.Counter(s["day"] for c in courses for s in c["slots"]))
    for c in courses[:6]:
        print("  ", c["title"][:34], "|", c["room"], "|", "・".join(c["instructors"]),
              "|", [f'{s["day"]}{s["period"]}' for s in c["slots"]])
