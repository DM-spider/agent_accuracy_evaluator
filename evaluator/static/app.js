async function api(path, options) {
  const res = await fetch(path, Object.assign({ headers: { "Content-Type": "application/json" } }, options));
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = JSON.stringify((await res.json()).detail); } catch (e) {}
    throw new Error(detail);
  }
  const type = res.headers.get("content-type") || "";
  if (type.includes("application/json")) return res.json();
  return res;
}

function pct(v) {
  if (v === null || v === undefined) return "—";
  return (Number(v) * 100).toFixed(1) + "%";
}

function duration(v) {
  return v === null || v === undefined ? "未记录" : (Number(v) / 1000).toFixed(2) + " 秒";
}

function compactDuration(ms) {
  const value = Number(ms);
  if (!Number.isFinite(value) || value <= 0) return "—";
  return (value / 1000).toFixed(value >= 10000 ? 1 : 2) + "秒";
}

function metricValue(value, empty = "暂无可评估题") {
  return value === null || value === undefined ? empty : pct(value);
}

function localTime(value) {
  if (!value) return "未记录";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString("zh-CN", {timeZone: "Asia/Shanghai", hour12: false});
}

function inlineMarkdown(value) {
  return escapeHtml(value).replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
}

const contextIssues = {
  ORGANIZATION_MISMATCH: "组织口径不一致", ORGANIZATION_UNCONFIRMED: "组织范围待确认",
  AGGREGATION_MISMATCH: "单月与累计口径不一致", AGGREGATION_UNCONFIRMED: "统计粒度待确认",
  PERIOD_FALLBACK_REVIEW: "月份回退规则待复核", PERIOD_MISSING: "回答缺少明确年月",
  PERIOD_AMBIGUOUS: "回答含多个候选期间", PERIOD_RESOLVER_REVIEW: "复杂期间待复核",
  PERIOD_INFERRED: "回答未明确期间，按问题口径查询", PERIOD_CONFLICT: "回答与工具查询期间冲突",
  DATABASE_PERIOD_FALLBACK: "原期间无数据，SQL已回退到最近可用月份",
  DATE_BOUNDARIES_REVIEW: "日期起止边界待确认", FUTURE_PERIOD: "回答期间晚于请求时间",
  SQL_BENCHMARK_INCOMPLETE: "SQL 基准结果不完整", DUPLICATE_COORDINATES: "指标坐标重复"
};

function stamp(status) {
  const labels = {
    RUNNING: "运行中", PENDING: "等待中", COMPLETED: "已完成",
    FAILED: "运行失败", INTERRUPTED: "已中断"
  };
  return `<span class="stamp ${status || ""}">${labels[status] || status || "—"}</span>`;
}

function modeLabel(mode) {
  return {live: "实时", mock_mixed: "模拟 20 题", mock_perfect: "模拟（全对）", mock_errors: "模拟（全错）"}[mode] || mode || "—";
}

function verdictLabel(status) {
  return {QUALIFIED: "合格", PARTIAL: "部分作答", UNQUALIFIED: "不合格", UNEVALUABLE: "无法评估"}[status] || status || "无法评估";
}

function verdictNote(c) {
  if (c.reason_label) return c.reason_label;
  if (c.final_verdict === "UNEVALUABLE" || c.auto_verdict === "UNEVALUABLE") return "无法自动比对";
  if (c.final_verdict === "PARTIAL" || c.auto_verdict === "PARTIAL") return "同一粒度下漏行或漏列";
  if (c.final_verdict === "QUALIFIED" || c.auto_verdict === "QUALIFIED") return "必答格全部对齐";
  return "与 SQL 不一致";
}

function resultCell(c) {
  const verdict = c.final_verdict || "UNEVALUABLE";
  const overridden = Boolean(c.manual_verdict) && c.manual_verdict !== c.auto_verdict;
  const mark = overridden ? '<span class="verdict-stamp" aria-label="已平反">平反</span>' : "";
  return `<span class="result-cell">${mark}<span class="result-tag ${verdict}">${verdictLabel(verdict)}</span></span>`;
}

function runIdFromPath() {
  const m = location.pathname.match(/\/runs\/([^/]+)/);
  return m ? decodeURIComponent(m[1]) : "";
}

function caseIdFromPath() {
  const m = location.pathname.match(/\/cases\/([^/]+)/);
  return m ? decodeURIComponent(m[1]) : "";
}

