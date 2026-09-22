"""成績評価欄の解析。

シラバスの成績評価欄は「ラベル → 説明文」の対が並ぶ半構造データで、
ラベルは8種類に固定されている（実測 2,089件）。仕様書2.1が
「規則ベースで抽出できるのでAIに投げないこと」と書いているのはここ。

  定期試験 / 期末試験100%
  出席     / 総授業回数の3分の1を超えて欠席した者は、評価の対象とはしません。

割合はマトリクスに、条件や注意書きは補足に振り分ける。
"""
import re

# 出現順がそのまま表示順。実測の件数は
# 出席1375 / レポート1348 / 定期試験1239 / 授業への貢献度981 / 小テスト875 / 発表752 / 作品324 / その他623
METHODS = ["定期試験", "小テスト", "レポート", "発表", "作品",
           "授業への貢献度", "出席", "その他"]

# その方法を使わない、と書いてあるだけの説明文
UNUSED = {"なし", "無し", "特になし", "実施しない", "実施しない。", "行わない", "行わない。",
          "評価しない", "該当しない", "該当せず。", "ありません。", "N/A", "NA", "n/a",
          "None.", "None", "-", "‐", "―", "無", "×"}

_PCT = re.compile(r"(\d{1,3}(?:\.\d+)?)\s*[%％]")
_TRIM = re.compile(r"^[\s：:・（）()\[\]【】、,。．\.\-–—]+|[\s：:・（）()\[\]【】、,。．\-–—]+$")

# 「25%×2回」。1回ぶんの割合と回数が別々に書かれている
_PCT_MUL = re.compile(r"(\d{1,3}(?:\.\d+)?)\s*[%％]\s*[×xX＊*]\s*(\d{1,2})\s*回?")

# 説明が数字だけの欄。'%' を省いて割合だけ書く様式が実在する（レポート/60）
_BARE_PCT = re.compile(r"^\s*(\d{1,3}(?:\.\d+)?)\s*$")

# 割合が配点ではなく受験・合格の条件を指していることを示す語。
# 「総授業回数の80%以上の出席が必要」「at least 60% of the classes」など。
# 語は割合のすぐ隣を見る。「（1500字以上）を課す（50%）」を条件と読み違えないため
_COND_AFTER = re.compile(r"^\s*(以上|以下|未満|を?超え|に満たな|を?下回|欠席)")
_COND_BEFORE = re.compile(
    r"(at\s+least|no\s+less\s+than|more\s+than|less\s+than|minimum\s+of|少なくとも)\s*$", re.I)
# 文全体で見てよい言い回し。配点の宣言には現れない
_COND_CLAUSE = re.compile(
    r"(attendance\s+is\s+(expected|required|mandatory|compulsory)|must\s+attend"
    r"|出席率|出席回数|の出席が必要|以上の出席)", re.I)


def _clean(text):
    return _TRIM.sub("", text or "").strip()


def _is_condition(desc, m):
    """その割合が配点ではなく受験・合格の条件か。割合の前後と、その一文を見る。"""
    if _COND_AFTER.match(desc[m.end():]) or _COND_BEFORE.search(desc[:m.start()]):
        return True
    start = max(desc.rfind("。", 0, m.start()), desc.rfind("\n", 0, m.start())) + 1
    end = desc.find("。", m.end())
    return bool(_COND_CLAUSE.search(desc[start: end if end >= 0 else len(desc)]))


def _note_without_pct(desc):
    """割合を抜いた補足文。抜くと文が壊れるなら原文を返す。

    「（25%×2回）」から数字だけ抜くと「（×2回」が残る。壊すより重複を許す。
    """
    out = _clean(_PCT.sub("", desc))
    if not out:
        return ""
    if out.count("（") != out.count("）") or out.count("(") != out.count(")"):
        return _clean(desc)
    if re.search(r"[（(]\s*[×xX＊*]|^\s*[×xX＊*]", out):
        return _clean(desc)
    return out


def parse(raw):
    """成績評価欄の生テキストを {rows, unused, extra} に分解する。

    rows  … [方法, 割合(数値。記載がなければ None), 補足文] の並び。
            割合はあるが配点ではない（出席条件など）行だけ4つめに 1 が付く
    unused… 「なし」とだけ書かれた方法
    extra … どの方法にも紐づかない行（全体への注意書き）
    """
    lines = [x.strip() for x in (raw or "").splitlines() if x.strip()]
    rows, unused, extra = [], [], []
    i = 0
    while i < len(lines):
        label = lines[i]
        if label not in METHODS:
            extra.append(label)
            i += 1
            continue
        # 次のラベルまでが、この方法の説明。表のセルが複数行に折り返すことがある
        j = i + 1
        while j < len(lines) and lines[j] not in METHODS:
            j += 1
        desc = "\n".join(lines[i + 1:j])
        i = j

        if not desc or desc in UNUSED or _clean(desc) in {_clean(x) for x in UNUSED}:
            unused.append(label)
            continue

        cond = 0
        mul = _PCT_MUL.search(desc)
        pcts = list(_PCT.finditer(desc))
        bare = _BARE_PCT.match(desc)
        if mul:
            # 「25%×2回」は合計50%。回数ぶんを掛けて1行にまとめる
            pct, note = float(mul.group(1)) * int(mul.group(2)), _clean(desc)
        elif len(pcts) == 1 and not _is_condition(desc, pcts[0]):
            pct, note = float(pcts[0].group(1)), _note_without_pct(desc)
        elif not pcts and bare:
            # '%' を省いて割合だけ書く様式。0 もここで数値になり、評価フラグから外れる
            pct, note = float(bare.group(1)), ""
        elif len(pcts) == 1:
            # 出席条件などの割合。配点ではないと分かっているので合計から外す
            pct, note, cond = None, _clean(desc), 1
        else:
            # 割合が2つ以上ある説明は合算の根拠がない。数値は立てず全文を補足に回す
            pct, note = None, _clean(desc)
        # 「発表：15%」のように方法名の言い換えでしかない補足は落とす
        if note == label or note in UNUSED:
            note = ""
        # 4つめは「割合はあるが配点ではない」の印。無い行は3要素のまま
        rows.append([label, pct, note, cond] if cond else [label, pct, note])

    rows.sort(key=lambda r: METHODS.index(r[0]))
    return {"rows": rows, "unused": unused, "extra": extra}


def total_pct(rows):
    """全ての行に割合があるときだけ合計を返す。欠けているなら None。

    受験条件の割合（出席率など）は配点ではないので、合計に入れず欠けとも数えない。
    """
    scored = [r for r in rows if len(r) < 4 or not r[3]]
    if not scored or any(r[1] is None for r in scored):
        return None
    return round(sum(r[1] for r in scored), 1)
