"""時間割PDFから「科目 → 教室」を取り出す。

レイアウトの復元はしない。学部ごとに教室の書き方だけが違うので、その正規表現だけを
個別に持ち、拾った教室を Campusmate 側の科目名リスト（＝正解表）と突き合わせる。
名前（または講義コード）が一致したときだけ採用するため、崩れたPDFでも誤りが入りにくい。

  教育学部  科目名【A104】教員          → 【】の手前が科目名
  理学部    科目名 教員 W1-C-501        → 教室記号の手前、教員をまたぐので前方一致も見る
  農学部    26346215 森林環境経営学 溝上 展也 327  → 講義コードが主キー、末尾の数字が教室
  経済学部  科目名 D-103 教員            → 専用パーサの結果(econ-timetable.json)を使う
  基幹教育・共創学部  教室の記載そのものが無い（共創のPDFは「シラバスで通知」と明記）
"""
import json, re, unicodedata, collections
import pdfplumber

import db as DB
import extract as EX
import timetable_pdfs as PDFS
from config import KYUSHU, DATA_DIR, PDF_DIR, YEAR, SPRING_GROUPS, AUTUMN_GROUPS

UID = KYUSHU["university_id"]

# 照合のしきい値。どれも実際のPDFを見て決めた値
MIN_NAME_LEN = 4      # これより短い科目名（「英語」など）は、どこにでも当たるので探さない
TAIL_WINDOW = 12      # 直前の行から探すときは、行末からこの文字数以内で終わる名前だけ
PREFIX_MIN_LEN = 8    # 末尾が切れた名前を前方一致で同じ科目とみなす最短の長さ
PARTIAL_MIN_LEN = 5   # 頭が欠けた名前を部分一致で同じ科目とみなす最短の長さ
MISSING_TITLE_LEN = (4, 26)   # 未掲載の候補にする題名の長さ。外れるものは切り出しの失敗

CODE = re.compile(r"(2\d{7})")
ROOM_EDU = re.compile(r"【([^】]{1,24})】")
ROOM_SCI = re.compile(r"([A-Z]\d?-[A-Z0-9]{1,2}-\d{3}(?:\s*,\s*[A-Z]-?\d{3})?"
                      r"|[^\s【】]{0,6}号館[^\s【】]{0,8}|講義室\s?[0-9０-９A-Z]{1,4}"
                      r"|[0-9０-９]{3,4}講義室|講義棟\s?[0-9０-９]{3})")
# 法学部  ローマ法Ⅰ 五十君 B112 ２・３・４
ROOM_LAW = re.compile(r"(?<![A-Za-z0-9])([A-E]\d{3}|演習室\s?[0-9０-９ⅠⅡⅢⅣⅤ]+"
                      r"|大講義室[ⅠⅡⅢ]?|中講義室[ⅠⅡⅢ]?|小講義室[ⅠⅡⅢ]?|教員研究室)")
# 工学部・芸術工学部  括弧の中が教室  EC(工学部第7)一木 / 佐川（工14） / （W2-319）
ROOM_PAREN = re.compile(r"[（(]\s*((?:工学部第|シス情|創作工房|ウエスト|イースト|センター)"
                        r"[^（）()]{0,10}|[A-Z]\d?-\d{3}[^（）()]{0,6}|工\s?\d{1,2}[^（）()]{0,6}"
                        r"|工大[^（）()]{0,4}|[0-9０-９]{3}[^（）()]{0,8}"
                        r"|[^（）()]{0,6}(?:講義室|実験室|工作房|スタジオ|演習室|ホール)[^（）()]{0,6})\s*[）)]")

NOT_ROOM = re.compile(r"時間割参照|参照|注意|必修|備考|未定|TBA|基幹教育|学部$|^前期$|^後期$")


def norm(s):
    s = unicodedata.normalize("NFKC", s or "").lower()
    s = re.sub(r"【[^】]*】|\[[^\]]*\]|〔[^〕]*〕|※.*$", "", s)
    s = re.sub(r"[◆■□〇●◎○◇※☆★▲△\*]", "", s)
    return re.sub(r"[\s・､、,（）()〈〉<>･\-−―ー~〜]", "", s)


# 学期のまとまり。時間割PDFは前期と後期でページが分かれていることが多く（法・理・経済）、
# 同じ名前の科目が前期と後期の両方にある（民事訴訟法Ⅰ／Ⅱ、通年の演習など）。
# ページの学期と科目の学期が合うものだけを突き合わせる
_PAGE_SPRING = re.compile(r"前期|前学期|春学期|夏学期")
_PAGE_AUTUMN = re.compile(r"後期|後学期|秋学期|冬学期")


