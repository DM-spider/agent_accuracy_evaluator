"""面向业务读者的单文件报告；复用最终结论口径，不重新评分。"""
from __future__ import annotations

import json
import math
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path

from evaluator.evaluation_metrics import run_evaluation_metrics
from evaluator.verdict import ISSUE_LABELS

DIMENSIONS = {
    "period": "期间", "scope": "范围", "grain": "粒度", "field_coverage": "字段",
    "row_coverage": "行覆盖", "numeric_accuracy": "数值", "unit_caliber": "单位口径",
}
VERDICTS = {"QUALIFIED": "合格", "PARTIAL": "部分作答", "UNQUALIFIED": "不合格", "UNEVALUABLE": "无法评估"}
STATUSES = {"MATCH": "一致", "PARTIAL": "部分一致", "MISMATCH": "不一致", "UNKNOWN": "待确认", "NA": "不适用"}
RUN_STATUSES = {"COMPLETED": "已完成", "RUNNING": "运行中", "PENDING": "待执行", "FAILED": "执行失败", "INTERRUPTED": "已中断"}


def text(value):
    if value is None:
        return "—"
    if isinstance(value, dict):
        return "；".join(f"{k}：{text(v)}" for k, v in value.items()) or "—"
    if isinstance(value, list):
        return "、".join(text(v) for v in value) or "—"
    return str(value)


def h(value):
    return escape(text(value))


def number(value):
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str) and not re.fullmatch(r"[+-]?(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d+)?(?:[eE][+-]?\d+)?", value.strip()):
        return None
    try:
        result = float(str(value).replace(",", ""))
        return result if math.isfinite(result) else None
    except (ValueError, TypeError):
        return None


def value(value, numeric=True):
    parsed = number(value) if numeric else None
    if parsed is None:
        return h(value if value != "" else None)
    return f"{parsed:,.12f}".rstrip("0").rstrip(".")


def rate(value):
    parsed = number(value)
    return "暂无可评估样本" if parsed is None else f"{parsed * 100:.1f}%"


def local_time(value):
    if not value:
        return "未记录"
    try:
        date = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if date.tzinfo is not None:
            date = date.astimezone(timezone(timedelta(hours=8)))
        return date.strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return str(value)


def tag(status, label=None):
    css = {"QUALIFIED": "pass", "MATCH": "pass", "UNQUALIFIED": "fail", "MISMATCH": "fail", "ERROR": "fail", "PARTIAL": "review"}.get(status, "neutral")
    return f'<span class="tag {css}">{h(label or VERDICTS.get(status) or STATUSES.get(status) or status)}</span>'


def disclosure(title, body):
    return f'<details><summary>{h(title)}</summary><div class="body">{body}</div></details>'


def table(headers, rows, css="", numeric_cols=()):
    if not rows:
        return '<p class="empty">未记录数据</p>'
    head = "".join(f'<th scope="col" class="{"num" if i in numeric_cols else ""}">{h(label)}</th>' for i, label in enumerate(headers))
    body = "".join("<tr>" + "".join(f'<td class="{"num" if i in numeric_cols else ""}">{cell}</td>' for i, cell in enumerate(row)) + "</tr>" for row in rows)
    return f'<div class="scroll" tabindex="0" role="region" aria-label="{h(headers[0])}数据表，可横向滚动"><table class="{css}"><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def inline_markdown(source):
    source = escape(str(source))
    source = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", source)
    return re.sub(r"`([^`]+)`", r"<code>\1</code>", source)


