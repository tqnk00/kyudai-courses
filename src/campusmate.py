"""Campusmate の検索フォーム操作。

仕様書2.2は「ブラウザ自動化が確実」としているが、実測の結果 素のHTTPで動く。
鍵は2つ:
  1. 検索ボタンの JS `exec()` は buttonName を 'searchKougi' に書き換えて submit する
     だけ。空の buttonName で POST するとエラー殻(8.5KB)が返る。
  2. 表示件数は doPaging() が value(maxCount) を書き換える。200件にする最初の
     リクエストは value(pageCount) を空にすること。ページ番号を入れると無視される。
セッションCookieは必要（GETでエントリを踏んで取得）。
"""
import re, time, html
from urllib.parse import urlencode, urljoin, urlparse
import httpx
from config import KYUSHU, USER_AGENT

ORIGIN = KYUSHU["origin"]
ENTRY = KYUSHU["search_entry"]
FORM_HDR = {"Content-Type": "application/x-www-form-urlencoded"}

_ACTION = re.compile(r'name="sylbsActionForm"[^>]*action="([^"]+)"')
_TS = re.compile(r'name="timestamp"\s+value="(\d+)"')
_ROW = re.compile(r'<tr class="column_(?:odd|even)"[^>]*>(.*?)</tr>', re.S)
_TD = re.compile(r'<td[^>]*>(.*?)</td>', re.S)
_HITS = re.compile(r'(\d+)\s*-\s*(\d+)件表示\s*/\s*(\d+)件中')
_ERRPAGE = "システムが期待しない操作"


def _text(frag: str) -> str:
    frag = re.sub(r'<br\s*/?>', "\n", frag, flags=re.I)
    frag = re.sub(r"<[^>]+>", "", frag)
    return html.unescape(frag).replace("\u3000", " ").strip()


class SearchSession:
    def __init__(self, client: httpx.Client | None = None):
        self.c = client or httpx.Client(
            headers={"User-Agent": USER_AGENT}, timeout=120, follow_redirects=True)
        self.last = ""

    def close(self):
        self.c.close()

    def _post(self, prev_html: str, fields: list[tuple[str, str]]) -> str:
        action_match, ts_match = _ACTION.search(prev_html), _TS.search(prev_html)
        if not action_match or not ts_match:
            raise RuntimeError("検索フォームまたはtimestampが見つからない")
        action = urljoin(ENTRY, html.unescape(action_match.group(1)))
        if urlparse(action).netloc != urlparse(ORIGIN).netloc or urlparse(action).scheme != "https":
            raise RuntimeError("検索フォームの送信先が想定と異なる")
        ts = ts_match.group(1)
        body = urlencode([("timestamp", ts)] + fields)
        r = self.c.post(action, content=body, headers=FORM_HDR | {"Referer": action})
        r.raise_for_status()
        self.last = r.text
        return r.text

    def open_form(self) -> str:
        r = self.c.get(ENTRY)
        r.raise_for_status()
        self.last = r.text
        return r.text

    def search(self, year: int, kaiko_cd: str) -> str:
        """開講時期だけを条件に検索する。開講学部のチェックは一切入れない（仕様書2.4）。"""
        form = self.open_form()
        return self._post(form, [
            ("value(methodname)", "sylkougi_search"),
            ("buttonName", "searchKougi"),
            ("value(nendo)", str(year)),
            ("value(campuscd)", ""),
            ("value(kouginm)", ""),
            ("value(syokunm)", ""),
            ("value(kaikoCd)", kaiko_cd),
            ("value(kamokuNumber)", ""),
            ("value(siyouGengo)", ""),
        ])

    def page(self, prev_html: str, page_no: int | str, per_page: int = 200) -> str:
        """page_no='' で表示件数の切り替え（=1ページ目）、以降は 2,3,... を渡す。"""
        return self._post(prev_html, [
            ("buttonName", ""),
            ("value(pageCount)", str(page_no)),
            ("value(maxCount)", str(per_page)),
            ("navigateKougiList", "dummy"),
            ("maxDispListCount", str(per_page)),
        ])


def hit_range(page_html: str):
    """(from, to, total) を返す。ヒット0件なら (0,0,0)。"""
    plain = re.sub(r"<script.*?</script>", "", page_html, flags=re.S | re.I)
    plain = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", plain))
    m = _HITS.search(plain.replace(" ", ""))
    if m:
        return int(m.group(1)), int(m.group(2)), int(m.group(3))
    if re.search(r"(?:0件中|(?:該当|検索結果)[^。\n]{0,60}(?:ありません|見つかりません|0件))", plain):
        return 0, 0, 0
    raise RuntimeError("検索結果件数が読み取れない（応答形式の変更またはエラー）")


def is_error_page(page_html: str) -> bool:
    return _ERRPAGE in page_html or len(page_html) < 12000 and "講義一覧" not in page_html


def parse_rows(page_html: str) -> list[dict]:
    """検索結果1ページ分を辞書のリストにする。"""
    out = []
    for m in _ROW.finditer(page_html):
        tds = _TD.findall(m.group(1))
        if len(tds) < 5:
            continue
        code = _text(tds[1])
        if not code:
            continue
        out.append({
            "course_code": code,
            "title": _text(tds[2]),
            "term_slots": _text(tds[3]),
            "instructors": _text(tds[4]),
        })
    return out


def sweep_kaiko(sess: SearchSession, year: int, kaiko_cd: str,
                per_page: int = 200, pause: float = 1.0, log=print):
    """1つの開講時期コードについて全ページを巡回して行を返す。"""
    if per_page < 1 or pause < 0:
        raise ValueError("per_pageは1以上、pauseは0以上が必要")
    first = sess.search(year, kaiko_cd)
    if is_error_page(first):
        raise RuntimeError(f"kaikoCd={kaiko_cd}: 検索がエラー殻を返した")
    _, _, total = hit_range(first)
    if total == 0:
        log(f"  kaikoCd={kaiko_cd}: 0件")
        return [], 0
    cur = sess.page(first, "", per_page)      # 表示件数を per_page に切り替え
    rows, seen = [], set()
    page_no, last_hi = 1, 0
    expected_total = total
    while True:
        if is_error_page(cur):
            raise RuntimeError(f"kaikoCd={kaiko_cd} p{page_no}: エラー殻")
        lo, hi, total = hit_range(cur)
        got = parse_rows(cur)
        if (total != expected_total or lo != last_hi + 1 or hi < lo
                or hi > total or len(got) != hi - lo + 1):
            raise RuntimeError(f"kaikoCd={kaiko_cd} p{page_no}: ページ範囲・取得件数が不整合")
        before = len(seen)
        for r in got:
            if r["course_code"] not in seen:
                seen.add(r["course_code"])
                rows.append(r)
        log(f"  kaikoCd={kaiko_cd} p{page_no}: {lo}-{hi}/{total} rows={len(got)} uniq={len(rows)}")
        if len(seen) - before != len(got):
            raise RuntimeError(f"kaikoCd={kaiko_cd} p{page_no}: 重複または進行しないページ")
        if hi == total:
            break
        last_hi = hi
        page_no += 1
        time.sleep(pause)
        cur = sess.page(cur, page_no, per_page)
        if is_error_page(cur):
            raise RuntimeError(f"kaikoCd={kaiko_cd} p{page_no}: エラー殻")
    return rows, total