function mdTables(text) {
  if (!text) return "<em>无回答</em>";
  const lines = text.split(/\n/);
  let html = "", inTable = false, rows = [];
  const flush = () => {
    if (!rows.length) return;
    html += "<table class='md-table'>" + rows.map((r, i) => {
      const tag = i === 0 ? "th" : "td";
      if (i === 1 && r.every(c => /^:?-{2,}:?$/.test(c))) return "";
      return "<tr>" + r.map(c => `<${tag}>${inlineMarkdown(c)}</${tag}>`).join("") + "</tr>";
    }).join("") + "</table>";
    rows = [];
  };
  lines.forEach(line => {
    if (line.trim().startsWith("|")) {
      inTable = true;
      rows.push(line.trim().replace(/^\||\|$/g, "").split("|").map(s => s.trim()));
    } else {
      if (inTable) { flush(); inTable = false; }
      if (line.trim()) {
        const heading = line.match(/^#{1,6}\s+(.+)/);
        html += heading ? `<h3>${inlineMarkdown(heading[1])}</h3>` : `<p>${inlineMarkdown(line)}</p>`;
      }
    }
  });
  if (inTable) flush();
  return html || `<pre>${escapeHtml(text)}</pre>`;
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function renderSqlPanel(sql, contract) {
  const template = sql.sql_template || (contract && contract.sql_template) || "";
  return `<div class="sql-layout"><section><h3>SQL 模板</h3><pre class="sql-code">${escapeHtml(template || "无")}</pre>
    </section>
    <section><h3>绑定参数</h3><pre>${escapeHtml(JSON.stringify(sql.params || {}, null, 2))}</pre></section></div>
    ${sql.error ? `<h3>查询错误</h3><pre>${escapeHtml(sql.error)}</pre>` : ""}`;
}

function infoRows(entries) {
  return `<dl class="info-rows">${entries.map(([key, value]) => `<div><dt>${escapeHtml(key)}</dt><dd>${escapeHtml(value ?? "未记录")}</dd></div>`).join("")}</dl>`;
}

function formatSqlValue(value) {
  if (value === null || value === undefined || value === "") return "";
  const text = String(value).trim();
  if (!/^-?\d+(\.\d+)?([eE][-+]?\d+)?$/.test(text)) return text;
  const number = Number(text);
  if (!Number.isFinite(number)) return text;
  const rounded = Math.round(number);
  if (Math.abs(number - rounded) < 1e-9) {
    if (rounded >= 200001 && rounded <= 209912 && rounded % 100 >= 1 && rounded % 100 <= 12) return String(rounded);
    if (Math.abs(rounded) >= 1) return String(rounded);
    return (0).toFixed(4);
  }
  return number.toFixed(4);
}

function sqlTable(snapshot, resultsOnly = false) {
  if (snapshot && snapshot.error) return resultsOnly ? "<em>无可用结果</em>" : `<p>SQL 查询失败</p><pre>${escapeHtml(snapshot.error)}</pre>`;
  const rows = (snapshot && snapshot.rows) || [];
  const cols = (snapshot && snapshot.columns) || (rows[0] ? Object.keys(rows[0]) : []);
  if (!cols.length) return resultsOnly ? "<em>无可用结果</em>" : "<em>未执行 SQL，尚无查询快照</em>";
  const pageSize = resultsOnly ? rows.length : 50;
  const shown = rows.slice(0, pageSize);
  let table;
  if (shown.length <= 1) {
    const row = shown[0] || {};
    const body = cols.map(c => `<tr><th>${escapeHtml(c)}</th><td>${escapeHtml(formatSqlValue(row[c]))}</td></tr>`).join("");
    table = `<table class="sql-result-table"><thead><tr><th>指标</th><th>数值</th></tr></thead><tbody>${body}</tbody></table>`;
  } else {
    const head = "<tr>" + cols.map(c => `<th>${escapeHtml(c)}</th>`).join("") + "</tr>";
    const body = shown.map(r => "<tr>" + cols.map(c => `<td>${escapeHtml(formatSqlValue(r[c]))}</td>`).join("") + "</tr>").join("");
    table = `<table class="sql-result-table">${head}${body}</table>`;
  }
  if (resultsOnly) return table;
  const more = rows.length > pageSize ? `<p>仅显示前 ${pageSize} 行，共 ${rows.length} 行。</p>` : "";
  return `<div class="kicker">${snapshot.source === "golden_expected" ? "未执行 SQL" : `耗时 ${snapshot.latency_ms || 0} ms`} · ${rows.length} 行${snapshot.truncated ? "（截断）" : ""}</div>${table}${more}`;
}

async function renderIndex() {
  const platform = await api("/api/platform/status");
  document.getElementById("platform-status").textContent = `真实接口：${platform.enabled ? "流式" : "普通 HTTP"} · Cookie：${platform.cookie_configured ? "已配置" : "未配置"} · 会话：${platform.active ? "运行中" : platform.blocked ? "会话占用或状态待确认" : "空闲"}`;
  document.getElementById("confirm-idle").hidden = !platform.blocked || platform.active;
  const health = await api("/api/health");
  document.getElementById("health").innerHTML = `
    <div class="grid metrics">
      <div><div class="label">黄金集</div><strong>${health.golden}</strong></div>
      <div><div class="label">本地存储</div><strong>${health.storage}</strong></div>
      <div><div class="label">数据库 / 智能体</div><strong>${health.database}</strong> / ${health.agent}</div>
    </div>`;
  const data = await api("/api/runs");
  const box = document.getElementById("run-list");
  if (!data.runs.length) {
    box.innerHTML = `<div class="empty">还没有测评批次。点击右上角「新建测评」开始，可用 Mock 样本先跑通页面。</div>`;
    return;
  }
  box.innerHTML = `<table><thead><tr><th>运行编号</th><th>状态</th><th>模式</th><th>通过率</th><th>准确率</th><th>可评估率</th><th></th></tr></thead><tbody>${
    data.runs.map(r => {
      const s = r.summary || {};
      const h = s.headline_metrics || {};
      return `<tr class="clickable" data-id="${r.run_id}"><td>${r.run_id}</td><td>${stamp(r.status)}</td><td>${escapeHtml(modeLabel(r.mode || s.mode))}</td><td class="num">${pct(h.pass_rate ?? s.case_pass_rate)}</td><td class="num">${pct(h.accuracy ?? s.numeric_accuracy)}</td><td class="num">${pct(h.assessability ?? s.assessability_rate)}</td><td><a href="/runs/${r.run_id}">打开</a></td></tr>`;
    }).join("")
  }</tbody></table>`;
  box.querySelectorAll("tr.clickable").forEach(tr => tr.addEventListener("click", () => location.href = "/runs/" + tr.dataset.id));
}

function openDialog() {
  document.getElementById("dialog").classList.add("show");
}
function closeDialog() {
  document.getElementById("dialog").classList.remove("show");
}

function shuffled(items) {
  const copy = items.slice();
  for (let i = copy.length - 1; i > 0; i -= 1) {
    const j = Math.floor(Math.random() * (i + 1));
    [copy[i], copy[j]] = [copy[j], copy[i]];
  }
  return copy;
}

async function questionPool(mode, all = false) {
  if (mode === "live") {
    const catalog = await api("/api/catalog");
    const cases = catalog.cases || [];
    if (all) return cases.map(item => item.case_id);
    return cases.filter(item => item.numeric_evaluable && item.realtime_ready).map(item => item.case_id);
  }
  const pack = await api("/api/mock-demo");
  return pack.case_ids || [];
}

async function resolveScope(mode, scope) {
  if (scope === "custom") {
    const caseIds = document.getElementById("case-ids").value.split(/[,，\s]+/).filter(Boolean);
    if (!caseIds.length) throw new Error("请输入题号");
    return caseIds;
  }
  const pool = await questionPool(mode, scope === "all");
  if (!pool.length) throw new Error("当前模式没有可测评题目");
  if (scope === "all") return pool;
  if (scope === "sequential10") return pool.slice(0, 10);
  if (scope === "random10") return shuffled(pool).slice(0, 10);
  throw new Error("请选择题目范围");
}

function applyCreateRunUi() {
  const live = document.getElementById("mode").value === "live";
  const scope = document.getElementById("scope");
  document.getElementById("scope-fields").hidden = !live;
  document.getElementById("mock-hint").hidden = live;
  document.getElementById("anchor").disabled = live;
  if (live) document.getElementById("anchor").value = "";
  document.getElementById("custom-ids").hidden = !live || scope.value !== "custom";
}

async function startRun() {
  const button = document.getElementById("confirm");
  button.disabled = true;
  document.getElementById("start-error").textContent = "";
  try {
  const mode = document.getElementById("mode").value;
  const scope = mode === "live" ? document.getElementById("scope").value : "all";
  const case_ids = await resolveScope(mode, scope);
  const body = {
    mode,
    numeric_only: scope !== "all",
    case_ids,
    anchor_time: document.getElementById("anchor").value || null,
    agent_name: document.getElementById("agent-name").value || "water-loss-agent",
  };
  const created = await api("/api/runs", { method: "POST", body: JSON.stringify(body) });
  location.href = "/runs/" + created.run_id;
  } catch (err) {
    document.getElementById("start-error").textContent = err.message;
  } finally { button.disabled = false; }
}

function renderRunProgress(progress, status) {
  const box = document.getElementById("run-progress");
  if (!box) return;
  const running = status === "RUNNING" || status === "PENDING";
  const total = Number(progress.total) || 0;
  if (!running || !total) {
    box.hidden = true;
    box.innerHTML = "";
    return;
  }
  const done = Number(progress.done) || 0;
  const ratio = Math.max(0, Math.min(100, Math.round(done / total * 100)));
  box.hidden = false;
  box.innerHTML = `<div class="progress-head"><strong>执行进度 ${done} / ${total}</strong><span>${ratio}%</span></div>
    <div class="progress-bar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${ratio}"><i style="width:${ratio}%"></i></div>`;
}

async function renderRun() {
  const runId = runIdFromPath();
  const poll = async () => {
    const run = await api("/api/runs/" + runId);
    const s = run.summary || {};
    const e = run.evaluation_metrics || {};
    const final = e.final || {};
    const numeric = e.numeric || {};
    const assessability = e.assessability || {};
    const progress = (run.status === "RUNNING" || run.status === "PENDING") ? await api(`/api/runs/${runId}/progress`) : {};
    const runStatus = {RUNNING: "运行中", PENDING: "等待中", COMPLETED: "已完成", FAILED: "运行失败", INTERRUPTED: "已中断"}[run.status] || run.status;
    document.getElementById("run-meta").textContent =
      `${run.run_id} · ${runStatus} · 基准时间 ${localTime(s.anchor_time || run.anchor_time)}${s.watermark_stable === false ? " · 数据水位发生变化" : ""}`;
    renderRunProgress(progress, run.status);
    document.getElementById("metric-cards").innerHTML = [
      ["题目通过率", metricValue(final.pass_rate, "暂无题目"), `${final.generated_questions || 0} / ${e.total_questions || 0} 道题智能体已生成结果`],
      ["准确率", metricValue(numeric.accuracy), `${numeric.qualified_questions || 0} / ${numeric.evaluable_questions || 0} 道可评估题合格（部分作答不计）`],
      ["可评估率", metricValue(assessability.rate), `${assessability.evaluable_questions || 0} / ${e.total_questions || 0} 道题可自动比对或已人工平反`],
    ].map((row, i) => `<div class="card stat primary-metric metric-${i}"><div class="label">${row[0]}</div><div class="value">${row[1]}</div><div class="hint">${row[2]}</div></div>`).join("");
    const statuses = final.statuses || {};
    const statusLabels = [["QUALIFIED", "合格"], ["PARTIAL", "部分作答"], ["UNQUALIFIED", "不合格"], ["UNEVALUABLE", "无法评估"]];
    document.getElementById("status-summary").innerHTML = statusLabels.map(([key, label]) => `<span class="status-count ${key}">${label} <strong>${statuses[key] || 0}</strong></span>`).join("");
    await renderCaseTable(runId);
    if (run.status === "RUNNING" || run.status === "PENDING") setTimeout(poll, 1200);
  };
  document.getElementById("export-json").onclick = () => location.href = `/api/runs/${runId}/export?format=json`;
  document.getElementById("export-xlsx").onclick = () => location.href = `/api/runs/${runId}/export?format=xlsx`;
  document.getElementById("export-html").onclick = () => location.href = `/api/runs/${runId}/export?format=html`;
  ["q", "status", "result-type"].forEach(id => {
    document.getElementById(id).addEventListener("input", () => renderCaseTable(runId));
    document.getElementById(id).addEventListener("change", () => renderCaseTable(runId));
  });
  await poll();
}

async function renderCaseTable(runId) {
  const params = new URLSearchParams();
  const q = document.getElementById("q").value;
  const status = document.getElementById("status").value;
  const resultType = document.getElementById("result-type").value;
  if (q) params.set("q", q);
  if (status) params.set("status", status);
  if (resultType) params.set("result_type", resultType);
  const data = await api(`/api/runs/${runId}/cases?` + params.toString());
  const box = document.getElementById("case-table");
  if (!data.cases.length) {
    box.innerHTML = `<div class="empty">${status === "RUNNING" ? "正在执行…" : "没有符合筛选的题目。"}</div>`;
    return;
  }
  const ranked = data.cases;
  box.innerHTML = `<table><thead><tr><th>题号</th><th>结果</th><th>问题</th><th>耗时</th><th>结论说明</th></tr></thead><tbody>${
    ranked.map(c => {
      const note = verdictNote(c);
      const question = c.question || "";
      const agentMs = (c.agent_timings || {}).completed_ms ?? (c.agent_timings || {}).transport_total_ms ?? c.agent_latency_ms;
      const timing = `智能体 ${compactDuration(agentMs)} · SQL ${compactDuration(c.sql_latency_ms)}`;
      return `<tr class="clickable" data-id="${c.case_id}"><td>${escapeHtml(c.case_id)}</td><td>${resultCell(c)}</td><td title="${escapeHtml(question)}">${escapeHtml(question)}</td><td title="${escapeHtml(timing)}">${escapeHtml(timing)}</td><td title="${escapeHtml(note)}">${escapeHtml(note)}</td></tr>`;
    }).join("")
  }</tbody></table>`;
  box.querySelectorAll("tr.clickable").forEach(tr => tr.addEventListener("click", () => location.href = `/runs/${runId}/cases/${tr.dataset.id}`));
}

function rowClass(status) {
  if (status === "VALUE_MATCH_REVIEW") return "match";
  if (status === "VALUE_DIFF_REVIEW") return "wrong";
  if (["NO_BENCHMARK", "UNEXPECTED"].includes(status)) return "unavailable-row";
  if (["FIELD_UNRECOGNIZED", "PERIOD_REVIEW", "CALIBER_REVIEW", "CALIBER_MISMATCH", "COORDINATE_REVIEW", "VALUE_UNPARSEABLE"].includes(status)) return "review-row";
  if (status === "WRONG_VALUE") return "wrong";
  if (status === "MISSING") return "missing";
  if (status === "UNEXPECTED") return "unexpected";
  if (status === "MATCH" || status === "MATCH_WITH_TOLERANCE") return "match";
  return "";
}

async function renderCase() {
  const runId = runIdFromPath();
  const caseId = caseIdFromPath();
  document.getElementById("back").href = "/runs/" + runId;
  const detail = await api(`/api/runs/${runId}/cases/${caseId}`);
  const result = detail.result || {};
  const timing = result.agent_timings || {};
  const alignment = result.alignment || {};
  document.getElementById("case-kicker").textContent = `${caseId} · ${result.scene_big || ""} · ${result.result_type || ""}`;
  document.getElementById("case-title").textContent = caseId;
  const judgment = detail.judgment || {};
  document.getElementById("case-question").textContent = result.question || (detail.contract || {}).question || "";
  const agent = (detail.agent_answer || {}).text || "";
  const rechecks = detail.sql_rechecks || [];
  const usableRechecks = rechecks.filter(item => !item.error && ((item.columns || []).length || (item.rows || []).length));
  const supplemental = usableRechecks.length > 0;
  const sql = usableRechecks[usableRechecks.length - 1] || detail.sql_snapshot || rechecks[rechecks.length - 1] || {};
  document.getElementById("agent-result").innerHTML = agent ? mdTables(agent) : "<em>无回答</em>";
  document.getElementById("sql-result").innerHTML = sqlTable(sql, true);
  const queryParams = Object.fromEntries(Object.entries(sql.params || alignment.sql_params || alignment.requested_params || {}).filter(([key]) => !key.startsWith("__text_")));
  const sqlNotice = supplemental ? `事后补查参考结果 · ${localTime(sql.finished_at)}（北京时间） · 不修改原评分` : alignment.query_purpose === "requested_context_reference" ? "按原请求期间查询，尚未与回答期间对齐，不参与评分" : "";
  const reference = detail.table_comparison;
  const items = (reference ? reference.items : result.comparison_items || []).slice();
  const renderDiff = () => {
    const labels = {MATCH: "一致", MATCH_WITH_TOLERANCE: "精度范围内一致", WRONG_VALUE: "数值不一致", MISSING: "回答缺失", UNEXPECTED: "SQL 无对应值", UNPARSEABLE: "数值未识别", FIELD_UNRECOGNIZED: "回答字段未识别", NO_BENCHMARK: "SQL 无对应值", PERIOD_REVIEW: "期间待确认", CALIBER_REVIEW: "口径待确认", CALIBER_MISMATCH: "单月/累计口径不一致", COORDINATE_REVIEW: "单位或坐标待确认", VALUE_UNPARSEABLE: "未返回有效数值", VALUE_MATCH_REVIEW: "数值一致（年份待确认）", VALUE_DIFF_REVIEW: "数值有差异（年份待确认）"};
    const counts = Object.entries(items.reduce((out, i) => { const label = labels[i.status] || i.status; out[label] = (out[label] || 0) + 1; return out; }, {}));
    const periodRange = params => {
      const values = Object.entries(params || {}).filter(([key]) => key === "period" || /^period_\d+$/.test(key)).map(([, value]) => String(value)).sort();
      return values.length === 0 ? "未明确" : values.length === 1 ? values[0] : `${values[0]} 至 ${values[values.length - 1]}`;
    };
    const periods = alignment.effective_period || (alignment.reported_periods || []).join(", ") || "未明确";
    const requestedPeriod = alignment.requested_period || periodRange(alignment.requested_params);
    const executedPeriod = alignment.sql_period || periodRange(queryParams);
    const sourceLabels = {agent_answer: "回答明确", agent_tool: "工具参数", question_contract: "问题推定", database_fallback: "数据库回退", unresolved: "未确定"};
    // 仅展示原值；标准化与数值比对在内部完成，结果列只呈现比对结论。
    // 展示值剥离 Markdown 加粗等标记符号（如 **14.48%**），只显示数值本身
    const displayValue = value => String(value == null ? "" : value).replace(/\*\*/g, "").trim();
    const rows = items.map(i => `<tr class="${rowClass(i.status)}"><td>${escapeHtml(Object.values(i.coordinates || {}).join(" · ") || i.agent_period || i.sql_period || "汇总")}</td><td>${escapeHtml(i.metric)} ${escapeHtml(i.unit || "")}</td><td>${escapeHtml(displayValue(i.actual_raw || ""))}</td><td>${escapeHtml(displayValue(i.expected_raw))}</td><td>${escapeHtml(labels[i.status] || i.status)}</td><td>${escapeHtml(i.note || i.normalization_rule || i.mapping_reason || i.evidence || "")}</td></tr>`).join("");
    // 字段映射诊断：表头 → 契约指标/维度；未映射列需人工确认
    const mappingLabels = {MAPPED: "已映射", UNMAPPED: "未映射", NON_TARGET: "非本题基准指标"};
    const kindLabels = {dimension: "维度", measure: "指标"};
    const fieldMapping = (reference || {}).field_mapping || [];
    const mappingRows = fieldMapping.map(row => {
      const state = mappingLabels[row.status] || row.status;
      const target = row.target_metric ? `${kindLabels[row.target_kind] || row.target_kind || ""} · ${row.target_metric}` : "—";
      const method = row.mapping_method ? `${row.mapping_method}（${row.confidence || ""}）` : "—";
      const candidates = (row.candidate_metrics || []).join("、") || "—";
      return `<tr class="${row.status === "UNMAPPED" ? "review-row" : ""}"><td>${escapeHtml(row.source_column)}</td><td>${escapeHtml(state)}</td><td>${escapeHtml(target)}</td><td>${escapeHtml(method)}</td><td>${escapeHtml(candidates)}</td></tr>`;
    }).join("");
    const mappingTable = fieldMapping.length ? `<h3 class="section-title">字段映射</h3>
    <table class="comparison-table mapping-table"><thead><tr><th>智能体字段</th><th>映射状态</th><th>标准指标 / 维度</th><th>映射方式</th><th>候选指标</th></tr></thead><tbody>${mappingRows}</tbody></table>` : "";
    document.getElementById("tab-diff").innerHTML = `${reference ? `<p class="comparison-note">${reference.source === "recheck" ? "使用事后补查结果 · " : ""}逐项状态不改写原始证据</p>` : ""}
    <div class="period-flow"><span>问题期间 <strong>${escapeHtml(String(requestedPeriod))}</strong></span><span>智能体采用 <strong>${escapeHtml(String(periods))}</strong></span><span>SQL执行 <strong>${escapeHtml(String(executedPeriod))}</strong></span><span>依据 <strong>${escapeHtml(sourceLabels[alignment.period_source] || "历史记录")}</strong></span></div>
    <p class="kicker">${escapeHtml([...(reference?.issues || []), ...(alignment.issues || []).map(x => contextIssues[x] || x)].join("；"))}</p>
    <p class="comparison-note">${counts.map(([label, count]) => `${escapeHtml(label)}：${count}`).join("　|　")}</p>
    ${items.length ? `<table class="comparison-table"><thead><tr><th>单位 / 期间</th><th>指标</th><th>智能体值</th><th>SQL值</th><th>结果</th><th>说明</th></tr></thead><tbody>${rows}</tbody></table>` : ""}
    ${items.length ? "" : '<p class="empty">暂无可展示的字段对比</p>'}
    ${mappingTable}`;
  };
  renderDiff();
  const raw = (detail.agent_answer || {}).raw || {};
  const req = raw.request || {};
  const resp = raw.response || {};
  const http = raw.http || {};
  document.getElementById("tab-agent").innerHTML = `${infoRows([["请求", `${http.method || "POST"} ${http.url || "未记录"}`], ["HTTP 状态", http.status || "未记录"], ["回答状态", resp.completion_status || result.completion_status]])}
    <div class="request-response">
      <section><h3>原始请求</h3><pre class="payload-code">${escapeHtml(JSON.stringify(req, null, 2) || "无请求报文")}</pre></section>
      <section><h3>原始返回</h3><pre class="payload-code">${escapeHtml(JSON.stringify(resp, null, 2) || "无返回报文")}</pre></section>
    </div>`;
  document.getElementById("tab-sql").innerHTML = `<h3>查询摘要</h3>
    <div class="query-summary">${[["来源", sql.source === "golden_expected" ? "Mock 金标" : supplemental ? "事后补查" : "原批次查询"], ["状态", sql.error ? "查询失败" : sql.columns ? "查询完成" : "未执行"], ["耗时", sql.columns ? duration(sql.latency_ms) : "未记录"], ["结果", `${(sql.rows || []).length} 行${sql.truncated ? "（已截断）" : ""}`]].map(([key, value]) => `<span>${escapeHtml(key)}：${escapeHtml(value)}</span>`).join("")}</div>
    ${sqlNotice ? `<p>${escapeHtml(sqlNotice)}</p>` : ""}${renderSqlPanel(sql, detail.contract || {})}`;
  const parameterInputs = Object.entries(queryParams).map(([key, value]) => `<label><span>${escapeHtml(key)}</span><input class="sql-param-input" data-param-key="${escapeHtml(key)}" data-param-type="${typeof value}" value="${escapeHtml(value)}" autocomplete="off"></label>`).join("");
  const recheckSql = sql.executed_sql || sql.sql_template || (detail.contract || {}).sql_template || "";
  document.getElementById("tab-sql").insertAdjacentHTML("beforeend", `
    <section class="sql-recheck-section"><h3>只读补查</h3><p class="kicker">可修改绑定参数；SQL 模板保持只读。补查结果不覆盖原快照和评分。</p>
    <div class="sql-param-editor">${parameterInputs || "<span class='kicker'>当前 SQL 无绑定参数</span>"}</div>
    <textarea id="sql-recheck-query" class="sql-query-textbox" rows="9" readonly aria-label="完整 SQL 查询" spellcheck="false">${escapeHtml(recheckSql || "暂无可查询 SQL")}</textarea>
    <button id="sql-recheck-submit" class="btn" ${recheckSql ? "" : "disabled"}>查询 SQL</button><p id="sql-recheck-status" role="status"></p>
    </section><h3>补查记录 · ${rechecks.length}</h3>
    ${rechecks.slice().reverse().map(s => `<details class="recheck-record"><summary>${escapeHtml(localTime(s.finished_at))}（北京时间） · ${s.error ? "失败" : `${s.row_count} 行`} · ${duration(s.latency_ms)}</summary><p>${escapeHtml(s.note)}</p>${infoRows(Object.entries(s.params || {}))}${sqlTable(s)}</details>`).join("")}`);
  document.getElementById("sql-recheck-submit").onclick = async () => {
    const button = document.getElementById("sql-recheck-submit");
    const status = document.getElementById("sql-recheck-status");
    button.disabled = true;
    try {
      const params = {};
      document.querySelectorAll("#tab-sql .sql-param-input").forEach(input => {
        const rawValue = input.value.trim();
        if (input.dataset.paramType === "number") {
          const value = Number(rawValue);
          if (!rawValue || !Number.isFinite(value)) throw new Error(`参数 ${input.dataset.paramKey} 必须是有效数值`);
          params[input.dataset.paramKey] = value;
        } else {
          params[input.dataset.paramKey] = rawValue;
        }
      });
      status.textContent = "数据库查询中";
      const snapshot = await api(`/api/runs/${runId}/cases/${caseId}/sql-recheck`, {method: "POST", body: JSON.stringify({params})});
      status.textContent = snapshot.error ? `查询失败：${snapshot.error}` : `已补查 ${snapshot.row_count} 行，耗时 ${snapshot.latency_ms} ms`;
      if (snapshot.executed_sql) document.getElementById("sql-recheck-query").value = snapshot.executed_sql;
      status.insertAdjacentHTML("afterend", `<div><pre>${escapeHtml(JSON.stringify(snapshot.params))}</pre>${sqlTable(snapshot)}</div>`);
    } catch (error) { status.textContent = error.message; }
    finally { button.disabled = false; }
  };
  const caliber = (reference || {}).caliber_alignment || {rules: [], rows: []};
  const caliberGroups = new Map();
  (caliber.rows || []).forEach(row => {
    const type = row.kind === "dimension" ? "维度" : "指标";
    const canonical = row.canonical_unit || row.unit || "原数值";
    const rule = row.normalization_rule || row.rule || "按原值对齐";
    const key = `${type}|${canonical}|${rule}`;
    if (!caliberGroups.has(key)) caliberGroups.set(key, {type, canonical, rule, objects: []});
    const group = caliberGroups.get(key);
    if (!group.objects.includes(row.object)) group.objects.push(row.object);
  });
  const caliberRows = [...caliberGroups.values()].map(group => `<tr><td>${escapeHtml(group.type)}</td><td>${escapeHtml(group.objects.join("、"))}</td><td><strong>${escapeHtml(group.canonical)}</strong></td><td>${escapeHtml(group.rule)}</td></tr>`).join("");
  document.getElementById("tab-caliber").innerHTML = caliberRows ? `<table class="caliber-table"><thead><tr><th>类型</th><th>对齐对象</th><th>统一格式 / 单位</th><th>口径规则</th></tr></thead><tbody>${caliberRows}</tbody></table>` : '<p class="empty">本题没有需要转换的维度或单位</p>';
  document.getElementById("tab-logs").innerHTML = `<div class="log-columns">
    <section><h3>智能体运行</h3>${infoRows([["轮次", result.turn_index], ["回答状态", result.completion_status], ["首段回答", duration(timing.first_answer_ms)], ["完整响应", duration(timing.completed_ms)], ["传输总耗时", duration(timing.transport_total_ms ?? result.agent_latency_ms)], ["开始（北京时间）", localTime(timing.started_at)], ["结束（北京时间）", localTime(timing.finished_at)], ["重试次数", result.retries || 0]])}
    <details><summary>智能体事件与错误</summary><pre>${escapeHtml(JSON.stringify({events: resp.event_counts, error: resp.error, remote_error: resp.remote_error}, null, 2))}</pre></details></section>
    <section><h3>SQL 查询</h3>${infoRows([["查询状态", sql.error ? "失败" : sql.columns ? "完成" : "未执行"], ["查询耗时", sql.columns && sql.source !== "golden_expected" ? duration(sql.latency_ms) : "未记录"], ["结果行数", (sql.rows || []).length], ["补查次数", rechecks.length]])}
    ${(alignment.sql_attempts || []).length ? `<details><summary>时间回退记录</summary><pre>${escapeHtml(JSON.stringify(alignment.sql_attempts, null, 2))}</pre></details>` : ""}
    </section></div>`;
  document.querySelectorAll(".tabs button").forEach(btn => btn.addEventListener("click", () => {
    document.querySelectorAll(".tabs button").forEach(b => b.classList.remove("active"));
    document.querySelectorAll(".tab-panel").forEach(p => p.classList.remove("active"));
    btn.classList.add("active");
    document.getElementById("tab-" + btn.dataset.tab).classList.add("active");
  }));
  if (detail.prev_case_id) document.getElementById("prev").href = `/runs/${runId}/cases/${detail.prev_case_id}`;
  else document.getElementById("prev").style.opacity = "0.4";
  if (detail.next_case_id) document.getElementById("next").href = `/runs/${runId}/cases/${detail.next_case_id}`;
  else document.getElementById("next").style.opacity = "0.4";
  const paintVerdict = (current) => {
    const selected = current.final_verdict || current.auto_verdict || "UNEVALUABLE";
    const overridden = Boolean(current.manual_verdict) && current.manual_verdict !== current.auto_verdict;
    document.querySelectorAll(".verdict-btn").forEach(btn => {
      const on = btn.dataset.verdict === selected;
      btn.classList.toggle("is-on", on);
      btn.classList.toggle("is-overridden", on && overridden);
    });
  };
  paintVerdict(judgment);
  const saveVerdict = async (verdict) => {
    const chosen = verdict === judgment.auto_verdict ? null : verdict;
    const payload = await api(`/api/runs/${runId}/cases/${caseId}/verdict`, {
      method: "POST",
      body: JSON.stringify({ verdict: chosen }),
    });
    Object.assign(judgment, payload.judgment || {});
    paintVerdict(judgment);
  };
  document.querySelectorAll(".verdict-btn").forEach(btn => {
    btn.onclick = () => saveVerdict(btn.dataset.verdict);
  });
}

document.addEventListener("DOMContentLoaded", () => {
  const page = document.querySelector("main")?.dataset.page;
  if (page === "index") {
    document.getElementById("scope").value = "sequential10";
    document.getElementById("mode").onchange = (event) => {
      if (event.target.value === "live") document.getElementById("scope").value = "sequential10";
      applyCreateRunUi();
    };
    document.getElementById("scope").onchange = applyCreateRunUi;
    applyCreateRunUi();
    document.getElementById("confirm-idle").onclick = async () => {
      if (!confirm("已在业务网页确认该会话没有执行中的任务？解除暂停不会取消远端任务。")) return;
      try { await api("/api/platform/confirm-idle", {method: "POST", body: JSON.stringify({confirmed_idle: true})}); await renderIndex(); }
      catch (err) { document.getElementById("platform-status").textContent = err.message; }
    };
    document.getElementById("new-run").onclick = openDialog;
    document.getElementById("cancel").onclick = closeDialog;
    document.getElementById("confirm").onclick = startRun;
    api("/api/mock-demo").then(pack => {
      const hint = document.getElementById("mock-hint");
      if (hint && pack.case_ids) {
        hint.textContent = `模拟包 ${pack.case_ids.length} 题：${(pack.correct_case_ids || []).length} 题正确，${Object.keys(pack.planted_errors || {}).length} 题植入错值或漏行。`;
      }
    }).catch(() => {});
    renderIndex().catch(err => {
      document.getElementById("run-list").innerHTML = `<div class="empty">无法加载：${escapeHtml(err.message)}</div>`;
    });
  }
  if (page === "run") renderRun().catch(err => {
    document.getElementById("case-table").innerHTML = `<div class="empty">${escapeHtml(err.message)}</div>`;
  });
  if (page === "case") renderCase().catch(err => {
    document.getElementById("tab-diff").innerHTML = `<div class="empty">${escapeHtml(err.message)}</div>`;
  });
});