def answer_html(source):
    """只渲染安全的段落、标题和 Markdown 表格，不执行回答中的 HTML。"""
    if not source:
        return '<p class="empty">智能体未返回可用回答</p>'
    output, lines = [], []

    def flush():
        if not lines:
            return
        parsed = [[cell.strip() for cell in line.strip().strip("|").split("|")] for line in lines]
        columns = parsed[0]
        data = [row for row in parsed[1:] if not all(re.fullmatch(r":?-{2,}:?", cell) for cell in row)]
        output.append(table(columns, [[inline_markdown(cell) for cell in row] for row in data]))
        lines.clear()

    for line in str(source).splitlines():
        if line.strip().startswith("|"):
            lines.append(line)
            continue
        flush()
        if not line.strip() or re.fullmatch(r"\s*-{3,}\s*", line):
            continue
        heading = re.match(r"^#{1,6}\s+(.+)", line)
        output.append(f'<h4>{inline_markdown(heading[1])}</h4>' if heading else f'<p>{inline_markdown(line)}</p>')
    flush()
    return '<div class="report-answer">' + "".join(output) + "</div>"


def sql_evidence(snapshot):
    if not snapshot:
        return '<p class="empty">未记录 SQL 基准快照</p>'
    notes = []
    if snapshot.get("error"):
        notes.append(f'<div class="notice">SQL 查询错误：{h(snapshot["error"])}</div>')
    if snapshot.get("truncated"):
        notes.append('<div class="notice">基准结果已截断，以下仅为保存的结果，不代表完整数据。</div>')
    if snapshot.get("watermark_changed"):
        notes.append('<div class="notice">查询期间数据水位发生变化，需要确认基准稳定性。</div>')
    rows = snapshot.get("rows") or []
    columns = snapshot.get("columns") or list(dict.fromkeys(key for row in rows for key in row))
    # 日期、月份与标识保持原样，避免被误写成带千分位的数值。
    numeric = tuple(i for i, col in enumerate(columns) if not re.search(r"月|期|日期|时间|编码|编号|代码|id|code|date|time", col, re.I) and any(number(row.get(col)) is not None for row in rows))
    content = table(columns, [[value(row.get(col), i in numeric) for i, col in enumerate(columns)] for row in rows], numeric_cols=numeric)
    return "".join(notes) + f'<p class="note">原快照保存 {len(rows)} 行；查询耗时 {duration(snapshot.get("latency_ms"))}。</p>' + content


def duration(ms):
    parsed = number(ms)
    return "未记录" if parsed is None else f"{parsed / 1000:.2f} 秒"


def dimensions_html(dimensions):
    cards = []
    for key, label in DIMENSIONS.items():
        item = dimensions.get(key) or {}
        status = item.get("status") or "UNKNOWN"
        cards.append(f'<div class="dim {"failed" if status == "MISMATCH" else ""}"><div class="heading">{label}{tag(status)}</div><p class="preserve">{h(item.get("reason") or "未记录核验说明")}</p></div>')
    return '<div class="dimensions">' + "".join(cards) + "</div>"


def differences_html(differences, measures):
    if not differences:
        return '<p class="empty">未记录差异明细，请结合本题结论与维度说明判断。</p>'
    rows = []
    for diff in differences:
        agent, sql = diff.get("normalized_agent_value"), diff.get("normalized_sql_value")
        delta = ratio = None
        if diff.get("type") == "WRONG_VALUE" and isinstance(agent, (float, int)) and isinstance(sql, (float, int)):
            agent, sql = number(agent), number(sql)
            if agent is not None and sql is not None:
                delta = number(agent - sql)
                ratio = number(delta / abs(sql)) if delta is not None and sql else None
        field = diff.get("field") or "未指定字段"
        measure = measures.get(field) or next((m for m in measures.values() if field == m.get("label") or field in (m.get("aliases") or [])), {})
        unit = f'<div class="unit-note">单位：{h(measure["unit"])}</div>' if measure.get("unit") else ""
        detail = h(diff.get("explanation") or "未记录说明")
        if diff.get("evidence"):
            detail += disclosure("查看核验证据", f'<p class="preserve">{h(diff["evidence"])}</p>')
        code = diff.get("type") or "未分类问题"
        issue = tag(diff.get("severity"), ISSUE_LABELS.get(code, code))
        issue += '<div class="unit-note">' + ("错误" if diff.get("severity") == "ERROR" else "需复核") + "</div>"
        rows.append([issue, h(diff.get("coordinate") or "未指定位置"), h(field) + unit, value(diff.get("agent_value")), value(diff.get("sql_value")), ("+" if delta is not None and delta > 0 else "") + value(delta), "—" if ratio is None else f"{ratio * 100:+.1f}%", detail])
    return table(["问题类型", "数据位置", "指标 / 字段", "回答值", "基准值", "标准化差值", "差异率", "说明 / 证据"], rows, "diff-table", (3, 4, 5, 6))