def season_of(term):
    """開講学期を spring / autumn に寄せる。通年・集中などは None（どちらのページとも合う）。"""
    g = EX.term_group_of(term)
    if g in SPRING_GROUPS:
        return "spring"
    if g in AUTUMN_GROUPS:
        return "autumn"
    return None


def page_season(lines):
    """ページ冒頭の見出しから、そのページの学期を読む。決められなければ None。

    見るのは先頭2行だけ。凡例の「前年度後期開始越年科目」などを拾わないため。
    """
    head = " ".join(lines[:2])
    sp, au = _PAGE_SPRING.search(head), _PAGE_AUTUMN.search(head)
    if sp and not au:
        return "spring"
    if au and not sp:
        return "autumn"
    return None


def fits(course, season):
    return season is None or course["season"] is None or course["season"] == season


def clean_room(r):
    r = re.sub(r"\s+", "", r).strip(" 　,、")
    return re.sub(r"^伊都地区", "", r)


def load_courses(con, year):
    """DBから科目コード・科目名・学部・コマ・教員を引く。

    以前はビルド済みのサイトJSONを読んでいたが、出力を入力にすることになり、
    更地からビルドできなかった。照合に要るのはこれだけなのでDBから直接引く。
    """
    rows = con.execute(
        "SELECT course_code, title, faculty, instructors, term FROM course_structured "
        "WHERE university_id=? AND year=? AND is_undergrad=1",
        (UID, year)).fetchall()
    slots, slot_terms = collections.defaultdict(list), {}
    for r in con.execute("SELECT course_code, term, weekday, period FROM course_slots "
                         "WHERE university_id=? AND year=? ORDER BY course_code, seq",
                         (UID, year)):
        slots[r["course_code"]].append((r["weekday"], r["period"]))
        slot_terms.setdefault(r["course_code"], r["term"])
    idx, by_fac = {}, collections.defaultdict(lambda: collections.defaultdict(list))
    for r in rows:
        term = (r["term"] or "").strip() or slot_terms.get(r["course_code"]) or ""
        c = {"c": r["course_code"], "t": r["title"] or "",
             "f": r["faculty"] or "",
             "i": [x for x in (r["instructors"] or "").splitlines() if x.strip()],
             "season": season_of(term),
             "slots": slots.get(r["course_code"], [])}
        idx[c["c"]] = c
        by_fac[c["f"]][norm(c["t"])].append(c)
    return idx, by_fac


def find_name(seg, names, tail_only=False):
    """segの中にある科目名を探す。教室にいちばん近いもの、同じ位置なら長いものを採る。

    格子のPDFでは「3 数学特論11 坂本 祥太 W1-C-513」のように、科目名の前に時限、
    後ろに教員名が付く。末尾・先頭の一致だけでは拾えないので、途中も探す。
    """
    n = norm(seg)
    if not n:
        return None
    best, best_end = None, -1
    for name in names:
        if len(name) < MIN_NAME_LEN:
            continue
        at = n.rfind(name)
        if at < 0:
            continue
        if tail_only and at + len(name) < len(n) - TAIL_WINDOW:
            continue
        # 教室に近い（名前の終わりが後ろにある）ものを採る。終わりが同じなら長いほう。
        # 「国際政治学Ⅰ」の中に「政治学Ⅰ」も見つかるが、同じ位置で終わるので長いほうが勝つ
        end = at + len(name)
        if end > best_end or (end == best_end and len(name) > len(best)):
            best, best_end = name, end
    return best


def lines_of(path):
    """PDFの行を (そのページの学期, 行) の並びで返す。"""
    if not (PDF_DIR / path).exists():
        print(f"  !! {path} がありません（python run.py rooms で取得）")
        return []
    with pdfplumber.open(PDF_DIR / path) as pdf:
        out = []
        for page in pdf.pages:
            lines = (page.extract_text() or "").split("\n")
            season = page_season(lines) or PDFS.SEASON.get(path)
            out += [(season, ln) for ln in lines]
    return out


