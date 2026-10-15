const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require('playwright');

(async () => {
  const browser = await chromium.launch({headless: true, channel: 'chrome'});
  try {
    const page = await browser.newPage({viewport: {width: 1440, height: 1000}});
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    const css = fs.readFileSync(path.resolve('evaluator/static/styles.css'), 'utf8');
    await page.setContent(`<html lang="zh-CN"><head><style>${css}</style></head><body><div class="page" data-page="case"><div class="card"><div id="comparison"></div></div></div></body></html>`);
    await page.addScriptTag({path: path.resolve('evaluator/static/app.js')});
    await page.evaluate(() => {
      const reason = '核验期间、统计范围与查询条件一致，完整内容应换行展示，不应被省略号截断。'.repeat(3);
      const dimensions = Object.fromEntries(Object.keys(dimensionLabels).map(key => [key, {status: key === 'numeric_accuracy' ? 'MISMATCH' : 'MATCH', reason}]));
      const differences = [
        {type: 'WRONG_VALUE', severity: 'ERROR', coordinate: {月份: '202601'}, field: '供水量', agent_value: '1,730,758', sql_value: '2515137', normalized_agent_value: 1730758, normalized_sql_value: 2515137, explanation: '回答值偏低', evidence: '<script>unsafe</script>'},
        {type: 'WRONG_VALUE', severity: 'ERROR', field: '产销差量', agent_value: 10, sql_value: -10, normalized_agent_value: 10, normalized_sql_value: -10},
        {type: 'WRONG_VALUE', severity: 'ERROR', field: '零基准', agent_value: 1, sql_value: 0, normalized_agent_value: 1, normalized_sql_value: 0},
        {type: 'UNIT_MISMATCH', severity: 'ERROR', field: '单位', agent_value: '1 万吨', sql_value: 10000},
      ];
      window.fixture = {evaluation: {overall_verdict: 'UNQUALIFIED', confidence: .95, summary: reason, dimensions, differences}};
      const panel = document.getElementById('comparison');
      panel.innerHTML = renderLlmDiff(window.fixture, {});
      bindLlmDiff(panel);
    });
    assert.equal(await page.locator('.llm-dim').count(), 7);
    assert.match(await page.locator('.llm-check-count').textContent(), /6 \/ 7/);
    const first = page.locator('tbody tr').first();
    assert.equal(await first.locator('td').nth(4).textContent(), '2,515,137');
    assert.equal(await first.locator('td').nth(5).textContent(), '-784,379');
    assert.equal(await first.locator('td').nth(6).textContent(), '-31.2%');
    assert.equal(await page.locator('tbody tr').nth(1).locator('td').nth(6).textContent(), '+200.0%');
    assert.equal(await page.locator('tbody tr').nth(2).locator('td').nth(6).textContent(), '—');
    assert.equal(await page.locator('tbody tr').nth(3).locator('td').nth(5).textContent(), '—');
    assert.equal(await page.locator('.llm-evidence script').count(), 0);
    await page.locator('.llm-evidence summary').click();
    assert.equal(await page.locator('.llm-evidence p').textContent(), '<script>unsafe</script>');
    await page.locator('[data-diff-dimension="numeric_accuracy"]').click();
    assert.equal(await page.locator('tbody tr:visible').count(), 3);
    await page.locator('[data-diff-dimension="period"]').click();
    assert.equal(await page.locator('tbody tr:visible').count(), 0);
    assert.equal(await page.locator('[data-diff-empty]').isVisible(), true);
    await page.locator('[data-diff-reset]').click();
    assert.equal(await page.locator('tbody tr:visible').count(), 4);
    await page.locator('[data-diff-sort]').selectOption('ratio');
    assert.equal(await page.locator('tbody tr').first().locator('td').nth(2).textContent(), '产销差量');
    await page.locator('[data-diff-sort]').selectOption('original');
    assert.equal(await page.locator('tbody tr').first().locator('td').nth(2).textContent(), '供水量');
    for (const width of [1440, 768, 390]) {
      await page.setViewportSize({width, height: 1000});
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, `overflow at ${width}`);
      assert.equal(await page.locator('.llm-dim-reason').first().evaluate(el => getComputedStyle(el).whiteSpace), 'pre-wrap');
    }
    await page.evaluate(() => {
      const panel = document.getElementById('comparison');
      panel.innerHTML = renderLlmDiff({evaluation: {overall_verdict: 'QUALIFIED', dimensions: {}, differences: []}}, {});
      bindLlmDiff(panel);
    });
    assert.match(await page.locator('[data-diff-empty]').textContent(), /未发现差异/);
    assert.equal(await page.evaluate(() => renderLlmDiff(null, {}).includes('尚无评估结果')), true);
    assert.deepEqual(errors, []);
    console.log('PASS: result summary, full reasons, numeric differences, evidence escaping, filters, sorting, empty states and desktop/tablet/mobile overflow');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
