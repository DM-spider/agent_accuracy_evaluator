const { chromium } = require('playwright');

(async () => {
  const browser = await chromium.launch({headless: true, channel: 'chrome'});
  try {
    const page = await browser.newPage({viewport: {width: 1440, height: 1000}});
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto('http://127.0.0.1:8765/runs/20260907_174006_8a03/cases/CX07');
    await page.locator('.comparison-table tbody tr').first().waitFor();
    const headers = await page.locator('.comparison-table th').allTextContents();
    if (headers.indexOf('智能体值') >= headers.indexOf('SQL值')) throw Error('Wrong column order');
    if (await page.locator('#timing-context, [data-tab=context]').count()) throw Error('Obsolete details remain');
    if (!(await page.locator('[data-tab=exchange]').textContent()).includes('智能体')) throw Error('Missing agent label');
    await page.screenshot({path: 'runtime/ui-detail-diff.png', fullPage: true});
    for (const tab of ['sql', 'logs']) {
      await page.locator(`[data-tab=${tab}]`).click();
      await page.screenshot({path: `runtime/ui-detail-${tab}.png`, fullPage: true});
    }
    const columns = page.locator('.log-columns > section');
    const left = await columns.nth(0).boundingBox();
    const right = await columns.nth(1).boundingBox();
    if (right.x <= left.x || Math.abs(right.y - left.y) > 2) throw Error('Logs not in two columns');
    await page.setViewportSize({width: 390, height: 844});
    for (const tab of ['sql', 'logs', 'diff']) {
      await page.locator(`[data-tab=${tab}]`).click();
      if (await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)) throw Error('Mobile overflow: ' + tab);
    }
    await page.screenshot({path: 'runtime/ui-detail-mobile.png', fullPage: true});
    if (errors.length) throw Error(errors.join('\n'));
    console.log('PASS: table comparison, tab names, SQL layout, dual logs, desktop/mobile');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
