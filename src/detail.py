"""詳細ページの取得と本文抽出（仕様書2.1 / 実装順序3）。

ステートレスGET・Cookie不要。1件約520KB、うち99.4%はインラインJS。
生HTMLは保存せず、本文だけ残して即破棄する（仕様書5章）。
"""
import re, html, hashlib, time, sys, argparse, datetime as dt, threading
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote
import httpx
import db as DB
from config import KYUSHU, USER_AGENT, YEAR
from power import keep_awake

UID = KYUSHU["university_id"]

# 詳細ページには2つの様式がある（仕様書に記載なし・実測で発見）。
#   A: 全学の標準様式。'科目名称' で始まり '合理的配慮について' の手前で終わる（約520KB）
#   B: 医歯薬系などの旧様式。'講義科目名' で始まり 'PAGE TOP' の手前で終わる（約200KB）
#      B は '開講学部・学府' を持ち '対象学部等' が空、更新日付が末尾に来る
#   C: 医学部の統合科目（ユニット）。'授業科目名' で始まり 'PAGE TOP' の手前で終わる
#      C は本文中の表見出しに '科目名称' が現れるので、開始位置が最も早い様式を選ぶこと
LAYOUTS = [("A", "科目名称", "合理的配慮について"),
           ("B", "講義科目名", "PAGE TOP"),
           ("C", "授業科目名", "PAGE TOP")]

_UPD = re.compile(r"更新日付\s*\n\s*([0-9]{4}-[0-9]{2}-[0-9]{2}[ T][0-9:.]+)")


def url_for(code, year=YEAR):
    return KYUSHU["detail_url"].format(year=year, code=quote(str(code), safe=""), crclumcd=quote(KYUSHU["crclumcd"], safe=""))


def extract_body(raw_html: str):
    """(body, layout) を返す。どちらの様式にも当たらなければ ('', None)。"""
    t = re.sub(r"<script.*?</script>", "", raw_html, flags=re.S | re.I)
    t = re.sub(r"<style.*?</style>", "", t, flags=re.S | re.I)
    t = re.sub(r"<!--.*?-->", "", t, flags=re.S)
    t = re.sub(r"<br\s*/?>", "\n", t, flags=re.I)
    t = re.sub(r"</(td|tr|div|p|table|h[1-6]|li)>", "\n", t, flags=re.I)
    t = re.sub(r"<[^>]+>", "", t)
    t = html.unescape(t).replace("\u3000", " ")
    t = "\n".join(l.strip() for l in t.split("\n"))
    t = re.sub(r"\n{3,}", "\n\n", t).strip()
    hits = [(t.find(start), layout, end) for layout, start, end in LAYOUTS]
    hits = sorted((i, layout, end) for i, layout, end in hits if i >= 0)
    if not hits:
        return "", None
    i, layout, end = hits[0]
    j = t.find(end, i + 1)
    return (t[i:j] if j > i else t[i:]).strip(), layout


def updated_at_of(body: str):
    m = _UPD.search(body)
    return m.group(1) if m else None


def now():
    return dt.datetime.now().isoformat(timespec="seconds")


_lock = threading.Lock()
_local = threading.local()


def client(registry=None) -> httpx.Client:
    """スレッドごとに1本のコネクションを使い回す。毎回 Client を作ると
    TLSハンドシェイクのぶん約2倍遅くなる（実測 2.1s/件 -> 1.1s/件）。"""
    c = getattr(_local, "client", None)
    if c is None or c.is_closed:
        c = httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=120,
                         follow_redirects=True,
                         limits=httpx.Limits(max_connections=1, max_keepalive_connections=1))
        _local.client = c
        if registry is not None:
            with _lock:
                registry.append(c)
    return c


def fetch_one(client, code, year, pause):
    for attempt in range(3):
        try:
            r = client.get(url_for(code, year))
            r.raise_for_status()
            body, layout = extract_body(r.text)
            if not body:
                raise ValueError("本文が見つからない（既知のどの様式にも一致しない）")
            return (body, layout), len(r.content), None
        except Exception as e:
            if attempt == 2:
                return (None, None), 0, f"{type(e).__name__}: {e}"
            time.sleep(pause * (attempt + 1) * 2)


