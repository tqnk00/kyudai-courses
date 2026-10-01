// 授業さがし のブラウザ別テスト（16項目 × 8種類の画面）。
//
// 準備（1回だけ。npm は OneDrive の外で）:
//   C:/Users/<ユーザー>/dev/kyudai-browser-test を作り、そこで
//   npm i playwright && npx playwright install webkit
//   このファイルをそのフォルダに run.cjs としてコピーする（playwright を見つけるため）
// 実行:
//   1) 公開リポジトリの直下で  py -m http.server 8791   （index.html と details-年度/ を配る）
//   2) node run.cjs [URL] [対象名の一部]     既定のURLは http://localhost:8791/index.html
//
// PCの Chrome / Edge / Safari相当(WebKit)、iPhone・iPad(Safari相当)、Android の Chrome / Edge で、
// 検索・絞り込み・時間割・詳細・お気に入り・共有・取り込みを一通り操作する。
// WebKit は Safari と同じ表示エンジンだが実機そのものではない。最後は実機でも見ること。
const { chromium, webkit, firefox, devices } = require('playwright');

const URL0 = process.argv[2] || 'http://localhost:8791/index.html';
const ONLY = process.argv[3] || '';
const FAV_KEY = 'kyudai-fav-2026';

const TARGETS = [
  { name: 'PC Chrome',        type: chromium, launch: { channel: 'chrome' }, ctx: { viewport: { width: 1366, height: 800 } } },
  { name: 'PC Edge',          type: chromium, launch: { channel: 'msedge' }, ctx: { viewport: { width: 1366, height: 800 } } },
  { name: 'PC Safari(WebKit)',type: webkit,   launch: {}, ctx: { viewport: { width: 1366, height: 800 } } },
  // Firefox はこのPCでは起動がブロックされる（spawn UNKNOWN）ので外してある
  { name: 'iPhone Safari',    type: webkit,   launch: {}, ctx: { ...devices['iPhone 14'] }, mobile: true },
  { name: 'iPhone SE(小)',    type: webkit,   launch: {}, ctx: { ...devices['iPhone SE'] }, mobile: true },
  { name: 'Android Chrome',   type: chromium, launch: { channel: 'chrome' }, ctx: { ...devices['Pixel 7'] }, mobile: true },
  { name: 'Android Edge',     type: chromium, launch: { channel: 'msedge' }, ctx: { ...devices['Galaxy S9+'] }, mobile: true },
  { name: 'iPad Safari',      type: webkit,   launch: {}, ctx: { ...devices['iPad (gen 7)'] }, mobile: true },
];

const results = [];
function rec(t, test, ok, msg) { results.push({ target: t.name, test, ok, msg: msg || '' }); }

async function fresh(browser, t, opts = {}) {
  const ctx = await browser.newContext({ ...t.ctx, locale: 'ja-JP', ...(opts.ctx || {}) });
  const page = await ctx.newPage();
  page.__errors = [];
  page.on('pageerror', e => page.__errors.push('pageerror: ' + e.message));
  page.on('console', m => {
    // 下で止めている計測スクリプトの「読み込み失敗」は数えない
    if (m.type() === 'error' && !/gc\.zgo\.at|goatcounter/.test((m.location() || {}).url || ''))
      page.__errors.push('console: ' + m.text());
  });
  // 訪問数の計測はテストで数えさせない
  await page.route(/goatcounter|gc\.zgo\.at/, r => r.abort());
  if (opts.init) await page.addInitScript(opts.init);
  await page.goto(opts.url || URL0, { waitUntil: 'load' });
  await page.waitForFunction(() => document.querySelectorAll('.rhead').length > 0, null, { timeout: 30000 });
  return { ctx, page };
}
const act = (t, loc) => (t.mobile ? loc.tap() : loc.click());
const num = s => parseInt(String(s).replace(/[^\d]/g, ''), 10);