def case_html(case, index, contract):
    item, detail, judgment = case
    result = detail.get("result") or item
    artifact = detail.get("llm_evaluation") or {}
    data = artifact.get("evaluation") or {}
    differences = data.get("differences") or []
    review = detail.get("review") or {}
    final = judgment["final_verdict"]
    question = result.get("question") or contract.get("question") or item.get("question") or "未记录问题"
    warnings = []
    if judgment.get("manual_verdict") and judgment["manual_verdict"] != judgment["auto_verdict"]:
        warnings.append(f'<div class="notice">人工调整：{h(VERDICTS[judgment["auto_verdict"]])} → {h(VERDICTS[final])}。以下维度和差异保留自动评估记录。复核备注：{h(review.get("note") or "未记录")}。</div>')
    elif review.get("note"):
        warnings.append(f'<p class="note">复核备注：{h(review["note"])}</p>')
    sql = detail.get("sql_snapshot") or {}
    if not sql or sql.get("error") or sql.get("truncated") or sql.get("watermark_changed"):
        warnings.append('<div class="notice">基准证据存在缺失、查询错误、截断或水位变化，请结合下方基准说明复核。</div>')
    if not judgment.get("has_evaluation"):
        warnings.append('<div class="notice">尚无模型评估结果，不将历史比较数据视为当前评估结论。</div>')
    if data.get("needs_human_review"):
        warnings.append('<div class="notice">评估模型提示需要人工复核。</div>')
    confidence = rate(judgment.get("confidence")) if judgment.get("confidence") is not None else "未记录"
    answer = disclosure("查看完整智能体回答", answer_html((detail.get("agent_answer") or {}).get("text")))
    baseline = disclosure("查看原评分 SQL 基准结果", sql_evidence(sql))
    timings = result.get("agent_timings") or item.get("agent_timings") or {}
    technical = f'<dl class="case-facts"><div><dt>智能体完整响应</dt><dd>{duration(timings.get("completed_ms", result.get("agent_latency_ms")))}</dd></div><div><dt>SQL 查询</dt><dd>{duration(sql.get("latency_ms"))}</dd></div><div><dt>模型评估</dt><dd>{duration(artifact.get("latency_ms"))}</dd></div><div><dt>评估模型</dt><dd>{h(artifact.get("model"))}</dd></div><div><dt>提示词版本</dt><dd>{h(artifact.get("prompt_version"))}</dd></div><div><dt>评估时间（北京时间）</dt><dd>{h(local_time(artifact.get("created_at")))}</dd></div></dl><p class="note">各阶段耗时独立记录，不能简单相加作为端到端耗时。</p><h3>基准 SQL 与筛选参数</h3><pre>{h(sql.get("executed_sql") or sql.get("sql_template") or "未记录 SQL")}</pre><p class="preserve">{h(sql.get("params") or "未记录参数")}</p>'
    if artifact.get("error"):
        technical += f'<p class="notice">评估错误：{h(artifact["error"])}</p>'
    if artifact.get("validation_notes"):
        technical += f'<p class="preserve">数值复核说明：{h(artifact["validation_notes"])}</p>'
    rechecks = "".join(disclosure(f'事后 SQL 补查 · {local_time(snapshot.get("finished_at"))}', f'<p class="note">仅供复核，不覆盖原评分基准。用途：{h(snapshot.get("note") or "未记录")}</p>' + sql_evidence(snapshot)) for snapshot in detail.get("sql_rechecks") or [])
    return f'<article class="case-section" id="case-{index}" data-case-detail><div class="case-heading"><h3>{h(item["case_id"])} · {h(result.get("scene_big") or contract.get("scene_big") or "题目详情")}</h3>{tag(final)}</div><p class="question">{h(question)}</p>{"".join(warnings)}<p class="preserve">{h(judgment.get("summary") or "未记录评估摘要")}</p><p class="note">模型判断置信度：{confidence}；置信度不代表回答合格率。</p>{dimensions_html(judgment.get("dimensions") or {})}<h3 style="margin-top:24px">已记录的 {len(differences)} 条差异</h3>{differences_html(differences, contract.get("measures") or {})}<p class="note">差值与差异率仅对完成标准化的数值错误计算。基准为零时不计算差异率，未标准化时显示“—”。明细数量仅统计已保存的差异记录，不按评估摘要推算异常数量。</p>{answer}{baseline}{rechecks}{disclosure("技术附录：取数条件与执行记录", technical)}</article>'


