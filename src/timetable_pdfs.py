"""各学部が公開している時間割表PDFの取得先。

Campusmateのシラバスには教室がほとんど入っていない（本文にあるのは7.9%）。
足りないぶんは各学部が公開している時間割表から補うので、その在りかをここに置く。
学期ごとに差し替わるファイル名なので、URLが404になったら学部のページを見て直す。

  python run.py rooms            data/pdf に無いものだけ取ってくる
  python run.py rooms --refresh  全部取り直す
"""
import httpx

from config import USER_AGENT, PDF_DIR

# (保存名, URL, どのページから辿れるか)
PDFS = [
    ("edu-2026.pdf",
     "https://www.education.kyushu-u.ac.jp/wp/wp-content/uploads/2026/08/"
     "R8_jugyoujikanwari_kouki_as_of-_20260806-1.pdf",
     "https://www.education.kyushu-u.ac.jp/schedules/"),
    ("econ-2026.pdf",
     "https://www.econ.kyushu-u.ac.jp/wp-content/uploads/2026/09/"
     "39bf3001198290d7895e0e7460a48c40.pdf",
     "https://www.econ.kyushu-u.ac.jp/kyoumu/"),
    ("law-2026.pdf",
     "https://www.law.kyushu-u.ac.jp/new2021/wp-content/uploads/2026/09/"
     "R8法学部時間割20260907.pdf",
     "https://www.law.kyushu-u.ac.jp/faculty/study"),
    ("agr-2026.pdf",
     "https://ag.kyushu-u.ac.jp/jpn-under_class2026.pdf",
     "https://ag.kyushu-u.ac.jp/"),
    ("eng-eecs-c.pdf",
     "https://www.eecs.kyushu-u.ac.jp/wp/wp-content/uploads/R8class_c3.pdf",
     "https://www.eecs.kyushu-u.ac.jp/school.html"),
    ("eng-eecs-d.pdf",
     "https://www.eecs.kyushu-u.ac.jp/wp/wp-content/uploads/R8class_d3.pdf",
     "https://www.eecs.kyushu-u.ac.jp/school.html"),
    ("eng-civil.pdf",
     "https://civil.kyushu-u.ac.jp/civil_wp/wp-content/uploads/"
     "Lecture2026J_fallALL_20260907.pdf",
     "https://civil.kyushu-u.ac.jp/student/schedule/"),
    ("design-a.pdf",
     "https://www.design.kyushu-u.ac.jp/_cms_dir/wp-content/uploads/2026/09/"
     "0570e827da344433b0778c45301dcc1b.pdf",
     "https://www.design.kyushu-u.ac.jp/curriculum/"),
]
# 理学部は学科ごとに分かれている
PDFS += [(f"sci-{name}.pdf",
          f"https://www.sci.kyushu-u.ac.jp/student/pdf/2026_st_{name}.pdf",
          "https://www.sci.kyushu-u.ac.jp/student/timetable.html")
         for name in ("math_4", "phys_4", "chem_3", "bio_4",
                      "geo_3", "info_4", "com")]

# 基幹教育のB表。入学年度ごとに別の表になっていて、印が食い違うことがある
CORE_B = "https://www.artsci.kyushu-u.ac.jp/campus_life/pdf"
PDFS += [
    ("coreB-26-z.pdf", f"{CORE_B}/2026z_jikan_tableB_20260616.pdf",
     "https://www.artsci.kyushu-u.ac.jp/campus_life/course.html"),
    ("coreB-26-k.pdf", f"{CORE_B}/2026k_jikan_tableB_20260915.pdf",
     "https://www.artsci.kyushu-u.ac.jp/campus_life/course.html"),
    ("coreB-25-z.pdf", f"{CORE_B}/2026z_jikan_tableB_2nd_20260615.pdf",
     "https://www.artsci.kyushu-u.ac.jp/campus_life/course.html"),
    ("coreB-25-k.pdf", f"{CORE_B}/2026k_jikan_tableB_2nd_20260915.pdf",
     "https://www.artsci.kyushu-u.ac.jp/campus_life/course.html"),
]


def fetch(refresh=False):
    """PDFを data/pdf に落とす。取れなかったものは名前を返して先へ進む。

    学期ごとにファイル名が変わるので、1本落とせなくても他を止めない。
    """
    PDF_DIR.mkdir(parents=True, exist_ok=True)
    ok = failed = skipped = 0
    misses = []
    with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=60,
                      follow_redirects=True) as cli:
        for name, url, page in PDFS:
            dest = PDF_DIR / name
            if dest.exists() and not refresh:
                skipped += 1
                continue
            try:
                r = cli.get(url)
                r.raise_for_status()
                # ページが200でHTMLを返すことがある（経済学部で踏んだ）。
                # そのまま書くと手元の正しいPDFを壊すので、中身を見てから置く
                if not r.content.startswith(b"%PDF"):
                    raise ValueError("PDFではない（HTMLが返っている）")
                dest.write_bytes(r.content)
                print(f"  取得 {name:18} {len(r.content):>8,}バイト")
                ok += 1
            except Exception as e:
                print(f"  !! {name:18} {type(e).__name__} → {page} で新しいURLを確認")
                misses.append(name)
                failed += 1
    print(f"[pdf] 取得 {ok} / 既存 {skipped} / 失敗 {failed}")
    return misses


if __name__ == "__main__":
    fetch()
