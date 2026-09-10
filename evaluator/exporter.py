# -*- coding: utf-8 -*-
"""导出 JSON / Excel / 离线 HTML 报告。"""
from __future__ import annotations

import json
from html import escape
from pathlib import Path
from typing import Any, Dict

from openpyxl import Workbook

from evaluator.contract_loader import load_contracts
from evaluator.evaluation_metrics import run_evaluation_metrics
from evaluator.repository import Repository


def _case_judgments(repo: Repository, run_id: str) -> Dict[str, Any]:
    return (run_evaluation_metrics(repo, run_id, load_contracts()).get("case_judgments") or {})


def export_json(repo: Repository, run_id: str, dest: Path) -> Path:
    run = repo.get_run(run_id)
    cases = []
    for item in repo.list_cases(run_id):
        detail = repo.load_case_detail(run_id, item["case_id"]) or item
        cases.append(detail)
    payload = {"run": run, "cases": cases}
    dest.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return dest


def export_xlsx(repo: Repository, run_id: str, dest: Path) -> Path:
    run = repo.get_run(run_id) or {}
    summary = run.get("summary") or {}
    wb = Workbook()
    ws = wb.active
    ws.title = "概览"
    ws.append(["指标", "值"])
    for key in (
        "run_id",
        "status",
        "consistency_threshold",
        "case_pass_rate",
        "numeric_accuracy",
        "assessability_rate",
        "numeric_coverage",
        "row_coverage",
        "unexpected_rate",
        "auto_parse_rate",
        "scored_cases",
        "not_scored_cases",
        "pass_cases",
        "fail_cases",
        "review_cases",
    ):
        ws.append([key, summary.get(key, run.get(key))])
    for key, value in (summary.get("timing_summary") or {}).items():
        ws.append([key, value])
    cases_ws = wb.create_sheet("题目")
    judgments = _case_judgments(repo, run_id)
    cases_ws.append(["题号", "状态", "最终结论", "人工平反", "一致性", "场景", "主要失败", "准确率", "覆盖率", "错误值", "缺失", "多余", "轮次", "完成状态", "首段回答ms", "完整响应ms", "SQL耗时ms", "时间筛选", "对齐问题"])
    for item in repo.list_cases(run_id):
        metrics = item.get("metrics") or {}
        judgment = judgments.get(item.get("case_id")) or {}
        cases_ws.append(
            [
                item.get("case_id"),
                item.get("status"),
                judgment.get("final_verdict"),
                judgment.get("manual_verdict"),
                judgment.get("consistency"),
                (repo.load_case_detail(run_id, item["case_id"]) or {}).get("result", {}).get("scene_big"),
                item.get("primary_failure"),
                metrics.get("accuracy") if item.get("status") not in {"REVIEW", "NOT_SCORED"} else None,
                metrics.get("coverage"),
                metrics.get("wrong_count"),
                metrics.get("missing_count"),
                metrics.get("unexpected_count"),
                item.get("turn_index"),
                item.get("completion_status"),
                (item.get("agent_timings") or {}).get("first_answer_ms"),
                (item.get("agent_timings") or {}).get("completed_ms"),
                item.get("sql_latency_ms") or None,
                json.dumps((item.get("alignment") or {}).get("reported_periods", [])),
                ", ".join((item.get("alignment") or {}).get("issues", [])),
            ]
        )
    diff_ws = wb.create_sheet("逐项差异")
    diff_ws.append(["题号", "坐标", "指标", "SQL值", "智能体值", "差值", "状态", "证据"])
    for item in repo.list_cases(run_id):
        detail = repo.load_case_detail(run_id, item["case_id"]) or {}
        result = detail.get("result") or {}
        for cmp in result.get("comparison_items") or []:
            diff_ws.append(
                [
                    item["case_id"],
                    json.dumps(cmp.get("coordinates") or {}, ensure_ascii=False),
                    cmp.get("metric"),
                    cmp.get("expected_value"),
                    cmp.get("actual_value"),
                    cmp.get("delta"),
                    cmp.get("status"),
                    (cmp.get("evidence") or "")[:200],
                ]
            )
    context_ws = wb.create_sheet("期间与证据")
    context_ws.append(["题号", "请求参数", "SQL参数", "期间与口径证据", "Agent原文", "执行SQL", "SQL结果", "平反备注"])
    for item in repo.list_cases(run_id):
        detail = repo.load_case_detail(run_id, item["case_id"]) or {}
        alignment = item.get("alignment") or {}
        sql = detail.get("sql_snapshot") or {}
        context_ws.append([item["case_id"], json.dumps(alignment.get("requested_params", {})),
                           json.dumps(sql.get("params", {})), alignment.get("evidence", ""),
                           (detail.get("agent_answer") or {}).get("text", ""), sql.get("executed_sql", ""),
                           json.dumps(sql.get("rows", []), ensure_ascii=False),
                           (detail.get("review") or {}).get("note", "")])
    recheck_ws = wb.create_sheet("SQL补查")
    recheck_ws.append(["题号", "补查时间", "用途", "参数", "执行SQL", "耗时ms", "错误", "结果行"])
    for item in repo.list_cases(run_id):
        detail = repo.load_case_detail(run_id, item["case_id"]) or {}
        for snapshot in detail.get("sql_rechecks", []):
            for row in snapshot.get("rows") or [None]:
                recheck_ws.append([item["case_id"], snapshot.get("finished_at"), snapshot.get("note"),
                                   json.dumps(snapshot.get("params", {}), ensure_ascii=False), snapshot.get("executed_sql"),
                                   snapshot.get("latency_ms"), snapshot.get("error"), json.dumps(row, ensure_ascii=False)])
    for sheet in wb:
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = sheet.dimensions
        for row in sheet:
            for cell in row:
                if isinstance(cell.value, str):
                    cell.data_type = "s"
    wb.save(dest)
    return dest


