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
  python run.py deploy       ビルド結果（index.html と details-年度/）を公開リポジトリの直下に置く
  python run.py update       ★ふだんの更新はこれ。取り直し → 生成 → 配置 → テスト → 公開するか確認

いずれもクロール中はスリープを抑止する（電源設定は変更しない）。
"""
import sys, re, shutil, subprocess, argparse, datetime as dt
from pathlib import Path
import db as DB
import sweep, detail, extract, report, build_site, site_data
from config import KYUSHU, YEAR, EXPORT_DIR, SITE_DIR
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


def site_count(path):
    """index.html に入っている科目数。読めなければ None。"""
    try:
        m = re.search(r'"meta":\{.{0,300}?"n":(\d+)', path.read_text(encoding="utf-8"))
        return int(m.group(1)) if m else None
    except OSError:
        return None


def deploy(year=YEAR):
    """ビルド結果を公開リポジトリの直下に置く。push はしない。

    置くのは index.html と details-<year>/ の2つ。詳細の分割ファイルを置き忘れると
    詳細が開けなくなるので、必ず組で入れ替える。
    """
    html = EXPORT_DIR / f"kyudai-courses-{year}.html"
    det = EXPORT_DIR / f"details-{year}"
    n_shards = len(list(det.glob("*.json"))) if det.exists() else 0
    if not html.exists() or n_shards != site_data.DETAIL_SHARDS:
        raise SystemExit(f"[deploy] ビルド結果がそろっていません（{html.name} と詳細 "
                         f"{n_shards}/{site_data.DETAIL_SHARDS} ファイル）。先に python run.py site")
    before = site_count(SITE_DIR / "index.html")
    shutil.copyfile(html, SITE_DIR / "index.html")
    # フォルダごと消して作り直すと、OneDrive が掴んでいて失敗する（WinError 5）。
    # フォルダは残し、ファイル単位で入れ替える
    dest = SITE_DIR / det.name
    dest.mkdir(exist_ok=True)
    names = {p.name for p in det.glob("*.json")}
    for old in dest.glob("*.json"):
        if old.name not in names:
            old.unlink()
    for name in sorted(names):
        shutil.copyfile(det / name, dest / name)
    after = site_count(SITE_DIR / "index.html")
    print(f"[deploy] {SITE_DIR} に配置（科目数 {before} -> {after}）")
    return before, after


def git(*args):
    return subprocess.run(["git", "-C", str(SITE_DIR), *args],
                          capture_output=True, text=True, encoding="utf-8")


def update(year=YEAR, workers=4, skip_lit=False, yes=False):
    """データを取り直して公開の手前まで進める。最後に公開するかを聞く。

    学期中に教室変更や追加開講を反映したいとき、これ1つで済むようにしてある。
      一覧と新規科目の本文 → 文学部 → 時間割PDF（教室・事前申請）→ サイト生成 → 配置 → テスト
    """
    with keep_awake():
        sweep.run(year, "all")
        fetch_then_extract(year, workers)
        if not skip_lit:
            import lit
            try:
                lit.build(year)
            except Exception as e:      # 文学部のサイトが落ちていても、ほかの更新は続ける
                print(f"[update] !! 文学部は更新できませんでした（前回のデータを使います）: {e}")
        rooms(year, refresh=True)
        build_site.build(year, True)
    before, after = deploy(year)

    here = Path(__file__).resolve().parent
    t = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests"],
                       cwd=here, capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    if t.returncode != 0:
        print(t.stderr[-2000:])
        raise SystemExit("[update] !! テストが失敗しました。公開はしていません")
    print("[update] テスト OK")

    changed = git("status", "--short").stdout.strip()
    if not changed:
        print("[update] 変更はありませんでした。公開中の内容が最新です")
        return
    print("[update] 変更されたファイル:")
    print("\n".join("    " + ln for ln in changed.splitlines()[:12]))
    print(f"[update] 科目数 {before} -> {after}")
    if not yes:
        try:
            ans = input("この内容を公開しますか？ (y で公開 / それ以外は公開しない): ").strip().lower()
        except EOFError:        # 入力できない場所（タスクなど）から呼ばれた。勝手には公開しない
            ans = ""
        if ans != "y":
            print("[update] 公開していません。あとで公開するなら publish フォルダで git push まで行ってください")
            return
    git("add", "-A")
    c = git("commit", "-m", f"データ更新（{before}件 -> {after}件）")
    if c.returncode != 0:
        raise SystemExit("[update] !! commit に失敗: " + (c.stdout + c.stderr)[-500:])
    p = git("push")
    if p.returncode != 0:
        raise SystemExit("[update] !! push に失敗: " + (p.stdout + p.stderr)[-500:])
    print("[update] 公開しました。1〜2分で https://tqnk00.github.io/kyudai-courses/ に反映されます")


def positive_int(value):
    n = int(value)
    if n < 1:
        raise argparse.ArgumentTypeError("1以上の整数を指定してください")
    return n


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("job", choices=["init", "daily", "weekly", "full", "extract",
                                    "summarize", "report", "changes", "site", "rooms",
                                    "deploy", "update"])
    ap.add_argument("--year", type=int, default=YEAR)
    ap.add_argument("--set", default="all", choices=list(sweep.SETS))
    ap.add_argument("--workers", type=positive_int, choices=range(1, 6), default=4)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--refresh", action="store_true",
                    help="rooms: PDFを全部取り直す（既定は未取得のものだけ）")
    ap.add_argument("--days", type=positive_int, default=14)
    ap.add_argument("--skip-lit", action="store_true", help="update: 文学部の取り直しを省く（約9分短い）")
    ap.add_argument("--yes", action="store_true", help="update: 確認せずに公開まで進める")
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
    if a.job == "deploy":
        deploy(a.year); return
    if a.job == "update":
        update(a.year, a.workers, a.skip_lit, a.yes); return
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
