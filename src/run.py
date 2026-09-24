"""ジョブのまとめ役（仕様書5章）。

  python run.py init         DB作成
  python run.py daily        一覧スイープ + 新規科目の本文取得 + 抽出（軽い。日次）
  python run.py weekly       本文の全件巡回で差分検出 + 抽出（重い。週次・深夜）
  python run.py full         初回一式（sweep -> detail -> extract -> report -> export）
  python run.py extract      再抽出のみ（ネットワークアクセスなし）
  python run.py summarize    AI要約（差分のみ）。--dry-run で件数とコストの見積もり
  python run.py report       実測レポート + エクスポート
  python run.py site         検索サイトのHTMLを組み立てる（ネットワークアクセスなし）
  python run.py rooms        各学部の時間割表PDFを取り直し、教室と事前申請を作り直す
  python run.py changes      直近の更新件数

いずれもクロール中はスリープを抑止する（電源設定は変更しない）。
"""
import sys, argparse, datetime as dt
import db as DB
import sweep, detail, extract, report, build_site
from config import KYUSHU, YEAR
from power import keep_awake

UID = KYUSHU["university_id"]


def changes(year=YEAR, days=14):
    with DB.session() as con:
        since = (dt.datetime.now() - dt.timedelta(days=days)).isoformat(timespec="seconds")
        print(f"[changes] 直近{days}日の本文変化")
        for r in con.execute(
                "SELECT date(detected_at) d, kind, COUNT(*) n FROM syllabus_change_log "
                "WHERE university_id=? AND year=? AND detected_at>=? "
                "GROUP BY d, kind ORDER BY d", (UID, year, since)):
            print(f"  {r['d']}  {r['kind']:<11} {r['n']}件")
        tot = con.execute(
            "SELECT COUNT(*) FROM syllabus_change_log WHERE university_id=? AND year=? "
            "AND detected_at>=? AND kind<>'new'", (UID, year, since)).fetchone()[0]
        print(f"[changes] 新規を除く更新: {tot}件 / {days}日")
        return tot


def rooms(year=YEAR, refresh=False):
    """各学部の時間割表から、教室と事前申請を作り直す。

    出どころが学期ごとに差し替わるPDFなので、取り直す手順をコードに載せておく。
    これを忘れると site_data が古いJSONを使い続ける。
    """
    import timetable_pdfs, timetable_econ, timetable_rooms, core_btable
    misses = timetable_pdfs.fetch(refresh)
    if "econ-2026.pdf" not in misses:
        timetable_econ.build()
    core_btable.build(year)
    with DB.session() as con:
        _, got, stats, missing = timetable_rooms.build(con, year)
    print(f"[rooms] 教室 {len(got)}件 / 時間割のみの科目 {len(missing)}件")
    if misses:
        print(f"[rooms] !! 取れなかったPDF: {', '.join(misses)}")
        print("[rooms] !! 学部のページで新しいURLを確認して timetable_pdfs.py を直す")
    return got, missing


def fetch_then_extract(year, workers, refetch=False):
    """本文取得のあと、失敗していても必ず再抽出してから例外を送り直す。

    detail.run は成功分を保存してから失敗を投げる。そこで止まると
    syllabus_raw だけが新しくなり、course_structured が古いまま残る。
    先に抽出を揃えておけば、あとで site や report を単体で回しても食い違わない。
    """
    try:
        detail.run(year, workers, refetch=refetch)
    finally:
        extract.run(year)


def positive_int(value):
    n = int(value)
    if n < 1:
        raise argparse.ArgumentTypeError("1以上の整数を指定してください")
    return n


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("job", choices=["init", "daily", "weekly", "full", "extract",
                                    "summarize", "report", "changes", "site", "rooms"])
    ap.add_argument("--year", type=int, default=YEAR)
    ap.add_argument("--set", default="all", choices=list(sweep.SETS))
    ap.add_argument("--workers", type=positive_int, choices=range(1, 6), default=4)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--refresh", action="store_true",
                    help="rooms: PDFを全部取り直す（既定は未取得のものだけ）")
    ap.add_argument("--days", type=positive_int, default=14)
    a = ap.parse_args()

    if a.job == "init":
        DB.init(); print("DB 初期化完了")
        return
    if a.job == "extract":
        extract.run(a.year); return
    if a.job == "site":
        build_site.build(a.year, not a.all); return
    if a.job == "rooms":
        rooms(a.year, a.refresh); return
    if a.job == "report":
        report.report(a.year); report.export(a.year, not a.all); return
    if a.job == "changes":
        changes(a.year, a.days); return
    if a.job == "summarize":
        import summarize
        summarize.run(a.year, not a.all, None, a.dry_run); return

    with keep_awake():
        if a.job == "daily":
            sweep.run(a.year, a.set)
            fetch_then_extract(a.year, a.workers)   # 未取得＝新規開講のみ
            build_site.build(a.year, not a.all)
        elif a.job == "weekly":
            sweep.run(a.year, a.set)
            fetch_then_extract(a.year, a.workers, refetch=True)
            build_site.build(a.year, not a.all)
            changes(a.year, 7)
        elif a.job == "full":
            sweep.run(a.year, a.set)
            fetch_then_extract(a.year, a.workers)
            report.report(a.year)
            report.export(a.year, not a.all)
            build_site.build(a.year, not a.all)


if __name__ == "__main__":
    main()
