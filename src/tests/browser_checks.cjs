const assert = require('node:assert/strict');
const fs = require('node:fs');
const {pathToFileURL} = require('node:url');
const {chromium} = require('playwright');
(async () => {
  const browser = await chromium.launch({channel: 'msedge', headless: true});
  try {
    const page = await browser.newPage({viewport: {width: 1440, height: 1000}});
    const errors = [], dialogs = [];
    page.on('pageerror', e => errors.push(e.message));
    page.on('dialog', async d => { dialogs.push(d.message()); await d.dismiss(); });
    await page.route(/^https?:/, route => route.abort());
    await page.addInitScript(() => {
      window.resizeRegistrations = 0;
      const original = window.addEventListener;
      window.addEventListener = function(type, ...args) {
        if (type === 'resize') window.resizeRegistrations++;
        return original.call(this, type, ...args);
      };
    });
    await page.goto(pathToFileURL(process.argv[2]).href);
    assert.match(await page.title(), /2027/);
    assert.match(await page.locator('#scope').textContent(), /全科目/);
    assert.equal(await page.locator('.rhead').count(), 2);
    assert.equal(await page.locator('img').count(), 0, 'untrusted data must be text');
    assert.equal(await page.locator('#grid .gh').filter({hasText: /^土$/}).count(), 1);
    await page.getByRole('button', {name: '春学期', exact: true}).click();
    assert.equal(await page.locator('.rhead').count(), 1);
    await page.locator('.rhead').click();
    assert.match(await page.locator('.detail').innerText(), /2年～5年/);
    assert.match(await page.locator('.detail').innerText(), /実施しない: 定期試験・出席/);
    assert.equal(await page.locator('.grade tbody tr').count(), 2);
    // A queued debounced query must not reappear after resetting.
    await page.evaluate(() => {
      const q = document.getElementById('q');
      q.value = '存在しない科目';
      q.dispatchEvent(new Event('input'));
      document.getElementById('reset').click();
    });
    await page.waitForTimeout(220);
    assert.equal(await page.locator('.rhead').count(), 2);
    for (let i=0; i<4; i++) {
      await page.locator('#grid button.cell').first().click();
      await page.locator('#reset').click();
    }
    assert.equal(await page.evaluate(() => window.resizeRegistrations), 1);
    await page.locator('.rhead').last().click();
    const href = await page.locator('a.src').getAttribute('href');
    assert.ok(href.includes('%22%3E'), 'course code in URL must be encoded');
    assert.equal(await page.locator('img').count(), 0);
    assert.deepEqual(errors, []);
    assert.deepEqual(dialogs, []);
    await page.screenshot({path: process.argv[3], fullPage: true});
    await page.setViewportSize({width: 390, height: 844});
    await page.locator('#reset').click();
    assert.equal(await page.locator('.rhead').count(), 2);
    assert.deepEqual(errors, []);
    console.log('Browser checks passed: year/scope, semester, Saturday, detail, safe HTML/URL, reset race, resize listener, desktop/mobile, no JS errors.');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exitCode=1; });
