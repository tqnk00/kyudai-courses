"""一覧スイープ（仕様書2.4 / 実装順序2）。

開講学部のチェックは一切入れない。全45区分チェックでは 159件 取りこぼすため。
学部への絞り込みは詳細取得後（extract.py）に行う。
"""
import sys, time, datetime as dt, argparse
import campusmate as cm
import db as DB
from config import KYUSHU, KAIKO, PHASE1_KAIKO, AUTUMN, SPRING, FULLYEAR, YEAR
from power import keep_awake

UID = KYUSHU["university_id"]

SETS = {"phase1": PHASE1_KAIKO, "autumn": AUTUMN, "spring": SPRING,
        "full": FULLYEAR, "all": list(KAIKO)}


def now():
    return dt.datetime.now().isoformat(timespec="seconds")


def store(con, year, kaiko_cd, rows):
    ts = now()
    new = 0
    for r in rows:
        cur = con.execute(
            "SELECT course_code FROM course_index WHERE university_id=? AND year=? AND course_code=?",
            (UID, year, r["course_code"])).fetchone()
        if cur is None:
            new += 1
            con.execute(
                "INSERT INTO course_index (university_id,year,course_code,title,term_slots,"
                "instructors,kaiko_cd,first_seen,last_seen) VALUES (?,?,?,?,?,?,?,?,?)",
                (UID, year, r["course_code"], r["title"], r["term_slots"],
                 r["instructors"], kaiko_cd, ts, ts))
        else:
            # 同じ科目が複数の開講時期で拾われることがある。kaiko_cd は追記する
            con.execute(
                "UPDATE course_index SET title=?, term_slots=?, instructors=?, last_seen=?,"
                " kaiko_cd = CASE WHEN ','||kaiko_cd||',' LIKE ? THEN kaiko_cd"
                "                 ELSE kaiko_cd||','||? END"
                " WHERE university_id=? AND year=? AND course_code=?",
                (r["title"], r["term_slots"], r["instructors"], ts,
                 f"%,{kaiko_cd},%", kaiko_cd, UID, year, r["course_code"]))
    con.commit()
    return new


def run(year=YEAR, which="phase1", pause=1.0):
    codes = SETS[which]
    con = DB.init()
    run_id = con.execute(
        "INSERT INTO crawl_runs (job,started_at,scope,status)"
        " VALUES ('sweep',?,?,'running')",
        (now(), f"{which}:{','.join(codes)}")).lastrowid
    con.commit()
    t0 = time.time()
    sess = cm.SearchSession()
    total_rows = n_err = 0
    try:
        for kc in codes:
            label = KAIKO[kc][0]
            print(f"[sweep] kaikoCd={kc} ({label})")
            try:
                rows, total = cm.sweep_kaiko(sess, year, kc, pause=pause)
            except Exception as e:
                n_err += 1
                print(f"  !! {e}")
                sess.close(); sess = cm.SearchSession()
                continue
            if total and len(rows) != total:
                print(f"  ! 件数不一致: 取得{len(rows)} / 表示{total}")
            new = store(con, year, kc, rows)
            total_rows += len(rows)
            print(f"  -> {len(rows)}件 (新規 {new})")
            time.sleep(pause)
    finally:
        sess.close()
    uniq = con.execute(
        "SELECT COUNT(*) FROM course_index WHERE university_id=? AND year=?",
        (UID, year)).fetchone()[0]
    con.execute("UPDATE crawl_runs SET ended_at=?, n_target=?, n_fetched=?, n_error=?, note=?, "
                "status=? WHERE run_id=?",
                (now(), total_rows, uniq, n_err, f"{time.time()-t0:.0f}s",
                 "failed" if n_err else "ok", run_id))
    con.commit()
    print(f"\n[sweep] 延べ {total_rows}件 / ユニーク {uniq}件 / エラー {n_err} / {time.time()-t0:.0f}秒")
    con.close()
    if n_err:
        raise RuntimeError(f"一覧取得が{n_err}条件で失敗しました。再実行してください")
    return uniq


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", default="phase1", choices=list(SETS))
    ap.add_argument("--year", type=int, default=YEAR)
    ap.add_argument("--pause", type=float, default=1.0)
    a = ap.parse_args()
    with keep_awake():
        run(a.year, a.set, a.pause)
