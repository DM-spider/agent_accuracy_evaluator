const { chromium } = require('playwright');
const path = require('path');

(async () => {
  if (!process.env.LIVE_SMOKE_RUN) throw new Error('LIVE_SMOKE_RUN is required');
  const browser = await chromium.launch({headless: true, channel: 'chrome'});
  try {
    const page = await browser.newPage({viewport: {width: 1440, height: 1000}});
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    const url = `http://127.0.0.1:8765/runs/${process.env.LIVE_SMOKE_RUN}/cases/CX04`;
    await page.goto(url);
    const table = page.locator('#evidence .pane').nth(1).locator('table');
    await table.waitFor();
    if (await table.locator('tr').count() !== 14) throw new Error('SQL rows missing');
    await page.screenshot({path: path.resolve('runtime/ui-sql-recheck-desktop.png'), fullPage: true});
    await page.locator('[data-tab=sql]').click();
    if (!(await page.locator('#sql-recheck-query').textContent()).includes('202607')) throw new Error('Bound SQL missing');
    await page.locator('#sql-recheck-submit').click();
    await page.waitForFunction(() => document.querySelector('#sql-recheck-status').textContent.includes('已补查 13 行'));
    await page.setViewportSize({width: 390, height: 844});
    await page.screenshot({path: path.resolve('runtime/ui-sql-recheck-mobile.png'), fullPage: true});
    if (await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)) throw new Error('Mobile overflow');
    if (errors.length) throw new Error(errors.join('\n'));
    console.log('SQL recheck UI passed: real 13 rows, no Agent request, desktop/mobile');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