const TESTS = {
  async '01 読み込み・件数・横はみ出し'(browser, t) {
    const { ctx, page } = await fresh(browser, t);
    const total = num(await page.locator('#total').textContent());
    if (!(total > 5000)) throw new Error('総件数が少ない: ' + total);
    const over = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    if (over > 1) throw new Error('横に ' + over + 'px はみ出している');
    const pressed = await page.locator('#qchips .chip[aria-pressed="true"]').allTextContents();
    if (pressed.length < 3) throw new Error('今の学期が選ばれていない: ' + pressed.join(','));
    if (page.__errors.length) throw new Error(page.__errors.join(' | '));
    await ctx.close();
    return total + '件 / 既定 ' + pressed.join('・');
  },

  async '02 キーワード検索（AND・全角半角）'(browser, t) {
    const { ctx, page } = await fresh(browser, t, { url: URL0 + '#t=all' });
    const all = num(await page.locator('#rcount').textContent());
    await page.locator('#q').fill('統計');
    await page.waitForFunction(a => parseInt(document.getElementById('rcount').textContent.replace(/[^\d]/g, '')) < a, all);
    const one = num(await page.locator('#rcount').textContent());
    await page.locator('#q').fill('統計　力学');     // 全角スペース
    await page.waitForTimeout(500);
    const two = num(await page.locator('#rcount').textContent());
    if (!(two > 0 && two < one)) throw new Error(`AND検索が効いていない: ${one} -> ${two}`);
    const hash = decodeURIComponent(await page.evaluate(() => location.hash));
    if (!hash.includes('q=統計')) throw new Error('URLに検索語が残っていない: ' + hash);
    if (page.__errors.length) throw new Error(page.__errors.join(' | '));
    await ctx.close();
    return `${all} -> 統計 ${one} -> 統計 力学 ${two}`;
  },

  async '03 学部・区分・学年・キャンパス・解除'(browser, t) {
    const { ctx, page } = await fresh(browser, t, { url: URL0 + '#t=all' });
    const all = num(await page.locator('#rcount').textContent());
    await page.locator('#fac').selectOption('工学部');
    await page.waitForTimeout(300);
    const fac = num(await page.locator('#rcount').textContent());
    const cats = await page.locator('#cat option').count();
    if (!(fac > 0 && fac < all)) throw new Error('学部で絞れていない');
    if (cats < 2) throw new Error('科目区分の選択肢が出ていない');
    await page.locator('#cat').selectOption({ index: 1 });
    await page.waitForTimeout(300);
    const cat = num(await page.locator('#rcount').textContent());
    await act(t, page.locator('#gchips .chip', { hasText: /^3$/ }));
    await page.waitForTimeout(300);
    const g3 = num(await page.locator('#rcount').textContent());
    if (!(cat <= fac && g3 <= cat)) throw new Error(`絞り込みで増えた: ${fac} ${cat} ${g3}`);
    await act(t, page.locator('#reset'));
    await page.waitForTimeout(400);
    const facVal = await page.locator('#fac').inputValue();
    if (facVal !== '') throw new Error('解除しても学部が残る');
    if (page.__errors.length) throw new Error(page.__errors.join(' | '));
    await ctx.close();
    return `全${all} -> 工学部${fac} -> 区分${cat} -> 3年${g3}`;
  },

  async '04 時間割のコマ・戻るボタン・集中講義'(browser, t) {
    const { ctx, page } = await fresh(browser, t);
    const cell = page.locator('#grid .cell[data-k]').first();
    const k = await cell.getAttribute('data-k');
    await act(t, cell);
    await page.waitForTimeout(400);
    const title = await page.locator('#rtitle').textContent();
    if (!title.includes(k[0] + '曜')) throw new Error('コマの一覧にならない: ' + title);
    const h1 = decodeURIComponent(await page.evaluate(() => location.hash));
    if (!h1.includes('k=' + k)) throw new Error('URLにコマが残っていない');
    await page.goBack();
    await page.waitForTimeout(500);
    const h2 = decodeURIComponent(await page.evaluate(() => location.hash));
    if (h2.includes('k=' + k)) throw new Error('戻るでコマ選択が解けない');
    await act(t, page.locator('#traybtn'));
    await page.waitForTimeout(400);
    const tray = await page.locator('#rtitle').textContent();
    if (!tray.includes('集中')) throw new Error('集中講義ほかに切り替わらない');
    const txt = await page.locator('#rlist').innerText();
    if (txt.includes('その他その他')) throw new Error('「その他その他」が出ている');
    if (page.__errors.length) throw new Error(page.__errors.join(' | '));
    await ctx.close();
    return 'コマ ' + k + ' / 戻る / 集中講義';
  },

  async '05 詳細を開く（分割ファイルの読み込み）'(browser, t) {
    const { ctx, page } = await fresh(browser, t, { url: URL0 + '#t=all&q=' + encodeURIComponent('鉄鋼製錬学') });
    await act(t, page.locator('.rhead').first());
    await page.waitForFunction(() => {
      const d = document.querySelector('.detail');
      return d && !/読み込み中/.test(d.innerText);
    }, null, { timeout: 15000 });
    const d = await page.locator('.detail').innerText();
    if (/読み込めませんでした/.test(d)) throw new Error('詳細を読み込めない');
    if (/undefined|\bnull\b|NaN/.test(d)) throw new Error('undefined/null/NaN が表示されている');
    for (const w of ['授業の概要', '授業計画', '成績評価', '公式シラバスを開く', '出典'])
      if (!d.includes(w)) throw new Error('詳細に「' + w + '」が無い');
    if (!/合計\s*100%/.test(d)) throw new Error('成績評価の合計が100%でない');
    const more = page.locator('.detail .plan-more');
    if (await more.count()) {
      await act(t, more);
      await page.waitForTimeout(300);
      if (await page.locator('.detail .plan-more').count()) throw new Error('授業計画の「残りを表示」が効かない');
    }
    const href = await page.locator('.detail a.src').last().getAttribute('href');
    if (!/^https:\/\/ku-portal\.kyushu-u\.ac\.jp\//.test(href)) throw new Error('公式リンクが不正: ' + href);
    await act(t, page.locator('.rhead').first());
    await page.waitForTimeout(300);
    if (await page.locator('.detail').count()) throw new Error('もう一度押しても閉じない');
    if (page.__errors.length) throw new Error(page.__errors.join(' | '));
    await ctx.close();
    return '概要・計画・成績・リンク OK';
  },

  async '06 お気に入り（登録・解除・見た目・保存）'(browser, t) {
    const { ctx, page } = await fresh(browser, t);
    const star = page.locator('.fav').first();
    await star.scrollIntoViewIfNeeded();
    const off0 = await star.evaluate(e => getComputedStyle(e).color);
    await act(t, star);
    await page.waitForTimeout(350);
    if (await star.getAttribute('aria-pressed') !== 'true') throw new Error('押しても登録されない');
    const on = await star.evaluate(e => getComputedStyle(e).color);
    if (on === off0) throw new Error('登録しても色が変わらない');
    if (await page.locator('#favcnt').textContent() !== '1') throw new Error('件数が1にならない');
    const saved = await page.evaluate(k => localStorage.getItem(k), FAV_KEY);
    if (!saved || JSON.parse(saved).length !== 1) throw new Error('保存されていない: ' + saved);
    await act(t, star);
    await page.waitForTimeout(350);
    if (await star.getAttribute('aria-pressed') !== 'false') throw new Error('もう一度押しても外れない');
    if (t.mobile) {
      // スマホではタップ後に :hover が残る。外したのに色付きのまま見えないこと
      const off1 = await star.evaluate(e => getComputedStyle(e).color);
      const tf = await star.evaluate(e => getComputedStyle(e).transform);
      if (off1 !== off0) throw new Error(`外した星の色が戻らない: ${off0} -> ${off1}`);
      if (tf !== 'none') throw new Error('外した星が拡大されたまま: ' + tf);
    }
    await act(t, star);
    await page.reload({ waitUntil: 'load' });
    await page.waitForFunction(() => document.querySelectorAll('.rhead').length > 0);
    if (await page.locator('#favcnt').textContent() !== '1') throw new Error('再読み込みでお気に入りが消える');
    await act(t, page.locator('#favbtn'));
    await page.waitForTimeout(400);
    if (await page.locator('.rhead').count() !== 1) throw new Error('お気に入り一覧が1件にならない');
    await act(t, page.locator('.fav').first());
    await page.waitForTimeout(400);
    if (await page.locator('.rhead').count() !== 0) throw new Error('お気に入り一覧で外しても残る');
    if (page.__errors.length) throw new Error(page.__errors.join(' | '));
    await ctx.close();
    return '登録・解除・再読み込み・お気に入り一覧';
  },

  async '07 保存できない環境（プライベート等）でも動く'(browser, t) {
    const { ctx, page } = await fresh(browser, t, {
      init: () => {
        Storage.prototype.setItem = function () { throw new Error('QuotaExceededError'); };
        Storage.prototype.getItem = function () { throw new Error('SecurityError'); };
      },
    });
    const star = page.locator('.fav').first();
    await star.scrollIntoViewIfNeeded();
    await act(t, star);
    await page.waitForTimeout(300);
    if (await star.getAttribute('aria-pressed') !== 'true') throw new Error('保存できないと登録もできない');
    if (page.__errors.length) throw new Error(page.__errors.join(' | '));
    await ctx.close();
    return '保存は不可でも画面は動く';
  },

  async '08 わたしの時間割（追加・外す・前期後期）'(browser, t) {
    const { ctx, page } = await fresh(browser, t);
    await page.locator('#tobtn').scrollIntoViewIfNeeded();
    await act(t, page.locator('#tobtn'));
    await page.waitForFunction(() => document.body.classList.contains('tt'));
    const over = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    if (over > 1) throw new Error('時間割ページが横に ' + over + 'px はみ出す');
    const empty = page.locator('#ttgrid .ttcell:not(.filled)').nth(6);
    await act(t, empty);
    await page.waitForSelector('#sheet:not([hidden]) #addlist button');
    const sheetBox = await page.locator('#sheet').boundingBox();
    const vh = await page.evaluate(() => window.innerHeight);
    if (sheetBox.y < 0 || sheetBox.y + sheetBox.height > vh + 2) throw new Error('シートが画面からはみ出す');
    await page.locator('#addq').fill('学');
    await page.waitForTimeout(300);
    await act(t, page.locator('#addlist button').first());
    await page.waitForTimeout(400);
    if (await page.locator('#ttcount').textContent() !== '1') throw new Error('追加しても1科目にならない');
    await act(t, page.locator('#ttgrid .ttcell.filled').first());
    await page.waitForSelector('#sheet:not([hidden]) [data-drop]');
    const sh = await page.locator('#sheet').innerText();
    if (/undefined|NaN/.test(sh)) throw new Error('シートに undefined/NaN');
    await act(t, page.locator('#sheet [data-drop]').first());
    await page.waitForTimeout(400);
    if (await page.locator('#ttcount').textContent() !== '0') throw new Error('外しても0にならない');
    await act(t, page.locator('.ttsem [data-sem="spring"]'));
    await page.waitForTimeout(300);
    if (await page.locator('.ttsem [data-sem="spring"]').getAttribute('aria-pressed') !== 'true') throw new Error('前期に切り替わらない');
    await act(t, page.locator('#ttback'));
    await page.waitForFunction(() => !document.body.classList.contains('tt'));
    if (page.__errors.length) throw new Error(page.__errors.join(' | '));
    await ctx.close();
    return '追加・詳細シート・外す・前期切替・戻る';
  },

  async '09 秋冬の半分表示・重なり表示'(browser, t) {
    const favs = ['26220041', '26220042', '26073051', '26153601', '26220068'];
    const { ctx, page } = await fresh(browser, t, {
      url: URL0 + '#view=tt',
      init: `localStorage.setItem('${FAV_KEY}', '${JSON.stringify(favs)}')`,
    });
    await page.waitForSelector('#ttgrid .ttcell');
    await act(t, page.locator('.ttsem [data-sem="autumn"]'));
    await page.waitForTimeout(300);
    const split = await page.locator('.ttsplit').first().innerText();
    if (!/秋[\s\S]*物理数学[\s\S]*冬[\s\S]*物理数学/.test(split)) throw new Error('秋冬が半分ずつにならない: ' + split);
    const dup = await page.locator('.ttcell.dup').first().innerText();
    if (!/重複3/.test(dup)) throw new Error('重なりの件数が出ない: ' + dup);
    const lines = await page.locator('.ttcell.dup .dupline').count();
    if (lines !== 3) throw new Error('重なった科目名が3つ並ばない: ' + lines);
    // 科目名が1文字も見えないほど切れていないか
    const w = await page.locator('.ttcell.dup .dupline').first().evaluate(e => e.getBoundingClientRect().height);
    if (w < 10) throw new Error('重なりの科目名が潰れている');
    await act(t, page.locator('.ttcell.dup').first());
    await page.waitForSelector('#sheet:not([hidden]) .dupnote');
    if (page.__errors.length) throw new Error(page.__errors.join(' | '));
    await ctx.close();
    return '半分表示・重複3・注意書き';
  },

  async '10 共有（リンク・QR・コピー）'(browser, t) {
    const favs = ['26220041', '26220042', '26153601'];
    const perms = t.type === chromium ? { permissions: ['clipboard-read', 'clipboard-write'] } : {};
    const { ctx, page } = await fresh(browser, t, {
      url: URL0 + '#view=tt', ctx: perms,
      init: `localStorage.setItem('${FAV_KEY}', '${JSON.stringify(favs)}')`,
    });
    await act(t, page.locator('#ttshare'));
    await page.waitForSelector('#sheet:not([hidden]) #shareurl');
    const url = await page.locator('#shareurl').inputValue();
    if (!/#view=tt&s=26153601\.26220041\.26220042$/.test(url)) throw new Error('共有リンクが不正: ' + url);
    await page.waitForSelector('#shareqr svg', { timeout: 15000 });
    const box = await page.locator('#shareqr svg').boundingBox();
    if (!(box && box.width >= 150 && Math.abs(box.width - box.height) < 2)) throw new Error('QRの大きさが不正: ' + JSON.stringify(box));
    await act(t, page.locator('#sharecopy'));
    await page.waitForTimeout(500);
    const label = await page.locator('#sharecopy').textContent();
    if (!/コピーしました|長押し/.test(label)) throw new Error('コピーの反応が無い: ' + label);
    if (page.__errors.length) throw new Error(page.__errors.join(' | '));
    await ctx.close();
    return 'リンク・QR ' + Math.round(box.width) + 'px・' + label;
  },

  async '11 共有リンクを開いて取り込む'(browser, t) {
    const { ctx, page } = await fresh(browser, t, {
      url: URL0 + '#view=tt&s=26153601.26220041.26220042.99999999',
      init: `if (!localStorage.getItem('${FAV_KEY}')) localStorage.setItem('${FAV_KEY}', '["26531501"]')`,
    });
    await page.waitForSelector('#sheet:not([hidden]) [data-imp]');
    const sh = await page.locator('#sheet').innerText();
    if (!sh.includes('3 科目') || !sh.includes('ほかに 1 科目')) throw new Error('取り込みの案内が不正: ' + sh.slice(0, 120));
    const before = await page.evaluate(k => localStorage.getItem(k), FAV_KEY);
    if (before !== '["26531501"]') throw new Error('選ぶ前に書き換わっている');
    const h = await page.evaluate(() => location.hash);
    if (/[#&]s=/.test(h)) throw new Error('URLに s= が残る');
    await act(t, page.locator('#sheet [data-imp="add"]'));
    await page.waitForTimeout(400);
    const after = JSON.parse(await page.evaluate(k => localStorage.getItem(k), FAV_KEY));
    if (after.length !== 4) throw new Error('追加で4件にならない: ' + after);
    await page.reload({ waitUntil: 'load' });
    await page.waitForTimeout(800);
    if (await page.locator('#sheet:not([hidden])').count()) throw new Error('再読み込みでまた聞かれる');
    if (page.__errors.length) throw new Error(page.__errors.join(' | '));
    await ctx.close();
    return '確認→追加→再読み込みで聞かれない';
  },

  async '12 文字を大きく・色分け・未掲載一覧・出典'(browser, t) {
    const { ctx, page } = await fresh(browser, t);
    await page.locator('#zoom').scrollIntoViewIfNeeded();
    const fs0 = await page.locator('.rhead .rt').first().evaluate(e => parseFloat(getComputedStyle(e).fontSize));
    await act(t, page.locator('#zoom'));
    await page.waitForTimeout(200);
    if (!(await page.locator('.results').getAttribute('class')).includes('big')) throw new Error('文字を大きくが効かない');
    // 詳細を開いていなくても、一覧の科目名が実際に大きくなること
    const fs1 = await page.locator('.rhead .rt').first().evaluate(e => parseFloat(getComputedStyle(e).fontSize));
    if (!(fs1 >= fs0 + 2)) throw new Error(`文字を大きくしても一覧が変わらない: ${fs0}px -> ${fs1}px`);
    await page.locator('#colorbtn').scrollIntoViewIfNeeded();
    if (await page.locator('#colorbtn').isVisible()) {
      await act(t, page.locator('#colorbtn'));
      await page.waitForTimeout(200);
      if (!(await page.locator('.wrap').getAttribute('class')).includes('colorize')) throw new Error('色分けが効かない');
    }
    await page.locator('#missingBox summary').scrollIntoViewIfNeeded();
    await act(t, page.locator('#missingBox summary'));
    await page.waitForTimeout(300);
    const li = await page.locator('#missingList li').count();
    if (li < 5) throw new Error('未掲載一覧が出ない');
    const links = await page.locator('#sources a').count();
    if (links < 10) throw new Error('出典のリンクが足りない');
    if (page.__errors.length) throw new Error(page.__errors.join(' | '));
    await ctx.close();
    return `未掲載 ${li}件 / 出典リンク ${links}`;
  },

  async '16 事前申請だけに絞る'(browser, t) {
    const { ctx, page } = await fresh(browser, t, { url: URL0 + '#t=all' });
    const all = num(await page.locator('#rcount').textContent());
    await page.locator('#apbtn').scrollIntoViewIfNeeded();
    await act(t, page.locator('#apbtn'));
    await page.waitForTimeout(400);
    const n = num(await page.locator('#rcount').textContent());
    if (!(n > 100 && n < all)) throw new Error(`事前申請で絞れていない: ${all} -> ${n}`);
    const tags = await page.locator('#rlist li').evaluateAll(ls => ls.filter(li => !li.querySelector('.tag.ap')).length);
    if (tags) throw new Error('事前申請の印が無い科目が ' + tags + ' 件混ざっている');
    const h = await page.evaluate(() => location.hash);
    if (!/[#&]a=1/.test(h)) throw new Error('URLに残らない');
    await page.reload({ waitUntil: 'load' });
    await page.waitForFunction(() => document.querySelectorAll('.rhead').length > 0);
    if (await page.locator('#apbtn').getAttribute('aria-pressed') !== 'true') throw new Error('再読み込みで外れる');
    await act(t, page.locator('#reset'));
    await page.waitForTimeout(300);
    if (await page.locator('#apbtn').getAttribute('aria-pressed') !== 'false') throw new Error('解除で外れない');
    if (page.__errors.length) throw new Error(page.__errors.join(' | '));
    await ctx.close();
    return `${all} -> 事前申請 ${n}`;
  },

  async '13 ダークモード'(browser, t) {
    const { ctx, page } = await fresh(browser, t, { ctx: { colorScheme: 'dark' } });
    const c = await page.evaluate(() => {
      const rgb = s => s.match(/\d+/g).slice(0, 3).map(Number);
      const lum = a => (a[0] * 299 + a[1] * 587 + a[2] * 114) / 1000;
      const bg = lum(rgb(getComputedStyle(document.body).backgroundColor));
      const fg = lum(rgb(getComputedStyle(document.querySelector('.rhead .rt')).color));
      return { bg, fg };
    });
    if (!(c.bg < 80 && c.fg > 150)) throw new Error('暗い配色になっていない: ' + JSON.stringify(c));
    if (page.__errors.length) throw new Error(page.__errors.join(' | '));
    await ctx.close();
    return `背景${Math.round(c.bg)} 文字${Math.round(c.fg)}`;
  },

  async '14 スマホの使いやすさ（文字入力の拡大・押しやすさ）'(browser, t) {
    if (!t.mobile) return 'PCは対象外';
    const { ctx, page } = await fresh(browser, t, {
      url: URL0 + '#view=tt',
      init: `localStorage.setItem('${FAV_KEY}', '["26220041"]')`,
    });
    const bad = [];
    // iPhone は文字サイズ16px未満の入力欄にフォーカスすると画面を勝手に拡大する
    await act(t, page.locator('#ttshare'));
    await page.waitForSelector('#shareurl');
    const fsShare = await page.locator('#shareurl').evaluate(e => parseFloat(getComputedStyle(e).fontSize));
    await act(t, page.locator('#sheet [data-close]').first());
    await act(t, page.locator('#ttgrid .ttcell:not(.filled)').nth(8));
    await page.waitForSelector('#addq');
    const fsAdd = await page.locator('#addq').evaluate(e => parseFloat(getComputedStyle(e).fontSize));
    await act(t, page.locator('#sheet [data-close]').first());
    await act(t, page.locator('#ttback'));
    await page.waitForFunction(() => !document.body.classList.contains('tt'));
    const fs = await page.evaluate(() => ['q', 'fac', 'cat', 'camp'].map(id => [id, parseFloat(getComputedStyle(document.getElementById(id)).fontSize)]));
    fs.push(['addq', fsAdd], ['shareurl', fsShare]);
    const small = fs.filter(x => x[1] < 16);
    if (small.length) bad.push('入力欄の文字が16px未満（iPhoneで勝手に拡大）: ' + small.map(x => x[0] + '=' + x[1]).join(', '));
    // 押す場所の大きさ（目安 40px）
    const sizes = await page.evaluate(() => {
      const pick = (sel) => { const e = document.querySelector(sel); if (!e) return null; const r = e.getBoundingClientRect(); return [sel, Math.round(r.width), Math.round(r.height)]; };
      return ['.fav', '#qchips .chip', '#gchips .chip', '#favbtn', '#tobtn', '#traybtn', '#reset', '#zoom', '#grid .cell[data-k]'].map(pick).filter(Boolean);
    });
    const tiny = sizes.filter(s => s[2] < 36 || s[1] < 32);
    if (tiny.length) bad.push('押す場所が小さい(高さ36px未満): ' + tiny.map(s => `${s[0]}=${s[1]}x${s[2]}`).join(', '));
    const vp = await page.evaluate(() => (document.querySelector('meta[name=viewport]') || {}).content || '');
    if (!/width=device-width/.test(vp)) bad.push('viewport 指定が無い');
    if (page.__errors.length) bad.push(page.__errors.join(' | '));
    await ctx.close();
    if (bad.length) throw new Error(bad.join(' ／ '));
    return sizes.map(s => `${s[0]} ${s[1]}x${s[2]}`).join(', ');
  },

  async '15 通信できないときの詳細'(browser, t) {
    const { ctx, page } = await fresh(browser, t, { url: URL0 + '#t=all&q=' + encodeURIComponent('鉄鋼製錬学') });
    await page.route(/details-\d+\//, r => r.abort());
    await act(t, page.locator('.rhead').first());
    await page.waitForFunction(() => {
      const d = document.querySelector('.detail');
      return d && !/読み込み中/.test(d.innerText);
    }, null, { timeout: 15000 });
    const d = await page.locator('.detail').innerText();
    if (!d.includes('読み込めませんでした')) throw new Error('失敗の案内が出ない');
    if (d.includes('指定なし')) throw new Error('読めていない項目を既定値で出している');
    if (!d.includes('公式シラバスを開く')) throw new Error('公式リンクが無い');
    await ctx.close();
    return '案内と公式リンクを表示';
  },
};

(async () => {
  for (const t of TARGETS) {
    if (ONLY && !t.name.includes(ONLY)) continue;
    let browser;
    try {
      browser = await t.type.launch({ headless: true, ...t.launch });
    } catch (e) {
      rec(t, '起動', false, e.message.split('\n')[0]);
      continue;
    }
    for (const [name, fn] of Object.entries(TESTS)) {
      try {
        const msg = await Promise.race([
          fn(browser, t),
          new Promise((_, ng) => setTimeout(() => ng(new Error('60秒で終わらない')), 60000)),
        ]);
        rec(t, name, true, msg);
      } catch (e) {
        rec(t, name, false, String(e.message).split('\n')[0].slice(0, 300));
      }
    }
    await browser.close().catch(() => {});
    const mine = results.filter(r => r.target === t.name);
    console.log(`${t.name}: ${mine.filter(r => r.ok).length}/${mine.length} OK`);
  }
  console.log('\n===== 失敗 =====');
  for (const r of results.filter(r => !r.ok)) console.log(`[${r.target}] ${r.test}\n    ${r.msg}`);
  require('fs').writeFileSync(__dirname + '/results.json', JSON.stringify(results, null, 1));
  console.log(`\n合計 ${results.filter(r => r.ok).length}/${results.length} OK`);
})();