def is_known(title, known):
    """Campusmate のどこかの学部に同じ科目があるか。

    未掲載の判定は学部をまたいで見る（法学部の時間割に載る「学術英語・テーマベース」は
    基幹教育科目として載っている）。PDFで科目名の末尾が切れることもあるので
    （「Education and politic」→ Education and Politics Ⅰ）、長めの名前は前方一致も見る。
    """
    n = norm(title)
    if n in known:
        return True
    if len(n) >= PREFIX_MIN_LEN and any(k.startswith(n) for k in known):
        return True
    # 升の中で折り返されて頭が欠けることもある（「デザイン学Ａ」→「ザイン学Ａ」）
    return len(n) >= PARTIAL_MIN_LEN and any(n in k for k in known)


def match_course(seg, prev_seg, here, tail_only):
    """教室の手前の文字列 seg から科目を当てる。(科目, 同名が複数あったか, 切り出した題名) を返す。

    当て方は3段。上から順に試し、1件に決まったところで終わる。
      1. seg（無ければ直前の行の末尾。セルが折り返されるため）に科目名がそのまま含まれている
      2. 題名らしい部分を切り出し、頭の余分な語を1つずつ落として一致するものを探す
      3. 名前では当たらないので、末尾の教員名で引く（法学部のゼミ。by_instructor）
    """
    key = find_name(seg, here, tail_only) or find_name(prev_seg, here, tail_only=True)
    title = ""
    if not key:
        title = guess_title(seg)
        # 隣の升の教員名が頭に残ることがあるので、頭から1語ずつ削って試す
        for cand in (title, *(title.split(" ", i)[-1] for i in range(1, 3))):
            if norm(cand) in here:
                key, title = norm(cand), cand
                break
    if not key:
        return by_instructor(title, seg, here), False, title
    cands = here[key]
    return (cands[0] if len(cands) == 1 else None), len(cands) > 1, title


def note_missing(title, room, faculty, url, season, extra, known):
    """どの科目にも当たらなかった題名を、Campusmate に無い科目の候補として extra に控える。"""
    t = title
    # 隣の升から教員名・曜日・学年が頭に残っていれば落とす
    t = re.sub(r"^(?:[月火水木金土日]|[0-9０-９２３４・]+|[◆♦■□〇●○◇]"
               r"|（[^）]*）|課題発見科目|高年次)[\s　]*", "", t).strip()
    t = re.sub(r"^[一-龥]{2,4}[\s　]+(?=.{5,})", "", t).strip()
    t = re.sub(r"[\s　][一-龥]{2,3}$", "", t).strip()
    t = re.sub(r"^[◆♦■□〇●○◇]\s*", "", t).strip()
    lo, hi = MISSING_TITLE_LEN
    if not lo <= len(t) <= hi or is_known(t, known):
        return
    # 題名ではないもの。「(lectures)」のような注記と、半角カナだけの教員名（ｳﾞｨｯｶｰｽﾞ）も含む
    if re.search(r"補講枠|時間割|教室|曜日|コース$|参照|^同上|^～|^[0-9]|^[（(]|^[ｦ-ﾟ]+$", t):
        return
    m = extra.setdefault(norm(t), {"title": t, "room": room, "faculty": faculty,
                                   "source_url": url, "season": season or ""})
    # 前期と後期の両方のページに出る（通年の演習など）なら学期は付けない
    if m["season"] != (season or ""):
        m["season"] = ""


def by_room_marker(files, faculty, rx, names, url=None, tail_only=False, extra=None, known=()):
    """教室の表記（rx）を目印に、その手前の文字列から科目を当てて教室を付ける。

    extra を渡すと、Campusmateに無い科目の候補もそこに溜める。
    known は全学部の科目名（norm済み）。ここにある名前は未掲載に入れない。
    出典のURLは、そのPDFの掲載ページ（timetable_pdfs.PAGE）。url を渡せばそちらを使う。
    """
    if extra is None:
        extra = {}
    # そのページの学期に開講される科目だけで名前を探す。全学期で探すと、前期の「政治学Ⅰ」が
    # 後期ページの「国際政治学Ⅰ」に当たって、正しい科目を取りこぼす
    views = {}

    def in_season(season):
        if season not in views:
            views[season] = {k: v for k, v in
                             ((k, [c for c in cs if fits(c, season)]) for k, cs in names.items())
                             if v}
        return views[season]

    got, mention, amb = {}, 0, 0
    for f in files:
        src = url or PDFS.PAGE[f]
        prev_seg = ""
        for season, line in lines_of(f):
            here = in_season(season)
            if not line.strip():
                continue
            pos = 0
            for m in rx.finditer(line):
                room = clean_room(next(g for g in m.groups() if g))
                seg, pos = line[pos:m.start()], m.end()
                if not room or NOT_ROOM.search(room):
                    continue
                mention += 1
                course, ambiguous, title = match_course(seg, prev_seg, here, tail_only)
                if course:
                    got.setdefault(course["c"], {"room": room, "source_url": src})
                elif ambiguous:
                    amb += 1
                else:
                    note_missing(title, room, faculty, src, season, extra, known)
            prev_seg = line
    return got, mention, amb


