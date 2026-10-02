# コード調査の結果（Claude, 2026-10-01）

対象は `publish/src/` の Python 22ファイル 約4,400行と、画面 `site_template.html` 約2,100行。
依頼文（`1_依頼文.md`）と同じ形で書いてある。GPT・Gemini の答えと並べて比べるためのもの。
このコードの大半を書いたのは私自身なので、甘くなっている可能性がある。そこは他のAIの結果で補ってほしい。

行番号は各ファイルの行（`2_コード一式.txt` の行番号と同じ）。

## 1. 結論

- **無駄に長いか**: 全体としては妥当。ただし、今のサイトに使っていないコードが約500行あり、そこは削れる。
- **読みやすいか**: 「なぜそうしたか」のコメントが多く、1つずつの処理は追いやすい。一方で、長すぎる関数が5つあり、画面の JavaScript は1つの大きなかたまりで、全体の見通しは良くない。

## 2. 点数

| 観点 | 点数 | 理由 |
|---|---|---|
| 長さの妥当さ | 7 | 5種類の取得元を扱うわりに本体は約3,000行。未使用の約500行と、古い書き方による画面の冗長さが減点 |
| 読みやすさ | 6 | 小さな関数は読みやすいが、`site_data.build`（196行）など長い関数に処理が詰まっている |
| 構成の分かりやすさ | 6 | Python のファイル分けは役割どおり。画面は HTML・CSS・JS が1ファイル2,100行で、探すのに検索が要る |
| コメントの質 | 8 | 理由と実測値が書いてあり役に立つ。経緯の説明（「以前は…」）がやや長い。古い設計書が残っている |

## 3. 指摘の一覧

| # | 種類 | 場所 | 何が問題か | どう直すか | 減る行数の目安 | 確かさ |
|---|---|---|---|---|---|---|
| 1 | 長い関数 | `site_data.py:171-366` `build` | 196行の中で、DBから読む・科目を組み立てる・文学部を混ぜる・B表を混ぜる・教室を混ぜる・メタ情報を作る・詳細を分割する・ファイルに書く、の8つを続けてやっている | 段ごとに関数へ分ける（`load_rows` `to_course` `merge_lit` `merge_core_b` `merge_rooms` `build_meta` `split_details` `write_out`）。`build` は呼ぶ順番だけにする | 0（読みやすさ） | 確認した |
| 2 | 未使用 | `summarize.py` 全体（241行）、`db.py` の要約用テーブル | AI要約の機能。サイトは要約を一度も読んでいない（`site_data.py` `site_template.html` に参照なし） | 使う予定が無ければファイルごと消し、`run.py` の `summarize` ジョブと `requirements-ai.txt` も消す | 約260 | 確認した |
| 3 | 未使用 | `report.py`（180行）、`review-smoke.py`（27行）、`core_btable_check.py`（47行） | CSV・JSON・レポートの出力と、過去の確認用スクリプト。サイトの生成にも `update` にも使われない | `report.py` はCSVが要るなら残す。あとの2つは消すか `tools/` に移す | 約75〜250 | 確認した |
| 4 | 役割の混在 | `site_template.html:746-2090`（JavaScript 約1,340行、関数72個が1つの `(function(){…})()` の中） | 状態・URL・絞り込み・時間割・一覧・詳細・わたしの時間割・共有が同じ階層に並び、区切りはコメントの見出しだけ | 1ファイルのまま、機能ごとに `var Share = (function(){…})()` のような小さなまとまりに分ける。または生成時に複数の `.js` を連結する（ビルド道具は不要、`build_site.py` に数行） | 0（見通し） | 確認した |
| 5 | 長い関数 | `site_template.html:1463` `detailHTML`（104行）、`:1752` `drawTT`（85行）、`:1374` `drawList`（75行）、`:1208` `drawGrid`（73行） | HTMLの文字列を `+` でつなぐ書き方が続き、どこが見た目でどこが判断かを読み分けにくい | テンプレート文字列（バッククォート）と小さな部品関数（`tag()` `row()`）に分ける | 80〜150 | 確認した |
| 6 | 重複 | `site_template.html` に `Array.prototype.forEach.call(…querySelectorAll…)` が10回、`addEventListener` が35回 | 「選んだ要素すべてにクリック処理を付ける」を毎回手で書いている | `on(root, selector, event, fn)` を1つ作る。古いブラウザを気にしないなら `querySelectorAll().forEach` で足りる | 30〜50 | 確認した |
| 7 | 重複 | 学期の区分が3か所: `config.py:118` `TERM_GROUPS`、`timetable_rooms.py:59` `SPRING_GROUPS`、`site_template.html:780` `SPRING_TERMS` | 前期・春・夏／後期・秋・冬の対応を別々に持っている。1つ直すと残りがずれる | `config.py` に `SPRING_GROUPS` `AUTUMN_GROUPS` を置き、Python はそれを使う。画面には `meta` で渡す | 10 | 確認した |
| 8 | 重複 | 「今は前期か後期か」の判定が2か所: `site_template.html:783` と `:1686` | 同じ「4〜8月なら前期」を2回書いている | `currentSeason()` を1つにする | 6 | 確認した |
| 9 | 重複 | 詳細の既定値が2か所: `site_data.py:35` と `site_template.html:1310` | 項目を足すとき両方直す必要があり、片方を忘れると画面で `undefined` になる | `meta.detail_defaults` として生成側から渡し、画面側の定義を消す | 5 | 確認した |
| 10 | 重複 | 取得先のURLが3か所: `timetable_pdfs.py`（PDFと掲載ページ）、`timetable_rooms.py:22-30`（出典リンク）、`site_template.html` の出典欄 | 学部がURLを変えると3か所を直すことになる | `timetable_pdfs.py` に学部ごとの「掲載ページ」を1つ持たせ、残りはそこから引く | 20 | 確認した |
| 11 | その他 | `timetable_rooms.py:154,369,430,445,463`、`timetable_econ.py:51,67`、`lit.py:225,324` | 年度 `2026` がファイル名・URL・引数の既定値に直接書いてある。来年度に使うとき探して直すことになる | ファイル名を年度なしにするか `f"edu-{year}.pdf"` にする。既定値は `config.YEAR` を使う | 0（来年の手間） | 確認した |
| 12 | 長い関数 | `timetable_rooms.py:193-270` `by_room_marker`（77行）、`detail.py:96` `run`（102行）、`report.py:17` `report`（101行） | 1つの関数に、探す・照合する・未掲載として控える、が入っている。入れ子が深い | 「名前を当てる」「未掲載の候補にする」を別関数にする | 0（読みやすさ） | 確認した |
| 13 | 名前 | データの項目名が1〜3文字（`c` `t` `s` `f` `g` `cr` `rq` `tm` `tg` `sl` `iv` `ol` `ap` `kl` `rm` …）。`site_data.py:214-245` と画面全体 | ページを小さくするための短縮だが、対応表がどこにも無く、`c.tg` や `c.kl` が何かをコードを追わないと分からない | 短縮は残し、`site_data.py` の先頭に対応表のコメントを1つ置く（または画面側に同じ表） | +20（増える） | 確認した |
| 14 | コメント | `src/README.md`（215行）、`src/FIXES.md`（98行）、`src/REVIEW.md`（51行）、`publish/DESIGN.md`（330行、9/6 時点）、`publish/REVIEW-3.md`（164行） | 過去のレビューや設計の記録が5本、計約860行ある。今の実装（前期の収録、詳細の分割、共有機能）と食い違う記述が残っている | 今も正しい説明は `publish/README.md` に寄せ、残りは `docs/history/` に移す | 約600 | 推測（全文の突き合わせはしていない） |
| 15 | その他 | `timetable_rooms.py` の数値: 名前は4文字以上（`:138`）、末尾から12文字（`:143`）、前方一致は8文字以上・部分一致は5文字以上（`:187-190`）、題名は4〜26文字（`by_room_marker` 内） | どれも実データに合わせて決めた値だが、名前の無い数字で散らばっている | ファイルの先頭に `MIN_NAME_LEN = 4` のように名前を付けて集め、決めた理由を1行ずつ書く | 0 | 確認した |

