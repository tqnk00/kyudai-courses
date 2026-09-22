"""事前申請の印を取りこぼしていないか、PDFの生テキストと突き合わせて数える。

表として読んだ結果（parse_coreB）と、ページの文字そのものを数えた結果が
合わなければ、どこかで行を落としている。
"""
import re, collections, pathlib, unicodedata
import pdfplumber
import parse_coreB as P

MARK = re.compile(r"[○〇◯●※]")
CODE = re.compile(r"\b(2\d{7})\b")

for cohort, term, fname in P.FILES:
    path = P.S / "pdf" / fname
    # 1) 表として読んだ結果
    tbl = collections.Counter()
    tbl_codes = set()
    for r in P.rows_of(path):
        m = P.marks_of(r.get("事前申請", ""))
        if m:
            for ch in m:
                tbl[ch] += 1
            tbl_codes.add(r["講義コード"])
    # 2) 生テキストで、講義コードと印が同じ行にあるものを数える
    raw_codes = set()
    raw = collections.Counter()
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            for line in (page.extract_text() or "").split("\n"):
                # 凡例の行は数えない
                if line.startswith("事前申請：") or "受講希望の場合" in line:
                    continue
                cs = CODE.findall(line)
                ms = MARK.findall(line)
                if cs and ms:
                    raw_codes |= set(cs)
                    for x in ms:
                        raw[P.MARKS.get(x, x)] += 1
    print(f"{cohort} {term:3} {fname:18} 表 ○{tbl['○']:3} ●{tbl['●']:3} "
          f"※{tbl['※']:3} ({len(tbl_codes)}コード) / "
          f"生 ○{raw['○']:3} ●{raw['●']:3} ※{raw['※']:3} ({len(raw_codes)}コード)")
    only_raw = raw_codes - tbl_codes
    only_tbl = tbl_codes - raw_codes
    if only_raw:
        print(f"    生にしかない: {len(only_raw)}件 {sorted(only_raw)[:8]}")
    if only_tbl:
        print(f"    表にしかない: {len(only_tbl)}件 {sorted(only_tbl)[:8]}")