def export_html(repo: Repository, run_id: str, dest: Path) -> Path:
    run = repo.get_run(run_id) or {}
    summary = run.get("summary") or {}
    rows, details = [], []
    def pretty(value):
        return escape(json.dumps(value, ensure_ascii=False, indent=2, default=str))

    def rate(value):
        return "未评分" if value is None else f"{value * 100:.1f}%"

    judgments = _case_judgments(repo, run_id)
    for item in repo.list_cases(run_id):
        detail = repo.load_case_detail(run_id, item["case_id"]) or {}
        result = detail.get("result") or {}
        judgment = judgments.get(item["case_id"]) or {}
        cid = escape(item["case_id"])
        alignment = result.get("alignment") or {}
        sql = detail.get("sql_snapshot") or {}
        comparisons = result.get("comparison_items") or []
        diff_rows = "".join("<tr>" + "".join(f"<td>{escape(str(c.get(k, '')))}</td>" for k in
                              ("coordinates", "metric", "expected_value", "actual_value", "unit", "status", "evidence")) + "</tr>" for c in comparisons)
        details.append(f'''<details id="case-{cid}"><summary>{cid} · {escape(result.get("question", ""))} · {escape(judgment.get("final_verdict") or item.get("status", ""))}</summary>
<h3>耗时（毫秒）</h3><pre>{pretty({"agent": result.get("agent_timings"), "sql_ms": result.get("sql_latency_ms")})}</pre>
<h3>期间与口径</h3><pre>{pretty(alignment)}</pre>
<h3>智能体原始回答</h3><pre>{escape((detail.get("agent_answer") or {}).get("text", ""))}</pre>
<h3>SQL 与绑定参数</h3><pre>{escape(sql.get("executed_sql") or sql.get("sql_template") or "未执行")}</pre><pre>{pretty(sql.get("params", {}))}</pre>
<h3>SQL 快照</h3><pre>{pretty(sql.get("rows", []))}</pre>
<h3>事后 SQL 补查（仅供复核，不覆盖原评分）</h3><pre>{pretty(detail.get("sql_rechecks", []))}</pre>
<h3>逐项差异</h3><div class="scroll"><table><thead><tr><th>坐标</th><th>指标</th><th>SQL</th><th>Agent</th><th>单位</th><th>比较状态</th><th>证据</th></tr></thead><tbody>{diff_rows}</tbody></table></div>
<h3>运行错误</h3><pre>{pretty(detail.get("logs", {}))}</pre>
<h3>人工平反</h3><pre>{pretty(detail.get("review", {}))}</pre></details>''')
        rows.append(
            "<tr><td>{case_id}</td><td>{status}</td><td>{primary_failure}</td><td>{accuracy}</td><td>{coverage}</td></tr>".format(
                case_id=f'<a href="#case-{cid}">{cid}</a>',
                status=escape(judgment.get("final_verdict") or item.get("status") or ""),
                primary_failure=escape(item.get("primary_failure") or ""),
                accuracy=rate(judgment.get("consistency") if judgment.get("consistency") is not None else (item.get("metrics") or {}).get("accuracy")),
                coverage=rate((item.get("metrics") or {}).get("coverage")),
            )
        )
    html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>评测报告 {escape(run_id)}</title>
<style>
body {{ font-family: "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif; background:#f4f6f7; color:#20272b; margin:24px; }}
pre {{ white-space:pre-wrap; overflow-wrap:anywhere; font-size:13px; }}
details {{ border-top:1px solid #d9dfe2; padding:16px 0; }}
summary {{ cursor:pointer; font-weight:600; }}
.scroll {{ overflow-x:auto; }}
h1 {{ font-family: inherit; }}
table {{ border-collapse: collapse; width:100%; background:#fff; }}
th, td {{ border:1px solid #e4eaec; padding:8px 10px; vertical-align:middle; }}
th {{ text-align:center; }}
td {{ text-align:left; }}
th {{ background:#0e6b63; color:#fff; }}
.cards span {{ display:inline-block; margin:8px 16px 16px 0; padding:12px 16px; background:#fff; border:1px solid #d7cbb8; }}
</style></head><body>
<h1>智能体数值准确性测评报告</h1>
<p>运行 {escape(run_id)} · 状态 {escape(str(summary.get("status") or run.get("status")))} · 模式 {escape(str(summary.get("mode")))}</p>
<div class="cards">
<span>题目通过率 {rate((summary.get("headline_metrics") or {}).get("pass_rate", summary.get("case_pass_rate")))}</span>
<span>准确率 {rate((summary.get("headline_metrics") or {}).get("accuracy", summary.get("numeric_accuracy")))}</span>
<span>可评估率 {rate((summary.get("headline_metrics") or {}).get("assessability", summary.get("assessability_rate")))}</span>
<span>部分作答 {int((summary.get("headline_metrics") or {}).get("partial_questions") or (summary.get("partial_cases") or 0))}</span>
</div>
<table><thead><tr><th>题号</th><th>状态</th><th>主要失败</th><th>准确率</th><th>覆盖率</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table>
{''.join(details)}
</body></html>"""
    dest.write_text(html, encoding="utf-8")
    return dest