SEMINAR = norm("演習Ⅰ")


def by_instructor(title, seg, names):
    """科目名で当たらなかった行を、末尾の教員の姓で引き直す。1件に決まったときだけ返す。

    対象は2通り。
      ・時間割の「○○演習」と、Campusmate の「演習Ⅰ」（法学部のゼミ。題目は副題にある）
      ・頭4文字が同じ別表記（「政治過程・政策過程論特別講義」と「政治過程・政策過程特殊講義」）
    """
    m = TRAIL_NAME.search(seg.strip())
    if not m or len(norm(title)) < 4:
        return None
    # 「赤坂・高橋」「田中（孝）」は先頭の姓だけ使う。「西」「柳」のような1文字の姓もある
    fam = re.split(r"[・（(、,]", m.group(1))[0]
    if not fam or re.search(r"[0-9０-９A-Za-z]", fam):
        return None
    t = norm(title)

    def taught(c):
        # 1文字の姓は「西 英昭」の姓の欄と完全一致だけ見る（「西村」に当てない）
        return any((x.split()[0] == fam) if len(fam) == 1 else x.startswith(fam)
                   for x in c["i"] if x.split())

    everyone = [c for cs in names.values() for c in cs if taught(c)]
    # ゼミを先に見る。同じ先生の講義（民事訴訟法Ⅰ）と頭4文字が重なって決められなくなるため
    for pick in ((lambda c: norm(c["t"]) == SEMINAR and t.endswith("演習")),
                 (lambda c: norm(c["t"])[:4] == t[:4])):
        hits = {c["c"]: c for c in everyone if pick(c)}
        if len(hits) == 1:
            return next(iter(hits.values()))
        if hits:
            return None
    return None


# 「◆ローマ法Ⅰ 五十君 」から科目名だけを取り出す
LEAD_JUNK = re.compile(r"^[\s０-９0-9２３４・､、|]*(?:[◆■□〇●◎○◇※☆★▲△\*]\s*)*")
TRAIL_NAME = re.compile(r"[\s　]([^\s　]{1,6})$")


def guess_title(seg):
    """教室の手前の文字列から、科目名らしい部分を切り出す。

    法学部は「２・３・４ ◆中国法演習 西 」のように、前に学年と記号、後ろに教員名が付く。
    教育学部は「国際教育文化コース 〔秋学期〕教育哲学特論Ⅱ演習」のように前に区分が付く。
    """
    t = re.sub(r"〔[^〕]*〕", "", seg).strip()
    t = re.sub(r"^.*?(?:コース|系 共 通|教 育 心 理 学 系 共 通)\s*", "", t)
    t = LEAD_JUNK.sub("", t.strip())
    t = TRAIL_NAME.sub("", t).strip()      # 末尾の教員名を落とす
    t = re.sub(r"^[０-９0-9２３４・､、\s]+", "", t)
    t = re.sub(r"^(?:年生用補講枠|補講枠)\s*", "", t)
    return t


# 共創学部の表は「日本語名/English name」の形で科目が並ぶ。スラッシュの手前を科目名とみなす
_KYOSO_TITLE = re.compile(r"(?:^|(?<=\s))([^\s/\[\]【】]*[一-龥ぁ-んァ-ヶ][^\s/\[\]【】]*)/")
_KYOSO_JUNK = re.compile(r"限目|曜日|学期|時限|科目$|^：|Class|^[A-Za-z]\.|https|水色|赤字|集中開講"
                         r"|^[^一-龥ぁ-んァ-ヶ]*$|^[）)]|^.{0,3}$"
                         # 教員の割り振り「Class2-金子」「S.・中野」の切れ端
                         r"|^[.．0-9]|^\d-")


