"""授業日の計算。config.CALENDAR（公式の授業日程を写したもの）から、
「この開講期の、この曜日の授業は何月何日にあるか」を出す。

カレンダーへの書き出し（画面の「カレンダーに入れる」）が使う。日付の計算を画面側に
持たせると Python と JavaScript の二重管理になるので、ここで日付の並びまで作って渡す。

  祝日・九大祭・冬季休業・予備日 … その日の授業は無い
  振替授業日（11/5(木) は月曜日の授業、など） … その日は、指定された曜日の授業がある

授業日程は学部ごとに少しずつ違う（工学部はセメスターの授業終了が1週早い、経済学部は
1/13 が休講、など）。全学の日程を土台に、学部ごとの差分（CALENDAR[年度]["faculty"]）を当てる。
"""
import datetime as dt

from config import CALENDAR, PERIOD_TIMES

WEEKDAYS = "月火水木金土日"
# 授業のある曜日。土曜は公式の日程表では休業日だが、土曜開講の科目があるので
# 休業期間と祝日を除いた毎週として出す（回数の裏付けは無い）
CLASS_WEEKDAYS = "月火水木金土"


def _date(s):
    return dt.date.fromisoformat(s)


def _span(start, end):
    d = _date(start)
    while d <= _date(end):
        yield d
        d += dt.timedelta(days=1)


def rules_for(year, faculty=None):
    """その学部の (開講期の期間, 授業の無い日の集合, 振替授業日, 期間外の授業日) を返す。"""
    cal = CALENDAR[year]
    diff = cal.get("faculty", {}).get(faculty, {})
    terms = dict(cal["terms"], **diff.get("terms", {}))
    closed = {d.isoformat() for a, b, _ in cal["no_class"] + diff.get("no_class_add", [])
              for d in _span(a, b)}
    closed -= set(diff.get("no_class_remove", []))
    swap = {k: v for k, v in cal["swap"].items() if k not in diff.get("swap_remove", [])}
    swap.update(diff.get("swap_add", {}))
    return terms, closed, swap, diff.get("extra", {})


def weekday_of_class(d, closed, swap):
    """その日に行う授業の曜日。授業が無ければ None。"""
    iso = d.isoformat()
    if iso in swap:
        # 振替授業日。本来の曜日の授業は無く、指定された曜日の授業がある
        return swap[iso]
    if iso in closed or d.weekday() >= len(CLASS_WEEKDAYS):
        return None
    return WEEKDAYS[d.weekday()]


def class_days(year, faculty=None):
    """{開講期: {曜日: [授業日, ...]}} を返す。授業日は ISO 形式で日付順。"""
    terms, closed, swap, extra = rules_for(year, faculty)
    out = {}
    for term, (start, end) in terms.items():
        days = {w: [] for w in CLASS_WEEKDAYS}
        dates = list(_span(start, end)) + [_date(x) for x in extra.get(term, [])]
        for d in sorted(set(dates)):
            w = weekday_of_class(d, closed, swap)
            if w:
                days[w].append(d.isoformat())
        out[term] = days
    return out


def for_site(year):
    """画面に渡す形。その年度の授業日程を写していなければ None（書き出しのボタンを出さない）。

      days        … 全学の日程での授業日
      by_faculty  … 全学と違う学部だけ、その学部の授業日と違いの説明
      verified    … 学部の授業日程表で確かめた学部（全学と同じ学部 + 差分を書いた学部）
    """
    if year not in CALENDAR:
        return None
    cal = CALENDAR[year]
    diffs = cal.get("faculty", {})
    return {
        "days": class_days(year),
        "by_faculty": {f: {"days": class_days(year, f), "note": d.get("note", ""),
                           "source": d.get("source", "")} for f, d in diffs.items()},
        "verified": list(cal.get("same", [])) + list(diffs),
        "periods": PERIOD_TIMES,
        "source": cal["source"],
    }