REPORT_SCRIPT = r"""
const evidence = [...document.querySelectorAll('details')];
document.getElementById('expand').onclick = () => evidence.forEach(item => { item.open = true; });
document.getElementById('collapse').onclick = () => evidence.forEach(item => { item.open = false; });
document.getElementById('print').onclick = () => window.print();
const rows = [...document.querySelectorAll('[data-case-row]')];
const search = document.getElementById('search');
const status = document.getElementById('status');
function filter() {
  rows.forEach(row => { row.hidden = (status.value && row.dataset.verdict !== status.value) || !row.textContent.toLowerCase().includes(search.value.trim().toLowerCase()); });
  document.getElementById('visible-count').textContent = `显示 ${rows.filter(row => !row.hidden).length} / ${rows.length} 题`;
  document.getElementById('filter-empty').hidden = rows.some(row => !row.hidden);
}
search.oninput = filter; status.onchange = filter;
document.getElementById('reset').onclick = () => { search.value = ''; status.value = ''; filter(); };
let printState = null;
window.addEventListener('beforeprint', () => { if (printState === null) printState = {open: evidence.map(item => item.open), hidden: rows.map(row => row.hidden)}; evidence.forEach(item => { item.open = true; }); rows.forEach(row => { row.hidden = false; }); });
window.addEventListener('afterprint', () => { if (printState) { evidence.forEach((item, i) => { item.open = printState.open[i]; }); rows.forEach((row, i) => { row.hidden = printState.hidden[i]; }); } printState = null; });
"""


