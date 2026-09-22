"""大学ごとに変わる設定。移植時はここだけ差し替える（仕様書8章）。"""
from pathlib import Path
import os

DB_PATH = Path(os.environ.get("SYLLABUS_DB_PATH", Path.home() / "syllabus-db" / "kyudai.db")).expanduser()
EXPORT_DIR = Path(os.environ.get("SYLLABUS_EXPORT_DIR", Path(__file__).resolve().parent / "exports")).expanduser()
# 外から持ってきた素材の置き場。Campusmateに無い情報（文学部のシラバス、各学部の
# 時間割表から取った教室、基幹教育B表の事前申請）はここに置く。
# EXPORT_DIR は毎回作り直せる出力専用にしたいので、入力を混ぜない。
DATA_DIR = Path(os.environ.get("SYLLABUS_DATA_DIR", Path(__file__).resolve().parent / "data")).expanduser()
PDF_DIR = DATA_DIR / "pdf"

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36")

KYUSHU = {
    "university_id": "kyushu-u",
    "name": "九州大学",
    "adapter": "campusmate",
    "origin": "https://ku-portal.kyushu-u.ac.jp",
    "base_url": "https://ku-portal.kyushu-u.ac.jp/campusweb/",
    "search_entry": ("https://ku-portal.kyushu-u.ac.jp/campusweb/slbsskgr.do"
                     "?clearAccessData=true&contenam=slbsskgr&kjnmnNo=7"),
    "detail_url": ("https://ku-portal.kyushu-u.ac.jp/campusweb/slbssbdr.do"
                   "?value(risyunen)={year}&value(semekikn)=1"
                   "&value(kougicd)={code}&value(crclumcd)={crclumcd}"),
    "crclumcd": "ZZ",                      # 九大の汎用値
    "course_code_re": r"\b2\d{7}[a-zA-Z]?\b",  # 末尾英字サフィックス込み
    "requires_login": False,
    # 検索条件は必須。開講時期(kaikoCd)を軸にスイープする
    "required_search_field": "value(kaikoCd)",
    "body_start": "科目名称",
    "body_end": "合理的配慮について",
}

# 開講時期コード（仕様書2.3）
KAIKO = {
    "20": ("後",        "autumn"), "23": ("秋学期",   "autumn"),
    "24": ("冬学期",    "autumn"), "93": ("後期集中", "autumn"),
    "21": ("後期前半",  "autumn"), "22": ("後期後半", "autumn"),
    "96": ("秋期",      "autumn"), "98": ("秋期集中", "autumn"),
    "10": ("前",        "spring"), "14": ("夏学期",   "spring"),
    "13": ("春学期",    "spring"), "92": ("前期集中", "spring"),
    "95": ("春期",      "spring"), "97": ("春期集中", "spring"),
    "11": ("前期前半",  "spring"), "12": ("前期後半", "spring"),
    "00": ("通年",      "full"),   "91": ("通年集中", "full"),
}
AUTUMN = [k for k, v in KAIKO.items() if v[1] == "autumn"]
SPRING = [k for k, v in KAIKO.items() if v[1] == "spring"]
FULLYEAR = [k for k, v in KAIKO.items() if v[1] == "full"]

# Phase 1 の初回対象: 後期系8値 + 通年系2値
PHASE1_KAIKO = AUTUMN + FULLYEAR

YEAR = 2026

# 講義名の末尾括弧に埋め込まれる学科指定（仕様書2.1）。
# 実測では後期系にこの書式は2件しかなく、括弧の中身は大半が副題や英語表記。
# 誤検出を避けるため明示リストに限定する。新しい略号が見つかったら追記すること。
DEPARTMENT_CODES = {"経経", "経工"}

# 授業科目区分の英語表記。基幹教育の国際コース（英語開講）が別名で入っている。
# 中身を確認して同一と判断したものだけを寄せる（Subjects in Science は微積分・線形代数・
# 物理学基礎など、Subjects in Humanities... は哲学入門・法学入門など）
CATEGORY_ALIASES = {
    "Subjects in Science": "理系ディシプリン科目",
    "Subjects in Humanities and Social Science": "文系ディシプリン科目",
    "Cybersecurity": "サイバーセキュリティ科目",
}

# 授業科目区分の統合。左を右にまとめる。
# 実データを見て「同じものが違う名前で入っているだけ」と確認したものだけを入れること。
CATEGORY_MERGES = {
    # 表記ゆれ。括弧の書き方が3通りあるだけ（留学デザイン講座・ワンヘルスなど）
    "総合科目（オープン）": "総合科目",
    "総合科目（オープン科目）": "総合科目",
    "総合科目【オープン】": "総合科目",
    # 下位区分。中身はプログラミング演習(P)
    "理系ディシプリン科目 専門基礎系": "理系ディシプリン科目",
    # コース名が付いただけ（芸工の応用音楽表現演習）
    "コース専門科目：未来構想デザインコース": "コース専門科目",
    # 学部の接頭辞。学部フィルタがあるので冗長。1年次限定は grades 列に残る
    "（農）専攻教育科目": "専攻教育科目",
    "（農）１年次専攻教育科目": "専攻教育科目",
    # 保健学科だけ呼称が違う（コミュニケーション論・地域在宅看護概論など）
    "専門教育科目": "専攻教育科目",
    # 区分欄に学部名が入った記入ミス（口腔病理学）
    "歯学部": "",
}

# 経済学部は学科ごとに扱いが違うため「（経済工学科）選択必修科目／（経済・経営学科）自由選択科目」
# のような複合ラベルになり、98件が13区分に割れている。先頭の区分だけを採って学科の括弧を落とす。
# 丸めると学科ごとの違いは消えるが、その情報は required 列と category_raw 列に残る
CATEGORY_SPLIT_FACULTIES = {"経済学部"}

# 学部の表示順。九大の正式な学部順に、全員が取る基幹教育科目を先頭に置く。
# 文学部は独自システムのためシラバスDBには入らないが、順番だけ用意しておく。
# ここに無い学部は末尾に五十音順で並ぶ
FACULTY_ORDER = [
    "基幹教育科目",
    "文学部", "教育学部", "法学部", "経済学部", "理学部",
    "医学部医学科", "医学部生命科学科", "医学部保健学科",
    "歯学部", "薬学部", "工学部", "芸術工学部", "農学部", "共創学部",
    "留学生センター",
]

# これ以下の件数の区分は「その他」に畳む。学部で絞れば中身は見当がつくため、
# 選択肢を短く保つ方を優先する。元の区分名は category_raw に残る
CATEGORY_MIN_COUNT = 6
CATEGORY_OTHER = "その他"

# 開講学期のまとめ方。春・夏と半期前半/後半も分類する。
TERM_GROUPS = [
    ("前期",      ["前", "前期", "前期集中"]),
    ("春学期",    ["春学期", "春期", "春期集中", "前期前半"]),
    ("夏学期",    ["夏学期", "前期後半"]),
    ("後期",      ["後", "後期", "後期集中"]),
    ("秋学期",    ["秋学期", "秋期", "秋期集中", "後期前半"]),
    ("冬学期",    ["冬学期", "後期後半"]),
    ("通年・その他", ["通年", "通年集中"]),
]

# 学府（大学院）を示す語。対象学部等がこれに当たるものを除外する
GRADUATE_MARKERS = ("学府", "大学院", "府共通", "専門職")