## 4. 消してよさそうなもの

| 対象 | 行数 | 消すと何ができなくなるか |
|---|---|---|
| `summarize.py` と `requirements-ai.txt`、`run.py` の `summarize` | 約260 | 科目のAI要約を作れなくなる。今のサイトは要約を使っていない |
| `review-smoke.py` | 27 | 9月のレビュー用の動作確認。今のテスト（59件）で足りる |
| `core_btable_check.py` | 47 | B表の読み取り結果を目で確かめる補助。`tests/test_timetable.py` が同じ点を検査している |
| `report.py` と `run.py` の `report` `full` | 約200 | 実測レポートとCSV・JSONの出力。CSVを使っていなければ不要 |
| `src/FIXES.md` `src/REVIEW.md` `publish/REVIEW-3.md` `publish/DESIGN.md` | 約640 | 過去の経緯の記録。git の履歴には残る |

全部消すと、コードが約530行、文書が約640行減る。

## 5. 最初にやるなら、この3つ

1. **使っていないファイルを消す**（指摘2・3と文書の整理）。考えることが少なく、すぐ500行以上減る。CSVを使っているかどうかだけ持ち主が決める必要がある。
2. **`site_data.build` を8つの関数に分ける**（指摘1）。サイトの中身を決める一番大事な関数で、今後いちばん触る場所。動きは変えずに切り分けるだけで、テストがあるので安全に進められる。
3. **重複している定義を1か所にする**（指摘7・8・9・10）。学期の区分、詳細の既定値、取得先のURL。どれも「片方だけ直して壊す」事故のもとで、実際に今回 `undefined` の表示が1件出ていた。

## 補足: 長さの内訳

| まとまり | 行数 | 備考 |
|---|---|---|
| Campusmate から取って整える（`campusmate` `sweep` `detail` `extract` `grading` `db`） | 1,304 | 本体 |
| Campusmate 以外から取る（`lit` `timetable_*` `core_btable`） | 1,233 | PDFの読み取りが中心。形式が学部ごとに違うので、ここは短くしにくい |
| サイトを作る（`site_data` `build_site`） | 415 | |
| 入口と設定（`run` `config` `power`） | 386 | |
| サイトに使っていないもの（`summarize` `report` `review-smoke` `core_btable_check`） | 495 | 削れる |
| テスト（Python 2本） | 596 | |
| 画面 `site_template.html` | 2,099 | CSS 575 / JavaScript 約1,340 / HTML 180 |

60行を超える関数は Python に4つ（`site_data.build` 196、`detail.run` 102、`report.report` 101、`timetable_rooms.by_room_marker` 77）、JavaScript に5つ（`detailHTML` 104、`drawTT` 85、`drawList` 75、`drawGrid` 73、`render` 60）。