def kyoso_missing(known, missing):
    """共創学部の時間割にあって、Campusmate のどの学部にも無い科目を missing に足す。

    教室は載っていないので、使い道は未掲載の照合だけ。表の升で名前が折り返されて
    欠けることがあるため、照合は is_known の前方・部分一致に任せる。
    """
    n = 0
    for f in ("kyoso-B1.pdf", "kyoso-B2.pdf", "kyoso-B3.pdf"):
        for season, line in lines_of(f):
            for m in _KYOSO_TITLE.finditer(line):
                # 前の升の英語が頭に付くことがある（「Seminar○共創基礎演習」）。印の後ろだけ採る
                t = re.split(r"[◎○■]", m.group(1))[-1]
                t = re.sub(r"^[A-Za-z]+(?=[^A-Za-z])", "", t)   # 印の無い「Seminar共創プロジェクト」
                if _KYOSO_JUNK.search(t) or is_known(t, known):
                    continue
                n += 1
                mm = missing.setdefault(norm(t), {"title": t, "room": "", "faculty": "共創学部",
                                                  "source_url": PDFS.PAGE[f], "season": season or ""})
                if mm["season"] != (season or ""):
                    mm["season"] = ""
    return n


def agr_rooms(idx):
    """農学部は講義コードが入っているので、コードごとに後ろの教室番号を採る。

    1行に複数の科目が並ぶので、教室は「そのコードの直後に最初に現れる番号」を採る。
    末尾を採ると隣の科目の教室を拾ってしまう（土壌物理学 229 / 田村 和彦 228）。
    拾ったあと、PDF側の科目名がCampusmateの同コードの科目名と合うかを検算する。
    """
    got, mention, disagree = {}, 0, 0
    rx = re.compile(r"^\s*(.*?)\s*(?:^|\s)([0-9０-９]{2,3}|[A-Z]-?\d{3}"
                    r"|[^\s]{0,6}号館[^\s]{0,8}|遠隔授業|オンライン)(?:\s|$)")
    for _season, line in lines_of("agr-2026.pdf"):
        parts = CODE.split(line)
        for i in range(1, len(parts), 2):
            code = parts[i]
            tail = parts[i + 1] if i + 1 < len(parts) else ""
            if code not in idx:
                continue
            m = rx.match(tail)
            if not m:
                continue
            mention += 1
            head = unicodedata.normalize("NFKC", m.group(1))
            # 検算: コードの直後に科目名か教員名が来ているはず。
            # Campusmateの科目名と食い違い、かつ教員名にも見えないものは捨てる
            want = norm(idx[code]["t"])
            if want and want[:5] not in norm(head) and not re.fullmatch(
                    r"[^\s]{1,5}\s+[^\s]{1,6}", head.strip()):
                disagree += 1
                continue
            got.setdefault(code, {"room": clean_room(m.group(2)),
                                  "source_url": PDFS.PAGE["agr-2026.pdf"]})
    return got, mention, disagree


def econ_rooms(by_fac):
    """経済学部は専用パーサの結果を、科目名＋コマで突き合わせる。"""
    p = DATA_DIR / "econ-timetable.json"
    if not p.exists():
        return {}, 0, 0
    tt = json.loads(p.read_text(encoding="utf-8"))["courses"]
    names = by_fac.get("経済学部", {})
    got, amb = {}, 0
    for t in tt:
        room = clean_room(t.get("room") or "")
        if not room:
            continue
        season = season_of(t.get("term"))
        cands = [c for c in names.get(norm(t["title"]), []) if fits(c, season)]
        if len(cands) > 1:
            want = {(s["day"], str(s["period"])) for s in t.get("slots", [])}
            cands = [c for c in cands if want & set(c["slots"])] or cands
        if len(cands) > 1:
            # 「経済・経営学演習」のように同名・同コマが20件あるので、教員で絞る
            # PDF側は姓だけのことが多い
            fam = [re.sub(r"[\s　].*$", "", x) for x in t.get("instructors", []) if x]
            if fam:
                cands = [c for c in cands
                         if any(f and f in "".join(c.get("i") or []) for f in fam)] or cands
        if len(cands) == 1:
            got[cands[0]["c"]] = {"room": room, "source_url": PDFS.PAGE["econ-2026.pdf"]}
        elif cands:
            amb += 1
    return got, len(tt), amb


