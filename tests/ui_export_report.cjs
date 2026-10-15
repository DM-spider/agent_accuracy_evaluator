const fs = require('node:fs');
const assert = require('node:assert/strict');
const {chromium} = require('playwright');

(async () => {
  const browser = await chromium.launch({headless: true, channel: 'chrome'});
  try {
    const page = await browser.newPage({viewport: {width: 1440, height: 1000}});
    const errors = [], requests = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('request', request => requests.push(request.url()));
    await page.setContent(fs.readFileSync('runtime/report-preview/report.html', 'utf8'));
    assert.equal(await page.locator('[data-case-row]').count(), 1);
    assert.equal(await page.locator('.diff-table tbody tr').count(), 6);
    assert.match(await page.locator('.kpi').first().textContent(), /合格 0 \/ 可评估 1/);
    assert.match(await page.locator('.diff-table').textContent(), /-31.2%/);
    assert.match(await page.locator('.diff-table').textContent(), /-1,444,098/);
    await page.locator('#status').selectOption('QUALIFIED');
    assert.equal(await page.locator('[data-case-row]:visible').count(), 0);
    assert.equal(await page.locator('#filter-empty').isVisible(), true);
    await page.locator('#reset').click();
    assert.equal(await page.locator('[data-case-row]:visible').count(), 1);
    await page.locator('#search').fill('CX-032');
    assert.equal(await page.locator('[data-case-row]:visible').count(), 1);
    await page.locator('#search').fill('不存在的题目');
    assert.equal(await page.locator('[data-case-row]:visible').count(), 0);
    await page.locator('#reset').click();
    await page.locator('#expand').click();
    assert.equal(await page.locator('details:not([open])').count(), 0);
    assert.equal(await page.locator('.report-answer table tbody tr').count(), 5);
    for (const width of [1440, 768, 390]) {
      await page.setViewportSize({width, height: 1000});
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, `overflow at ${width}`);
    }
    await page.locator('#collapse').click();
    assert.equal(await page.locator('details[open]').count(), 0);
    await page.setViewportSize({width: 1440, height: 1000});
    await page.screenshot({path: 'runtime/report-preview/desktop.png', fullPage: true});
    await page.locator('#status').selectOption('QUALIFIED');
    await page.evaluate(() => window.dispatchEvent(new Event('beforeprint')));
    assert.equal(await page.locator('[data-case-row]:visible').count(), 1);
    assert.equal(await page.locator('details:not([open])').count(), 0);
    await page.emulateMedia({media: 'print'});
    assert.equal(await page.locator('#filter-empty').isVisible(), false);
    await page.pdf({path: 'runtime/report-preview/report.pdf', printBackground: true, preferCSSPageSize: true});
    await page.evaluate(() => window.dispatchEvent(new Event('afterprint')));
    assert.equal(await page.locator('[data-case-row]').evaluate(row => row.hidden), true);
    assert.equal(await page.locator('details[open]').count(), 0);
    assert.deepEqual(errors, []);
    assert.deepEqual(requests, []);
    console.log('PASS: actual export, offline dependencies, evidence tables, filters/reset, desktop/tablet/mobile, full-report printing and state restoration');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