def build_report(repo, run_id, contracts):
    run = repo.get_run(run_id) or {}
    metrics = run_evaluation_metrics(repo, run_id, contracts)
    judgments = metrics["case_judgments"]
    contract_map = {contract.case_id: contract.model_dump(mode="json") for contract in contracts}
    snapshot = Path(repo.runs_dir) / run_id / "contracts_snapshot.json"
    if snapshot.exists():
        contract_map = {item["case_id"]: item for item in json.loads(snapshot.read_text(encoding="utf-8"))}
    cases = [(item, repo.load_case_detail(run_id, item["case_id"]) or {}, judgments[item["case_id"]]) for item in repo.list_cases(run_id)]
    order = {"UNQUALIFIED": 0, "PARTIAL": 1, "UNEVALUABLE": 2, "QUALIFIED": 3}
    cases.sort(key=lambda case: order[case[2]["final_verdict"]])
    total = metrics["total_questions"]
    generated = metrics["final"]["generated_questions"]
    evaluable = metrics["numeric"]["evaluable_questions"]
    qualified = metrics["numeric"]["qualified_questions"]
    counts = metrics["final"]["statuses"]
    difference_count = sum(len(((detail.get("llm_evaluation") or {}).get("evaluation") or {}).get("differences") or []) for _, detail, _ in cases)
    run_status = run.get("status") or (run.get("summary") or {}).get("status") or "UNKNOWN"
    if counts["UNQUALIFIED"] or counts["PARTIAL"]:
        title = "部分回答尚未通过核验，需核对后再使用"
        conclusion = f'已记录 {len(cases)} 题，其中不合格 {counts["UNQUALIFIED"]} 题、部分作答 {counts["PARTIAL"]} 题；保存 {difference_count} 条差异记录。请优先查看问题题目的明细与基准证据，修正后复测。'
    elif counts["UNEVALUABLE"] or not cases:
        title = "评估证据尚不完整，需要补充后复核"
        conclusion = f'已记录 {len(cases)} 题，其中 {counts["UNEVALUABLE"]} 题无法评估。无法评估不等于回答错误，也不等于通过。'
    else:
        title = "已评估回答均通过本次核验"
        conclusion = f'本次 {evaluable} 道可评估题目全部合格，结论仅适用于报告所列样本与核验条件。'
    warnings = ""
    if len(cases) < total or run_status != "COMPLETED":
        warnings += f'<div class="notice">批次状态：{h(RUN_STATUSES.get(run_status, run_status))}。计划 {total} 题，已记录 {len(cases)} 题，尚未记录 {max(total - len(cases), 0)} 题；当前结果为阶段性结果。</div>'
    manual_count = sum(bool(j.get("manual_verdict") and j["manual_verdict"] != j["auto_verdict"]) for _, _, j in cases)
    if manual_count:
        warnings += f'<p class="note">{manual_count} 题存在人工调整，汇总按最终结论统计；自动评估维度与差异仍保留。</p>'
    head = metrics["headline"]
    overview_class = "overview-pass" if evaluable and qualified == evaluable else "overview-review" if not counts["UNQUALIFIED"] and not counts["PARTIAL"] else ""
    cards = [("回答合格率", rate(head["accuracy"]), f"合格 {qualified} / 可评估 {evaluable} 题"), ("回答完成率", rate(head["pass_rate"]), f"已返回回答 {generated} / 计划 {total} 题"), ("可评估率", rate(head["assessability"]), f"可评估 {evaluable} / 已记录 {len(cases)} 题"), ("已记录差异", f"{difference_count} 条", "保存的自动评估明细，非问题题数")]
    kpis = "".join(f'<div class="kpi"><span class="label">{label}</span><strong>{display}</strong><small>{sub}</small></div>' for label, display, sub in cards)
    dim_cards = []
    for key, label in DIMENSIONS.items():
        dim = metrics["dimension_metrics"][key]
        dim_cards.append(f'<div class="dim {"failed" if dim["mismatched"] else ""}"><div class="heading">{label}</div><h3>{rate(dim["rate"])}</h3><p>一致 {dim["matched"]} · 部分 {dim["partial"]} · 不一致 {dim["mismatched"]}</p><p class="mini-count">可核验 {dim["evaluable"]} / 已记录 {len(cases)} 题</p></div>')
    category_rows = [[h(bucket["label"]), str(bucket["total"]), str(bucket["qualified"]), rate(bucket["pass_rate"])] for bucket in metrics.get("category_pass_rate", {}).values()]
    distribution = table(["业务专题", "已记录题数", "最终合格题数", "合格占比（全部已记录题）"], category_rows, numeric_cols=(1, 2, 3)) if len(category_rows) > 1 else ""
    issue_counts = Counter()
    for _, detail, judgment in cases:
        if judgment["final_verdict"] in {"UNQUALIFIED", "PARTIAL"}:
            codes = set(judgment.get("issue_codes") or [])
            codes.update(diff.get("type") for diff in ((detail.get("llm_evaluation") or {}).get("evaluation") or {}).get("differences") or [] if diff.get("type"))
            issue_counts.update(codes)
    issues = table(["问题类型", "受影响题数"], [[h(ISSUE_LABELS.get(code, code)), str(count)] for code, count in issue_counts.most_common()], numeric_cols=(1,)) if issue_counts else '<p class="note">未记录最终不合格或部分作答题目的问题类型。</p>'
    list_rows, sections = [], []
    for index, case in enumerate(cases):
        item, detail, judgment = case
        contract = contract_map.get(item["case_id"], {})
        result = detail.get("result") or item
        question = result.get("question") or contract.get("question") or "未记录问题"
        reason = judgment.get("reason_label") or ("尚无评估" if not judgment.get("has_evaluation") else "未记录主要问题")
        list_rows.append(f'<tr data-case-row data-verdict="{h(judgment["final_verdict"])}"><td><a href="#case-{index}">{h(item["case_id"])}</a></td><td>{h(question)}</td><td>{tag(judgment["final_verdict"])}</td><td>{h(reason)}</td><td class="num">{judgment["differences_count"]}</td></tr>')
        sections.append(case_html(case, index, contract))
    options = "".join(f'<option value="{key}">{label}</option>' for key, label in VERDICTS.items())
    table_list = f'<div class="scroll" tabindex="0" role="region" aria-label="全部题目清单，可横向滚动"><table class="case-list"><thead><tr><th>题号</th><th>业务问题</th><th>最终结论</th><th>自动评估主要问题</th><th>差异条数</th></tr></thead><tbody>{"".join(list_rows)}</tbody></table></div>'
    suggestions = []
    if "WRONG_VALUE" in issue_counts:
        suggestions.append(("核对存在差异的取数与计算过程", "根据差异位置核对实际查询、组织筛选、期间参数和计算公式。现有差异只证明结果不一致，根因需由工具查询和计算证据确定。"))
    if any(code in issue_counts for code in ("PERIOD_MISMATCH", "SCOPE_MISMATCH", "GRAIN_MISMATCH", "UNIT_MISMATCH", "CALIBER_MISMATCH")):
        suggestions.append(("统一查询范围、统计粒度与单位口径", "根据相应维度说明，对齐回答与基准的月份、对象、汇总方式及单位，再重新核验。"))
    if any(code in issue_counts for code in ("MISSING_FIELD", "MISSING_ROW")):
        suggestions.append(("补齐缺失字段或数据行", "以题目要求与基准结果为依据补全内容，确认没有遗漏必需指标和查询对象。"))
    if counts["UNEVALUABLE"]:
        suggestions.append(("补充无法评估题目的证据", "查看缺失回答、SQL 错误或模型评估记录，补全证据后重新评估；不要将无法评估记为数值错误。"))
    if counts["UNQUALIFIED"] or counts["PARTIAL"]:
        suggestions.append(("修正后按原题完整复测", "保留本次基准与结论，使用修正后的回答重新评估全部必需指标与数据行。新结果应单独记录，不能覆盖本报告的历史结论。"))
    actions = "".join(f'<div class="action"><span class="number">{index:02d}</span><div><h3>{h(title)}</h3><p>{h(body)}</p></div></div>' for index, (title, body) in enumerate(suggestions, 1)) or '<p class="note">当前样本没有需要修正的最终结论。继续使用时应保留本次核验范围与基准条件。</p>'
    timestamps = [str((detail.get("llm_evaluation") or {}).get("created_at")) for _, detail, _ in cases if (detail.get("llm_evaluation") or {}).get("created_at")]
    timestamp = max(timestamps, default=run.get("finished_at") or run.get("started_at") or "")
    css = (Path(__file__).parent / "static" / "report.css").read_text(encoding="utf-8")
    status_summary = " · ".join(f"{label} {counts[key]} 题" for key, label in VERDICTS.items())
    return f'''<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>智能体评估报告 · {h(run_id)}</title><style>{css}</style></head><body>
<div class="toolbar"><span>智能体评估报告 · 可离线阅读</span><button type="button" id="expand">展开全部证据</button><button type="button" id="collapse">收起证据</button><button type="button" id="print">打印 / 另存 PDF</button></div><main class="report">
<header class="cover"><div class="eyebrow">漏损问数核验 · 评估结果报告</div><h1>{h(title)}</h1><p>智能体评估报告 · {h(run.get("agent_name") or "本次问数评估")}</p><div class="meta">批次 {h(run_id)} · 状态 {h(RUN_STATUSES.get(run_status, run_status))} · 最近评估记录 {h(local_time(timestamp))}（北京时间）</div><nav aria-label="报告目录"><a href="#overview">结果概览</a><a href="#dimensions">七维诊断</a><a href="#cases">问题与证据</a><a href="#actions">改进建议</a><a href="#method">指标口径</a></nav></header>
<section class="section {overview_class}" id="overview"><div class="section-title"><span class="index">01</span><h2>本次结果概览</h2></div><div class="conclusion"><h3>{h(title)}</h3><p>{h(conclusion)}</p></div>{warnings}<div class="kpis">{kpis}</div><p class="note">{status_summary}。结论仅适用于本次样本，不据此推断产品整体质量。</p>{distribution}</section>
<section class="section" id="dimensions"><div class="section-title"><span class="index">02</span><h2>七维核验结果</h2></div><div class="dimensions">{"".join(dim_cards)}</div><p class="note">各维度一致率 = 一致题数 / 可核验题数；待确认与不适用不进入分母。维度来源于自动评估，不随人工结论调整改变。</p><h3>最终问题题目的类型分布</h3>{issues}<p class="note">同题同类问题计 1 次；一题可有多种问题，受影响题数不能相加作为问题总题数。</p></section>
<section class="section" id="cases"><div class="section-title"><span class="index">03</span><h2>问题清单与核验证据</h2></div><div class="report-filters"><label for="search">查找题目</label><input id="search" type="search" placeholder="题号、问题或关键词"><label for="status">结论</label><select id="status"><option value="">全部结论</option>{options}</select><button id="reset" type="button">重置</button><span id="visible-count" class="note" aria-live="polite">显示 {len(cases)} / {len(cases)} 题</span></div><p class="note">筛选仅影响题目清单；首页统计与下方完整证据保持整批范围。点击题号定位详情。</p>{table_list}<p id="filter-empty" class="empty" hidden>没有符合筛选条件的题目。</p>{"".join(sections) if sections else '<p class="empty">本批次尚无题目结果。</p>'}</section>
<section class="section" id="actions"><div class="section-title"><span class="index">04</span><h2>建议的核对与复测事项</h2></div><p class="note">以下为基于本批次问题类型的建议，尚未执行；不代表已经确定根因。</p><div class="actions">{actions}</div></section>
<section class="section" id="method"><div class="section-title"><span class="index">05</span><h2>如何理解报告指标</h2></div><ul class="formula"><li>回答合格率：最终合格题数 / 可评估题数。不是数值单元格准确率。</li><li>回答完成率：已返回回答题数 / 计划题数。成功返回不代表回答正确。</li><li>可评估率：可评估题数 / 已记录题数；未记录题数单独展示。零分母显示暂无可评估样本。</li><li>最终结论使用持久化模型评估与人工调整；七维诊断和差异使用原自动评估记录。</li><li>标准化差值 = 回答值 − 基准值；差异率 = 差值 / 基准值绝对值。未标准化不计算；零基准不计算差异率。</li><li>模型判断置信度不等于准确率；七维一致率不能取平均替代回答合格率。</li></ul></section><footer class="footer">批次 {h(run_id)} · 所有数据来自导出时保存的评估、回答、原评分基准和人工复核记录。后续 SQL 补查单独展示，不改变历史评分。<br>本文件可离线转发；正文不包含原始请求 JSON 或会话凭据。<p class="print-hint">打印时会展开全部证据并输出完整题目清单，打印结束后恢复阅读状态。</p></footer></main><script>{REPORT_SCRIPT}</script></body></html>'''
