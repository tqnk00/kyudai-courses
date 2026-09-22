"""検索サイトのHTMLを組み立てる。

site_template.html の /*__DATA__*/ に site_data.py が作ったJSONを流し込む。
ローカルでも読めるUTF-8の単一HTML文書を生成する。
"""
import sys, argparse
from pathlib import Path
import site_data
from config import EXPORT_DIR, YEAR

HERE = Path(__file__).resolve().parent
TEMPLATE = HERE / "site_template.html"
PLACEHOLDER = "/*__DATA__*/"


def build(year=YEAR, undergrad_only=True):
    json_path = site_data.build(year, undergrad_only)
    payload = json_path.read_text(encoding="utf-8")
    # インラインJSONの中に </script> が現れると script が閉じてしまう。
    # JSON文字列としては \/ も / と等価なので、エスケープしても内容は変わらない
    payload = payload.replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")

    html = TEMPLATE.read_text(encoding="utf-8")
    if html.count(PLACEHOLDER) != 1:
        raise SystemExit(f"テンプレートの {PLACEHOLDER} は1個必要です")
    html = html.replace(PLACEHOLDER, payload)

    out = EXPORT_DIR / f"kyudai-courses-{year}.html"
    out.write_text(html, encoding="utf-8")
    print(f"[build_site] {out} ({out.stat().st_size/1e6:.2f} MB)")
    return out


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", type=int, default=YEAR)
    ap.add_argument("--all", action="store_true")
    a = ap.parse_args()
    build(a.year, not a.all)