def match_all(con, year=YEAR):
    """全学部の時間割PDFを読み、(科目索引, 教室, 学部ごとの集計, 未掲載の候補) を返す。"""
    idx, by_fac = load_courses(con, year)
    rooms, stats = {}, []

    # 未掲載科目は、レイアウトが素直で名前を切り出せるPDFだけを対象にする
    missing = {}
    known = {name for fac in by_fac.values() for name in fac}
    g, n, a = by_room_marker(["edu-2026.pdf", "edu-2026-spring.pdf"], "教育学部", ROOM_EDU,
                             by_fac["教育学部"], extra=missing, known=known)
    rooms.update(g); stats.append(("教育学部", "【教室】", n, len(g), a))

    sci = [f"sci-{x}.pdf" for x in
           ("math_4", "phys_4", "chem_3", "bio_4", "geo_3", "info_4", "com")]
    g, n, a = by_room_marker(sci, "理学部", ROOM_SCI, by_fac["理学部"])
    rooms.update(g); stats.append(("理学部", "W1-C-501等", n, len(g), a))

    g, n, dis = agr_rooms(idx)
    rooms.update(g); stats.append(("農学部", "講義コード＋番号", n, len(g), dis))

    g, n, a = econ_rooms(by_fac)
    rooms.update(g); stats.append(("経済学部", "専用パーサ", n, len(g), a))

    g, n, a = by_room_marker(["law-2026.pdf"], "法学部", ROOM_LAW,
                             by_fac["法学部"], extra=missing, known=known)
    rooms.update(g); stats.append(("法学部", "B112/演習室2等", n, len(g), a))

    g, n, a = by_room_marker(["eng-eecs-c.pdf", "eng-eecs-d.pdf", "eng-civil.pdf",
                              "eng-eecs-a.pdf", "eng-eecs-b.pdf", "eng-civil-spring.pdf"],
                             "工学部", ROOM_PAREN, by_fac["工学部"])
    rooms.update(g); stats.append(("工学部", "（工学部第7）等", n, len(g), a))

    g, n, a = by_room_marker(["design-a.pdf", "design-a-spring.pdf"], "芸術工学部", ROOM_PAREN,
                             by_fac["芸術工学部"])
    rooms.update(g); stats.append(("芸術工学部", "（室番号）", n, len(g), a))

    stats.append(("基幹教育科目", "記載なし", 0, 0, 0))
    n = kyoso_missing(known, missing)
    stats.append(("共創学部", "記載なし(シラバスで通知)。未掲載の照合のみ", n, 0, 0))

    # 農学部は講義コードで判定できる。PDFにあってCampusmateに無いコードを拾う
    for season, line in lines_of("agr-2026.pdf"):
        for code in CODE.findall(line):
            if code not in idx:
                missing.setdefault("code:" + code, {
                    "title": "", "code": code, "room": "",
                    "faculty": "農学部", "source_url": PDFS.PAGE["agr-2026.pdf"],
                    "season": season or ""})
    return idx, rooms, stats, missing


def build(con, year=YEAR):
    """PDFを読んで data/ に教室と未掲載科目のJSONを書き出す。"""
    idx, rooms, stats, missing = match_all(con, year)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    # 同名が複数あって決められなかった件数も残す。捨てた数が見えないと、
    # あとで詰めるときの手がかりが無くなる
    payload = dict(rooms)
    payload["_meta"] = {
        "generated_for": year,
        "per_faculty": [{"faculty": f, "how": how, "detected": n,
                         "resolved": k, "dropped_ambiguous": a}
                        for f, how, n, k, a in stats],
    }
    (DATA_DIR / f"timetable-rooms-{year}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    (DATA_DIR / f"timetable-missing-{year}.json").write_text(
        json.dumps(sorted(missing.values(), key=lambda m: (m["faculty"], m["title"])),
                   ensure_ascii=False, indent=1), encoding="utf-8")
    return idx, rooms, stats, missing


if __name__ == "__main__":
    import random
    with DB.session() as con:
        idx, rooms, stats, missing = build(con)
    print(f"{'学部':14}{'教室の書き方':26}{'検出':>6}{'確定':>6}{'除外/同名':>9}")
    for f, how, n, k, a in stats:
        print(f"{f:14}{how:26}{n:6}{k:6}{a:9}")
    print()
    print(f"教室が付いた科目: {len(rooms)}")
    bym = collections.Counter(m["faculty"] for m in missing.values())
    print("時間割にあってCampusmateに無い科目の候補: "
          + " / ".join(f"{k} {v}" for k, v in bym.most_common()))
    print()
    print("教室の抜き取り確認 12件:")
    random.seed(1)
    for code in random.sample(sorted(rooms), min(12, len(rooms))):
        print(f"  {idx[code]['f']:8} {idx[code]['t'][:30]:32} → {rooms[code]['room']}")
