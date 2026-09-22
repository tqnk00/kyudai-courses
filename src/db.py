"""スキーマ定義と接続。仕様書4章のテーブルをそのまま起こす。"""
import sqlite3
from contextlib import contextmanager


@contextmanager
def session():
    con = init()
    try:
        with con:
            yield con
    finally:
        con.close()

from config import DB_PATH, KYUSHU

SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS universities (
  university_id TEXT PRIMARY KEY,
  name          TEXT NOT NULL,
  base_url      TEXT NOT NULL,
  adapter       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS course_index (
  university_id TEXT NOT NULL,
  year          INTEGER NOT NULL,
  course_code   TEXT NOT NULL,
  title         TEXT,
  term_slots    TEXT,
  instructors   TEXT,
  kaiko_cd      TEXT,
  first_seen    TEXT,
  last_seen     TEXT,
  PRIMARY KEY (university_id, year, course_code)
);

CREATE TABLE IF NOT EXISTS syllabus_raw (
  university_id TEXT NOT NULL,
  year          INTEGER NOT NULL,
  course_code   TEXT NOT NULL,
  body          TEXT NOT NULL,
  updated_at    TEXT,
  body_sha256   TEXT NOT NULL,
  fetched_at    TEXT NOT NULL,
  layout        TEXT,                   -- 'A'(科目名称様式) | 'B'(講義科目名様式)
  PRIMARY KEY (university_id, year, course_code)
);

CREATE TABLE IF NOT EXISTS course_structured (
  university_id TEXT NOT NULL,
  year          INTEGER NOT NULL,
  course_code   TEXT NOT NULL,
  numbering     TEXT,
  title         TEXT,
  subtitle      TEXT,
  faculty       TEXT,                   -- 学部名（A:学部カテゴリ / B:開講学部・学府）
  faculty_raw   TEXT,                   -- 対象学部等の生値。学年が入っていることが多い
  department    TEXT,                   -- 学科指定（DEPARTMENT_CODES に一致したもののみ）
  title_suffix  TEXT,                   -- 講義名の末尾括弧の生値。副題・英語表記も含む
  target_grade  TEXT,                   -- 対象学年の生値（67通りある）
  grades        TEXT,                   -- 正規化した学年 '1,2,3,4'。不明は全学年扱い
  credits       REAL,
  required      TEXT,
  term          TEXT,
  language      TEXT,
  campus        TEXT,
  instructors   TEXT,
  is_undergrad  INTEGER,
  is_intensive  INTEGER,
  is_online     INTEGER,
  eval_exam     INTEGER,
  eval_report   INTEGER,
  eval_quiz     INTEGER,
  eval_attend   INTEGER,
  eval_other    TEXT,
  prereq        TEXT,
  keywords      TEXT,
  goals         TEXT,
  plan          TEXT,
  -- この行を作った本文のハッシュ。syllabus_raw.body_sha256 と食い違う＝再抽出が必要
  extracted_sha256 TEXT,
  PRIMARY KEY (university_id, year, course_code)
);
CREATE INDEX IF NOT EXISTS ix_cs_faculty ON course_structured(university_id, year, faculty);
CREATE INDEX IF NOT EXISTS ix_cs_undergrad ON course_structured(university_id, year, is_undergrad);

CREATE TABLE IF NOT EXISTS course_slots (
  university_id TEXT NOT NULL,
  year          INTEGER NOT NULL,
  course_code   TEXT NOT NULL,
  term          TEXT,
  weekday       TEXT,
  period        TEXT,
  seq           INTEGER
);
CREATE INDEX IF NOT EXISTS ix_slots_code ON course_slots(university_id, year, course_code);
CREATE INDEX IF NOT EXISTS ix_slots_wd ON course_slots(university_id, year, weekday, period);

CREATE TABLE IF NOT EXISTS timetable_facts (
  university_id TEXT NOT NULL,
  year          INTEGER NOT NULL,
  course_code   TEXT,
  source        TEXT NOT NULL,
  room          TEXT,
  room_raw      TEXT,
  intensive_dates TEXT,
  application   TEXT,
  class_group   TEXT,
  note          TEXT,
  match_status  TEXT NOT NULL,
  match_score   REAL,
  raw_row       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS course_summary (
  university_id TEXT NOT NULL,
  year          INTEGER NOT NULL,
  course_code   TEXT NOT NULL,
  summary       TEXT,
  topics        TEXT,
  model         TEXT,
  prompt_version TEXT,
  source_sha256 TEXT,
  generated_at  TEXT,
  PRIMARY KEY (university_id, year, course_code)
);

-- 投入済みのAI要約バッチ。送信した時点で課金されるので、IDと対象を必ず残す。
-- 途中で落ちても次回ここから回収でき、同じ本文を二重に送らずに済む
CREATE TABLE IF NOT EXISTS summary_batches (
  batch_id      TEXT PRIMARY KEY,
  university_id TEXT NOT NULL,
  year          INTEGER NOT NULL,
  submitted_at  TEXT NOT NULL,
  collected_at  TEXT,
  status        TEXT NOT NULL,   -- 'submitted' | 'collected' | 'expired'
  n             INTEGER
);

CREATE TABLE IF NOT EXISTS summary_batch_items (
  batch_id      TEXT NOT NULL,
  course_code   TEXT NOT NULL,
  source_sha256 TEXT,
  PRIMARY KEY (batch_id, course_code)
);

-- 本文が変わった科目の記録。仕様書6章「週あたりの更新件数」を実測するため
CREATE TABLE IF NOT EXISTS syllabus_change_log (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  university_id TEXT NOT NULL,
  year          INTEGER NOT NULL,
  course_code   TEXT NOT NULL,
  old_sha256    TEXT,
  new_sha256    TEXT NOT NULL,
  old_updated_at TEXT,
  new_updated_at TEXT,
  kind          TEXT NOT NULL,   -- 'new' | 'updated_at' | 'body_only'
  detected_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_chg_at ON syllabus_change_log(detected_at);

-- クロール実績。所要時間・件数の実測を残す
CREATE TABLE IF NOT EXISTS crawl_runs (
  run_id     INTEGER PRIMARY KEY AUTOINCREMENT,
  job        TEXT NOT NULL,
  started_at TEXT NOT NULL,
  ended_at   TEXT,
  scope      TEXT,
  n_target   INTEGER,
  n_fetched  INTEGER,
  n_changed  INTEGER,
  n_error    INTEGER,
  bytes      INTEGER,
  note       TEXT,
  status     TEXT              -- 'running' | 'ok' | 'failed' | 'aborted'
);
"""

def assert_extracted(con, university_id, year):
    """本文が更新されたのに再抽出されていない科目があれば止める。

    detail.run が途中で失敗すると syllabus_raw だけが新しくなる。その状態で
    site や report を回すと、新しい本文を持っているのに古い属性で配布物ができる。
    """
    n = con.execute(
        "SELECT COUNT(*) FROM syllabus_raw sr"
        " LEFT JOIN course_structured st ON st.university_id=sr.university_id"
        "   AND st.year=sr.year AND st.course_code=sr.course_code"
        " WHERE sr.university_id=? AND sr.year=?"
        "   AND st.extracted_sha256 IS NOT sr.body_sha256",
        (university_id, year)).fetchone()[0]
    if n:
        raise RuntimeError(
            f"本文と抽出結果が食い違う科目が {n} 件あります。"
            "`python run.py extract` を実行してから生成し直してください")


def connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=60, check_same_thread=False)  # 書き込みは呼び出し側でロックする
    con.row_factory = sqlite3.Row
    return con

MIGRATIONS = [
    ("syllabus_raw", "layout", "ALTER TABLE syllabus_raw ADD COLUMN layout TEXT"),
    ("course_structured", "faculty_raw",
     "ALTER TABLE course_structured ADD COLUMN faculty_raw TEXT"),
    ("course_structured", "title_suffix",
     "ALTER TABLE course_structured ADD COLUMN title_suffix TEXT"),
    ("course_structured", "grades",
     "ALTER TABLE course_structured ADD COLUMN grades TEXT"),
    ("course_structured", "category",
     "ALTER TABLE course_structured ADD COLUMN category TEXT"),
    ("course_structured", "term_group",
     "ALTER TABLE course_structured ADD COLUMN term_group TEXT"),
    ("course_structured", "category_raw",
     "ALTER TABLE course_structured ADD COLUMN category_raw TEXT"),
    ("course_structured", "extracted_sha256",
     "ALTER TABLE course_structured ADD COLUMN extracted_sha256 TEXT"),
    ("crawl_runs", "status", "ALTER TABLE crawl_runs ADD COLUMN status TEXT"),
]


def migrate(con):
    for table, col, sql in MIGRATIONS:
        cols = {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
        if col not in cols:
            con.execute(sql)


def init():
    con = connect()
    con.executescript(SCHEMA)
    migrate(con)
    con.execute(
        "INSERT OR REPLACE INTO universities VALUES (?,?,?,?)",
        (KYUSHU["university_id"], KYUSHU["name"], KYUSHU["base_url"], KYUSHU["adapter"]),
    )
    con.commit()
    return con

if __name__ == "__main__":
    con = init()
    names = [r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    print("DB:", DB_PATH)
    print("tables:", ", ".join(names))
