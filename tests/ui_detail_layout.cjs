const { chromium } = require('playwright');
const path = require('path');

const ORIGIN = 'http://127.0.0.1:8765';

async function createMockRun(page) {
  const created = await page.evaluate(async () => {
    const res = await fetch('/api/runs', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({mode: 'mock_mixed'}),
    });
    return res.json();
  });
  const runId = created.run_id;
  for (let i = 0; i < 120; i += 1) {
    const progress = await page.evaluate(async (id) => (await fetch(`/api/runs/${id}/progress`)).json(), runId);
    if (progress.status === 'COMPLETED' || progress.status === 'INTERRUPTED') break;
    await page.waitForTimeout(250);
  }
  return runId;
}

(async () => {
  const browser = await chromium.launch({headless: true, channel: 'chrome'});
  try {
    const page = await browser.newPage({viewport: {width: 1440, height: 1000}});
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(ORIGIN);
    await page.locator('#run-list').waitFor();
    const runId = await createMockRun(page);

    await page.goto(`${ORIGIN}/runs/${runId}/cases/CX04`);
    await page.locator('#tab-diff .llm-overall').waitFor();
    const dims = await page.locator('#tab-diff .llm-dim').count();
    if (dims !== 7) throw new Error('Expected seven fixed dimensions, got ' + dims);
    await page.locator('#tab-diff .llm-differences tbody tr').first().waitFor();
    const meta = await page.locator('#tab-diff .llm-meta').textContent();
    if (!meta || !meta.includes('mock-llm')) throw new Error('Missing evaluation metadata');
    const diffText = await page.locator('#tab-diff').textContent();
    for (const legacy of ['规则引擎', '字段映射', '逐项状态不改写原始证据']) {
      if (diffText.includes(legacy)) throw new Error('Legacy comparison content remains: ' + legacy);
    }
    if (await page.locator('#tab-diff .llm-overall .result-tag').count() !== 1) throw new Error('Expected exactly one overall verdict');
    if (await page.locator('.tabs [data-tab]').count() !== 5) throw new Error('Expected five detail tabs');
    for (const tab of ['agent', 'sql', 'caliber', 'logs']) {
      if (await page.locator(`[data-tab=${tab}]`).count() !== 1) throw new Error('Missing tab ' + tab);
    }
    if (await page.locator('.verdict-btn').count() !== 4) throw new Error('Missing manual override buttons');
    await page.locator('[data-tab=caliber]').click();
    if (await page.locator('#tab-caliber .caliber-table tbody tr').count() < 1) throw new Error('Caliber rows missing');
    await page.locator('[data-tab=diff]').click();
    await page.screenshot({path: path.resolve('runtime/ui-detail-diff.png'), fullPage: true});

    await page.setViewportSize({width: 390, height: 844});
    for (const tab of ['sql', 'logs', 'diff']) {
      await page.locator(`[data-tab=${tab}]`).click();
      if (await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)) throw new Error('Mobile overflow: ' + tab);
    }
    await page.screenshot({path: path.resolve('runtime/ui-detail-mobile.png'), fullPage: true});
    if (errors.length) throw Error(errors.join('\n'));
    console.log('PASS: single LLM comparison, seven dimensions, differences, tabs');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exit(1); });