def run(year=YEAR, workers=4, pause=0.3, limit=None, refetch=False):
    if not 1 <= workers <= 5 or pause < 0 or (limit is not None and limit < 0):
        raise ValueError("workersは1〜5、pauseとlimitは0以上で指定してください")
    con = DB.init()
    if refetch:
        targets = [r[0] for r in con.execute(
            "SELECT course_code FROM course_index WHERE university_id=? AND year=? ORDER BY course_code",
            (UID, year))]
    else:
        targets = [r[0] for r in con.execute(
            "SELECT ci.course_code FROM course_index ci "
            "LEFT JOIN syllabus_raw sr ON sr.university_id=ci.university_id "
            "  AND sr.year=ci.year AND sr.course_code=ci.course_code "
            "WHERE ci.university_id=? AND ci.year=? AND sr.course_code IS NULL "
            "ORDER BY ci.course_code", (UID, year))]
    if limit is not None:
        targets = targets[:limit]
    print(f"[detail] 対象 {len(targets)}件 / 並列 {workers}")
    if not targets:
        con.close()
        return 0, 0, 0

    run_id = con.execute(
        "INSERT INTO crawl_runs (job,started_at,scope,n_target,status)"
        " VALUES ('detail',?,?,?,'running')",
        (now(), f"year={year} refetch={refetch}", len(targets))).lastrowid
    con.commit()

    t0 = time.time()
    stats = {"ok": 0, "changed": 0, "err": 0, "bytes": 0}
    errors = []
    clients = []

    def work(code):
        (body, layout), nbytes, err = fetch_one(client(clients), code, year, pause)
        time.sleep(pause)
        with _lock:
            if err:
                stats["err"] += 1
                errors.append((code, err))
            else:
                sha = hashlib.sha256(body.encode("utf-8")).hexdigest()
                upd = updated_at_of(body)
                prev = con.execute(
                    "SELECT body_sha256, updated_at FROM syllabus_raw WHERE university_id=?"
                    " AND year=? AND course_code=?", (UID, year, code)).fetchone()
                if prev is None or prev["body_sha256"] != sha:
                    stats["changed"] += 1
                    # 更新日付が動かない編集もあるため、本文ハッシュと両方を記録する
                    kind = ("new" if prev is None else
                            "updated_at" if prev["updated_at"] != upd else "body_only")
                    con.execute(
                        "INSERT INTO syllabus_change_log (university_id,year,course_code,"
                        "old_sha256,new_sha256,old_updated_at,new_updated_at,kind,detected_at)"
                        " VALUES (?,?,?,?,?,?,?,?,?)",
                        (UID, year, code, prev["body_sha256"] if prev else None, sha,
                         prev["updated_at"] if prev else None, upd, kind, now()))
                con.execute(
                    "INSERT OR REPLACE INTO syllabus_raw (university_id,year,course_code,body,"
                    "updated_at,body_sha256,fetched_at,layout) VALUES (?,?,?,?,?,?,?,?)",
                    (UID, year, code, body, upd, sha, now(), layout))
                stats["ok"] += 1
                stats["bytes"] += nbytes
            n = stats["ok"] + stats["err"]
            if n % 50 == 0:
                el = time.time() - t0
                con.commit()
                print(f"  {n}/{len(targets)}  {el:.0f}s  "
                      f"{n/el:.2f}件/s  残り{(len(targets)-n)/max(n/el,1e-9)/60:.0f}分  "
                      f"err={stats['err']}", flush=True)

    # 同時接続は workers 本に制限される（仕様書5章: 3〜5本まで）
    try:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            list(ex.map(work, targets))
    except BaseException:
        con.rollback()
        con.execute("UPDATE crawl_runs SET ended_at=?, status='aborted' WHERE run_id=?",
                    (now(), run_id))
        con.commit()
        con.close()
        raise
    finally:
        for c in clients:
            c.close()

    con.execute("UPDATE crawl_runs SET ended_at=?, n_fetched=?, n_changed=?, n_error=?, bytes=?,"
                " note=?, status=? WHERE run_id=?",
                (now(), stats["ok"], stats["changed"], stats["err"], stats["bytes"],
                 f"{time.time()-t0:.0f}s", "failed" if errors else "ok", run_id))
    con.commit()
    el = time.time() - t0
    print(f"\n[detail] 成功 {stats['ok']} / 変化 {stats['changed']} / 失敗 {stats['err']} / "
          f"{el/60:.1f}分 / 転送 {stats['bytes']/1e9:.2f} GB")
    if errors:
        print("[detail] 失敗した講義コード（先頭20件）:")
        for c, e in errors[:20]:
            print("   ", c, e)
    con.close()
    if errors:
        raise RuntimeError(f"本文取得が{len(errors)}件失敗しました。再実行してください")
    return stats["ok"], stats["changed"], stats["err"]


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", type=int, default=YEAR)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--pause", type=float, default=0.3)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--refetch", action="store_true", help="取得済みも取り直す（週次巡回）")
    a = ap.parse_args()
    with keep_awake():
        run(a.year, a.workers, a.pause, a.limit, a.refetch)
