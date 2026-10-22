# -*- coding: utf-8 -*-
"""把 results.json 渲染成一份自包含 HTML 报告。"""
from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any, Dict, List, Sequence

OUT = Path(__file__).resolve().parent / "out"
RESULTS = OUT / "results.json"
REPORT = OUT / "cli_eval_report.html"

STATUS = {
    "PASS": ("通过", "pass"),
    "VALUE_DIFF": ("数值不符", "fail"),
    "CALIBER_DIFF": ("口径不符", "warn"),
    "PARTIAL": ("部分可测", "partial"),
    "BLOCKED": ("接口阻塞", "blocked"),
    "ERROR": ("脚本异常", "fail"),
}

# 总表用的简短摘要：不要写“5 行 × 4 列”这种无信息量的形态
SUMMARY = {
    "CX-033": ("福田 1–6 月累计率", "6 期累计率"),
    "CX-032": ("盐田 1–5 月供/售/差", "5 期供/售/差"),
    "CX-036": ("15 个单位 × 5 个月", "16 个组织 × 5 个月"),
    "CX-037": ("15 个单位同比", "15 个组织同比"),
    "JL-018": ("1007 条维修工单", "12 个组织 × 3 列"),
    "JL-036": ("单月率 15 个单位；检漏数缺", "14 个组织 × 2 列"),
    "BJ-003": ("558 条 / TOP10", "10 个 DMA"),
    "BJ-016": ("11308 / 8663 / 6221", "11558 / 1 / 1"),
    "BJ-017": ("56 组（管径×单位）", "27 行（含三级部门）"),
    "DMA-002": ("5675 条 / 12 个组织", "5675 条 / 12 个组织"),
    "DMA-026": ("率 15 个单位；漏损/小区数缺", "15 个组织 × 3 列"),
    "JL-016": ("E101 阻塞", "13 行"),
    "JL-019": ("E101 阻塞", "10 行"),
    "JL-021": ("E101 阻塞", "6 行"),
    "JL-022": ("E101 阻塞", "6 行"),
    "BJ-012": ("E101 阻塞", "166 行"),
}


def esc(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def fmt(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, (int, float)):
        number = float(value)
        if abs(number) >= 1000:
            return f"{number:,.0f}" if number.is_integer() else f"{number:,.2f}"
        return f"{number:.4f}".rstrip("0").rstrip(".")
    return str(value)


def badge(status: str) -> str:
    label, cls = STATUS.get(status, (status, "blocked"))
    return f'<span class="badge {cls}">{esc(label)}</span>'


RAW_COLUMNS = {"月份(期数)", "DMA编码", "管道编码", "工单编号"}


def table(
    rows: Sequence[Dict[str, Any]],
    columns: Sequence[str],
    *,
    limit: int = 50,
    raw_columns: Sequence[str] = (),
) -> str:
    if not rows:
        return '<p class="empty">无数据</p>'
    raw = set(raw_columns)
    head = "".join(f"<th>{esc(c)}</th>" for c in columns)
    body = []
    for row in rows[:limit]:
        cells = "".join(
            f"<td>{esc(row.get(c) if c in raw else fmt(row.get(c)))}</td>" for c in columns
        )
        body.append(f"<tr>{cells}</tr>")
    more = ""
    if len(rows) > limit:
        more = f'<p class="more">仅展示前 {limit} 行，共 {len(rows)} 行</p>'
    return f'<div class="table-wrap"><table><thead><tr>{head}</tr></thead><tbody>{"".join(body)}</tbody></table></div>{more}'


def render_diffs(
    diffs: Sequence[Dict[str, Any]],
    *,
    limit: int = 12,
    expected_label: str = "标准值",
    actual_label: str = "CLI 值",
) -> str:
    if not diffs:
        return '<p class="ok">无差异</p>'
    head = (
        f"<tr><th>类型</th><th>键</th><th>字段</th><th>{esc(expected_label)}</th>"
        f"<th>{esc(actual_label)}</th><th>差值</th><th>说明</th></tr>"
    )
    rows = []
    for d in diffs[:limit]:
        rows.append(
            "<tr>"
            f"<td>{esc(d.get('type'))}</td>"
            f"<td>{esc(d.get('key', '—'))}</td>"
            f"<td>{esc(d.get('field', '—'))}</td>"
            f"<td class=\"diff-expected\">{esc(fmt(d.get('expected')))}</td>"
            f"<td class=\"diff-actual\">{esc(fmt(d.get('actual')))}</td>"
            f"<td class=\"diff-delta\">{esc(fmt(d.get('delta')))}</td>"
            f"<td>{esc(d.get('message', ''))}</td>"
            "</tr>"
        )
    more = f'<p class="more">仅展示前 {limit} 条，共 {len(diffs)} 条</p>' if len(diffs) > limit else ""
    return f'<div class="table-wrap"><table><thead>{head}</thead><tbody>{"".join(rows)}</tbody></table></div>{more}'


def render_actual(actual: Any) -> str:
    if actual is None:
        return '<p class="empty">接口未返回可比数据</p>'
    if isinstance(actual, dict):
        rows = [{"项目": k, "值": v} for k, v in actual.items()]
        return table(rows, ["项目", "值"])
    if isinstance(actual, list):
        if not actual:
            return '<p class="empty">空结果</p>'
        columns = [c for c in actual[0].keys() if not str(c).startswith("__")]
        return table(actual, columns, limit=20, raw_columns=RAW_COLUMNS)
    return f"<pre>{esc(actual)}</pre>"


def render_expected(case: Dict[str, Any]) -> str:
    stat = case.get("expected_stat") or []
    if stat:
        rows = [
            {"指标": f.get("key"), "标准值": f.get("value"), "单位": f.get("unit", "")}
            for f in stat
        ]
        return table(rows, ["指标", "标准值", "单位"])
    rows = case.get("expected_rows") or []
    if not rows:
        return '<p class="empty">无标准值</p>'
    columns: List[str] = []
    for key in (case.get("key_fields") or []) + (case.get("value_fields") or []):
        if key not in columns:
            columns.append(key)
    for key in rows[0].keys():
        if key not in columns and not str(key).startswith("__"):
            columns.append(key)
    return table(rows, columns, limit=20, raw_columns=RAW_COLUMNS)


def render_evidence(evidence: Sequence[str]) -> str:
    if not evidence:
        return ""
    items = "".join(f"<li>{esc(e)}</li>" for e in evidence)
    return f'<ul class="evidence">{items}</ul>'


def render_commands(plan: Sequence[Dict[str, Any]]) -> str:
    if not plan:
        return '<p class="empty">未记录</p>'
    blocks = []
    for item in plan:
        command = item.get("command") or ""
        params = item.get("params") or {}
        lines = [f"python -m leakage_agent_cli invoke {command} --json"]
        for key, value in params.items():
            lines.append(f"  --param {key}={value}")
        notes = []
        if item.get("rows") is not None:
            notes.append(f"本次返回 {item['rows']} 行")
        if item.get("note"):
            notes.append(str(item["note"]))
        error = item.get("error")
        if isinstance(error, dict):
            notes.append(f"{error.get('respCode')}: {error.get('respMsg')}")
        block = f'<pre class="cmd"><code>{esc(chr(10).join(lines))}</code></pre>'
        if notes:
            block += f'<p class="cmd-note">{esc("；".join(notes))}</p>'
        blocks.append(block)
    blocks.append(
        '<p class="cmd-note">分页由客户端追加：指标 pageNo 从 0 起、pageSize=100；'
        "明细 pageNumber 从 1 起、pageSize=50。</p>"
    )
    return "".join(blocks)


def render_sql(case: Dict[str, Any]) -> str:
    sql = (case.get("sql") or "").strip()
    if not sql:
        return '<p class="empty">无标准 SQL</p>'
    return f'<pre class="sql"><code>{esc(sql)}</code></pre>'


def render_case(case: Dict[str, Any]) -> str:
    case_id = case["case_id"]
    status = case.get("status", "ERROR")
    main_diff = ""
    if case.get("diffs"):
        main_diff = case["diffs"][0].get("message", "")

    if case.get("diffs"):
        diffs_html = render_diffs(case.get("diffs") or [])
    elif status == "BLOCKED":
        diffs_html = '<p class="empty">接口阻塞，未产生可比数值</p>'
    elif status == "PARTIAL":
        diffs_html = '<p class="ok">可对列无差异；不可对列见根因</p>'
    else:
        diffs_html = '<p class="ok">无差异</p>'

    return f"""
<details class="case">
  <summary>
    <span class="case-id">{esc(case_id)}</span>
    {badge(status)}
    <span class="case-q">{esc(case.get('question'))}</span>
    <span class="case-summary">{esc(SUMMARY.get(case_id, ("", ""))[0])}</span>
  </summary>
  <div class="case-body">
    <div class="meta-grid">
      <div><span class="meta-label">场景</span><span>{esc(case.get('category'))}</span></div>
      <div><span class="meta-label">结果类型</span><span>{esc(case.get('result_type'))}</span></div>
      <div><span class="meta-label">标准值来源</span><span>本次标准 SQL 重跑</span></div>
      <div><span class="meta-label">取数方式</span><span>CLI 实调 · 原始响应已落盘</span></div>
    </div>

    <div class="compare-grid">
      <div class="col cli">
        <h4>CLI 实测</h4>
        {render_actual(case.get('actual'))}
      </div>
      <div class="col std">
        <h4>标准值（本次 SQL 重跑）</h4>
        {render_expected(case)}
      </div>
    </div>

    <h4>差异明细（CLI vs 标准值）</h4>
    {diffs_html}

    <h4>根因</h4>
    <p class="root">{esc(case.get('root_cause'))}</p>

    <h4>优化建议</h4>
    <p class="recommend">{esc(case.get('recommendation'))}</p>

    <h4>证据</h4>
    {render_evidence(case.get('evidence') or [])}

    <h4>实际传参</h4>
    {render_commands(case.get('plan') or [])}

    <h4>实际执行的标准 SQL</h4>
    {render_sql(case)}
  </div>
</details>
"""


CONTRACT_FINDINGS = [
    {
        "owner": "网关 / 平台",
        "issue": "commandCode 超过 32 字符，invoke 直接失败",
        "evidence": "water_supply_pipe_maintenance_work（34 字符）、leak_detection_and_verification_work（36 字符）返回 E101「commandCode长度不能超过32」；31/29 字符的命令可正常调用。",
        "impact": "阻塞 JL-016、JL-019、JL-021、JL-022、BJ-012；BJ-017 的三级部门下钻也依赖该明细表。",
        "action": "把两个命令改名为 ≤32 字符（如 wsp_maintenance_work / jl_work_detail），或提供短别名映射；改完后补一次回归。",
    },
    {
        "owner": "网关 / 平台",
        "issue": "startDate / endDate 一传就报 1002",
        "evidence": "month_diff_water + startDate=202601&endDate=202605 → respCode 1002；不传日期则 SUCCESS 并返回全部 21 期。",
        "impact": "所有指标命令无法按期间取数，只能拉全量后客户端过滤；分页量从 1 页涨到 4 页，工单/预警类更是上百页。",
        "action": "让日期参数真正生效；如果短期做不到，请从 authority 的 params 里移除，避免调用方按文档传参直接失败。",
    },
    {
        "owner": "网关 / 平台",
        "issue": "指标与明细的响应外层不一致",
        "evidence": "指标：data.data.list + total/pageCount，pageNo 从 0 起；明细：data.data.result + totalRows/totalPage，pageNumber 从 1 起。",
        "impact": "同一套适配器必须按命令类型走两条解析/分页路径，容易漏翻页或取错数组。",
        "action": "统一为 data.list/data.result 二选一并写进正式 schema；至少把分页起止页在文档里按命令类型标清楚。",
    },
    {
        "owner": "网关 / 平台",
        "issue": "业务失败仍返回 HTTP 200，CLI 退出码为 0",
        "evidence": "respCode=1002 / E101 时，CLI 仍正常打印 JSON 且 exitCode=0；invoke 不校验 respCode，只有 authority 校验。",
        "impact": "批处理会把失败当成功，报告出现假通过。",
        "action": "CLI 在 invoke 里校验 respCode，非 SUCCESS 返回非 0；或至少在 stdout 里固定加 success 字段。",
    },
    {
        "owner": "指标口径",
        "issue": "月度量用累计差，标准值用单月快照",
        "evidence": "month_product/month_consume/month_diff_rate 用 SzwgBusinessYear 累计相减；标准值用 SzwgBusiness 单月。盐田 202605 供水 CLI 2,498,583 / 库 2,429,789；202606 单月率 CLI 12.92% / 库 10.35%。",
        "impact": "CX-032 的 202605、JL-036 的 8 个单位出现口径差异；不是数据错，是口径没定稿。",
        "action": "产品/业务确认「当月」到底取哪套值；定稿后 CLI 和标准集同时改，不能一边单月、一边累计差。",
    },
    {
        "owner": "指标口径",
        "issue": "yeardiff（人工录入）与 year_cum_diff_rate（公式）并存且差异大",
        "evidence": "福田 202602：yeardiff=0.036，year_cum_diff_rate=-0.0918；16 题标准值与公式命令一致。",
        "impact": "新映射表备注「yeardiff，不是 year_cum_diff_rate」与业务核对结果冲突，按备注迁会直接错。",
        "action": "明确看板「年累计产销差率」到底展示哪个 KPI；若两者都要保留，命令名必须能区分，禁止同名混用。",
    },
    {
        "owner": "指标口径",
        "issue": "率的小数尺度不统一",
        "evidence": "year_cum_diff_rate / month_diff_rate 返回 0.0936（0–1）；leakage_analysis_information.totaldiffrate 返回 2.828（0–100）。",
        "impact": "同一个适配器里两种尺度，乘 100 与否会直接造成 100 倍误差。",
        "action": "在 authority 的返回契约里给每个指标标 scale（ratio01 / percent）；或统一全部改成 0–1。",
    },
    {
        "owner": "指标口径",
        "issue": "momValue / yoyValue 是上期值，不是变化值",
        "evidence": "month_diff_water 202608：statValue=-181185，momValue=2727989（202607 的值）；year_cum_diff_rate 202608：momValue=0.1063（202607 的率）。",
        "impact": "直接拿 momValue 当环比会得到完全错误的结论。",
        "action": "字段改名为 prevValue / lastYearValue，或在文档里明确「环比需自行相减」。",
    },
    {
        "owner": "字段补齐",
        "issue": "消防栓维修明细缺排放水量 / 漏失水量",
        "evidence": "fire_hydrant_maintenance_work 7 月 1007 条，字段清单无 discharge_water / water_leakage；fire_hydrant_flushing_report 是另一张上报表（7 月 6 条）。",
        "impact": "JL-018 只能核对维修单数，两列水量不可核。",
        "action": "在消防栓维修明细补 discharge_water、water_leakage；若排放量来自上报表，请提供工单号关联规则。",
    },
    {
        "owner": "字段补齐",
        "issue": "检漏明细缺到场时间 / 完成判定字段",
        "evidence": "JL-021 需要 arrive_time，JL-022 需要 finish_time 或 step_status_name，JL-019 需要按 create_time 倒序取前 10；当前命令被 E101 阻塞，字段是否返回也未知。",
        "impact": "即使命令改短，这三题仍可能因为字段缺失或排序能力不足而不可测。",
        "action": "改短命令码的同时，确认 arrive_time、finish_time、create_time 返回，并提供 sort 参数或明确默认排序。",
    },
    {
        "owner": "字段补齐",
        "issue": "爆管聚合命令没有三级部门维度",
        "evidence": "4 个 *_pipe_diameter_burst 只返回 dimensionValue=单位；标准值 BJ-017 的 key 含三级部门。4 个命令 7 月按单位汇总后仍有 25 组与标准值不一致。",
        "impact": "BJ-017 只能验收单位级总量，无法验收「下钻到三级部门」。",
        "action": "给聚合命令加 dimensionType=2（三级部门）或 l3_name；或开放 water_supply_pipe_maintenance_work 的 caliber + l3_name 明细。",
    },
    {
        "owner": "字段补齐",
        "issue": "没有 DMA 物理漏损 / DMA 小区数的对应命令",
        "evidence": "标准 DMA-026 的漏损量来自 dwd_lsxt_wlls.physicalloss（diffresult=GOOD），小区数来自该表去重 dmaid。CLI 的 month_loss_water 是总漏损量（福田 1,390,993 vs 标准 484,990），month_physical_leakage_amount 是日粒度 2,134 行。",
        "impact": "DMA-026 只能核对累计产销差率一列。",
        "action": "新增按组织聚合的 DMA 物理漏损命令（SUM(physicalloss)、COUNT(DISTINCT dmaid)），或暴露可与 wlls 对齐的字段。",
    },
    {
        "owner": "数据质量",
        "issue": "预警明细与 dwd_lsxt_bj 行集不一致",
        "evidence": "7 月 NightFlow：CLI 558 条 / 库 549 条，CLI TOP10 少 1 条计数、缺 2 个 DMA；7 月全部预警：CLI 11,308 条 / 库 11,558 条，差 250。",
        "impact": "BJ-003、BJ-016 的数值不可直接采信。",
        "action": "核对 leakage_warning_information 的底层表、日期边界和去重规则，与 dwd_lsxt_bj 对齐。",
    },
    {
        "owner": "数据质量",
        "issue": "handled 字段语义与库表不一致",
        "evidence": "CLI handled：None 8,662 / True 2,645 / False 1；库表 NOT handled = 1；handldate / handlperson 在 8,664 条上同时为空。",
        "impact": "BJ-016 的「未处理数 / 超 10 天未处理数」完全不可用（CLI 8,663 vs 标准 1）。",
        "action": "返回与 dwd_lsxt_bj.handled 同源的字段，或提供按月汇总的预警统计命令；不要把处置记录表的 null 当成未处理。",
    },
    {
        "owner": "数据质量",
        "issue": "源表缺月 / 最新月未完整",
        "evidence": "year_cum_diff_rate 全单位返回 314 行（应 315），宝安水务集团缺 202602；202608 的 month_product 供水量为 0、售水量 155。",
        "impact": "CX-036 缺 1 个单元格；任何以 202608 为最新月的看板都会展示异常值。",
        "action": "确认宝安 202602 是否源表缺月；T-2 月未完成装载时，接口应返回明确状态而不是 0。",
    },
]


def render_contract_findings() -> str:
    rows = []
    for f in CONTRACT_FINDINGS:
        rows.append(
            "<tr>"
            f"<td>{esc(f['owner'])}</td>"
            f"<td class='issue'>{esc(f['issue'])}</td>"
            f"<td>{esc(f['evidence'])}</td>"
            f"<td>{esc(f['impact'])}</td>"
            f"<td>{esc(f['action'])}</td>"
            "</tr>"
        )
    return (
        '<div class="table-wrap wide"><table><thead><tr>'
        "<th>责任方</th><th>问题</th><th>实测证据</th><th>影响</th><th>优化建议</th>"
        f"</tr></thead><tbody>{''.join(rows)}</tbody></table></div>"
    )


def main() -> None:
    payload = json.loads(RESULTS.read_text(encoding="utf-8"))
    results: List[Dict[str, Any]] = payload["results"]
    counts: Dict[str, int] = {}
    for r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1

    cards = [
        ("16", "题目总数", "neutral"),
        (str(counts.get("PASS", 0)), "通过", "pass"),
        (str(counts.get("VALUE_DIFF", 0)), "数值不符", "fail"),
        (str(counts.get("CALIBER_DIFF", 0)), "口径不符", "warn"),
        (str(counts.get("PARTIAL", 0)), "部分可测", "partial"),
        (str(counts.get("BLOCKED", 0)), "接口阻塞", "blocked"),
    ]
    cards_html = "".join(
        f'<div class="card {cls}"><div class="num">{num}</div><div class="label">{label}</div></div>'
        for num, label, cls in cards
    )

    overview_rows = []
    for r in results:
        main_diff = (r.get("diffs") or [{}])[0].get("message", "")
        sum_actual, sum_expected = SUMMARY.get(
            r["case_id"], (r.get("actual_text", ""), r.get("expected_text", ""))
        )
        overview_rows.append(
            "<tr>"
            f"<td><a href='#{esc(r['case_id'])}'><strong>{esc(r['case_id'])}</strong></a></td>"
            f"<td class='q'>{esc(r['question'])}</td>"
            f"<td>{badge(r['status'])}</td>"
            f"<td class='sum'>{esc(sum_actual)}</td>"
            f"<td class='sum'>{esc(sum_expected)}</td>"
            f"<td class='diff'>{esc(main_diff)}</td>"
            "</tr>"
        )

    cases_html = "".join(f'<div id="{esc(r["case_id"])}">{render_case(r)}</div>' for r in results)

    def ids(status: str) -> str:
        return "、".join(r["case_id"] for r in results if r["status"] == status) or "无"

    lead = (
        f"结论：16 题中 <strong>{counts.get('PASS', 0)} 题通过</strong>（{ids('PASS')}），"
        f"<strong>{counts.get('VALUE_DIFF', 0)} 题数值不符</strong>（{ids('VALUE_DIFF')}），"
        f"<strong>{counts.get('CALIBER_DIFF', 0)} 题口径不符</strong>（{ids('CALIBER_DIFF')}），"
        f"<strong>{counts.get('PARTIAL', 0)} 题部分可测</strong>（{ids('PARTIAL')}），"
        f"<strong>{counts.get('BLOCKED', 0)} 题被网关 32 字符限制阻塞</strong>（{ids('BLOCKED')}）。"
        "可比对的题里，累计类指标（year_cum_diff_rate）质量最好；"
        "问题集中在月度量口径、预警明细行集和长命令码。"
    )

    html_doc = f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>漏损智能体 CLI 接口对账报告</title>
<style>
  :root {{
    --bg: #f5f6f8; --panel: #ffffff; --text: #1d2939; --muted: #667085;
    --line: #e4e7ec; --soft: #f9fafb;
    --pass: #067647; --pass-bg: #ecfdf3;
    --fail: #b42318; --fail-bg: #fef3f2;
    --warn: #b54708; --warn-bg: #fffaeb;
    --partial: #175cd3; --partial-bg: #eff8ff;
    --stale: #6941c6; --stale-bg: #f4f3ff;
    --blocked: #475467; --blocked-bg: #f2f4f7;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; background: var(--bg); color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
    font-size: 14px; line-height: 1.65;
  }}
  .page {{ max-width: 1240px; margin: 0 auto; padding: 36px 24px 64px; }}
  header {{ margin-bottom: 24px; }}
  h1 {{ font-size: 26px; margin: 0 0 8px; letter-spacing: -.02em; }}
  .sub {{ color: var(--muted); font-size: 13px; }}
  .sub span {{ margin-right: 18px; white-space: nowrap; }}
  .cards {{ display: grid; grid-template-columns: repeat(6, 1fr); gap: 10px; margin: 22px 0; }}
  .card {{ background: var(--panel); border: 1px solid var(--line); border-radius: 10px; padding: 14px 16px; }}
  .card .num {{ font-size: 26px; font-weight: 700; line-height: 1.1; }}
  .card .label {{ color: var(--muted); font-size: 12px; margin-top: 4px; }}
  .card.pass .num {{ color: var(--pass); }} .card.fail .num {{ color: var(--fail); }}
  .card.warn .num {{ color: var(--warn); }} .card.partial .num {{ color: var(--partial); }}
  .card.blocked .num {{ color: var(--blocked); }}
  section {{ margin-top: 30px; }}
  h2 {{ font-size: 18px; margin: 0 0 12px; padding-bottom: 8px; border-bottom: 1px solid var(--line); }}
  h3 {{ font-size: 15px; margin: 22px 0 8px; }}
  h4 {{ font-size: 13px; color: var(--muted); margin: 18px 0 8px; font-weight: 600; }}
  p {{ margin: 6px 0; }}
  .lead {{ background: var(--panel); border: 1px solid var(--line); border-left: 4px solid var(--partial); border-radius: 8px; padding: 14px 18px; }}
  .note {{ background: var(--soft); border: 1px dashed var(--line); border-radius: 8px; padding: 10px 14px; color: #344054; font-size: 13px; }}
  .table-wrap {{ overflow-x: auto; background: var(--panel); border: 1px solid var(--line); border-radius: 10px; }}
  .table-wrap.wide table {{ min-width: 1080px; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
  th, td {{ text-align: left; padding: 9px 10px; border-bottom: 1px solid var(--line); vertical-align: top; }}
  th {{ background: var(--soft); color: var(--muted); font-weight: 600; white-space: nowrap; }}
  tr:last-child td {{ border-bottom: 0; }}
  td.q {{ min-width: 220px; }}
  td:first-child, th:first-child {{ white-space: nowrap; }}
  td.diff {{ color: var(--fail); min-width: 200px; font-weight: 500; }}
  td.sum {{ color: #344054; font-weight: 500; white-space: nowrap; }}
  .diff-expected {{ color: var(--partial); font-variant-numeric: tabular-nums; }}
  .diff-actual {{ color: var(--fail); font-weight: 600; font-variant-numeric: tabular-nums; }}
  .diff-delta {{ color: var(--warn); font-weight: 700; font-variant-numeric: tabular-nums; }}
  td.issue {{ font-weight: 600; min-width: 180px; }}
  td.params {{ font-family: ui-monospace, SFMono-Regular, Consolas, monospace; font-size: 12px; color: var(--muted); }}
  code {{ font-family: ui-monospace, SFMono-Regular, Consolas, monospace; font-size: 12px; background: var(--soft); padding: 1px 5px; border-radius: 4px; }}
  .badge {{ display: inline-block; padding: 1px 8px; border-radius: 999px; font-size: 12px; font-weight: 600; white-space: nowrap; }}
  .badge.pass {{ color: var(--pass); background: var(--pass-bg); }}
  .badge.fail {{ color: var(--fail); background: var(--fail-bg); }}
  .badge.warn {{ color: var(--warn); background: var(--warn-bg); }}
  .badge.partial {{ color: var(--partial); background: var(--partial-bg); }}
  .badge.stale {{ color: var(--stale); background: var(--stale-bg); }}
  .badge.blocked {{ color: var(--blocked); background: var(--blocked-bg); }}
  .case {{ background: var(--panel); border: 1px solid var(--line); border-radius: 10px; margin-bottom: 10px; overflow: hidden; }}
  .case > summary {{ cursor: pointer; padding: 12px 16px; display: flex; align-items: center; gap: 10px; list-style: none; }}
  .case > summary::-webkit-details-marker {{ display: none; }}
  .case-id {{ font-family: ui-monospace, SFMono-Regular, Consolas, monospace; font-weight: 700; min-width: 62px; }}
  .case-q {{ flex: 1; }}
  .case-summary {{ color: var(--muted); font-size: 12px; white-space: nowrap; max-width: 380px; overflow: hidden; text-overflow: ellipsis; }}
  .case-body {{ padding: 4px 18px 20px; border-top: 1px solid var(--line); }}
  .compare-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 0; align-items: start; padding-top: 16px; }}
  .compare-grid > .col {{ min-width: 0; padding: 0 18px; }}
  .compare-grid > .col:first-child {{ padding-left: 0; }}
  .compare-grid > .col:last-child {{ padding-right: 0; border-left: 1px solid var(--line); }}
  .compare-grid h4 {{ margin-top: 0; }}
  .compare-grid .cli h4 {{ color: var(--fail); }}
  .compare-grid .std h4 {{ color: var(--partial); }}
  .compare-grid .table-wrap table {{ font-size: 12px; }}
  .compare-grid th, .compare-grid td {{ padding: 7px 9px; }}
  .compare-grid .empty {{ background: var(--soft); border: 1px dashed var(--line); border-radius: 8px; padding: 18px; text-align: center; }}
  .case-body > h4 {{ margin-top: 24px; padding-top: 16px; border-top: 1px solid var(--line); }}
  .meta-grid {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px 18px; margin: 0; padding: 12px 0 14px; border-bottom: 1px solid var(--line); font-size: 13px; }}
  .meta-label {{ color: var(--muted); margin-right: 8px; }}
  .col {{ min-width: 0; }}
  .summary-line {{ color: var(--muted); font-size: 12px; margin-bottom: 8px; }}
  .root {{ background: var(--warn-bg); border-left: 3px solid var(--warn); padding: 10px 14px; border-radius: 6px; }}
  .recommend {{ background: var(--partial-bg); border-left: 3px solid var(--partial); padding: 10px 14px; border-radius: 6px; }}
  .evidence {{ margin: 6px 0; padding-left: 20px; color: #344054; }}
  .evidence li {{ margin: 3px 0; }}
  pre.cmd, pre.sql {{ margin: 0 0 8px; padding: 12px 14px; background: #1d2939; color: #e4e7ec; border-radius: 8px; overflow: auto; font-family: ui-monospace, SFMono-Regular, Consolas, monospace; font-size: 12px; line-height: 1.6; white-space: pre-wrap; word-break: break-word; }}
  pre.cmd code, pre.sql code {{ background: transparent; color: inherit; padding: 0; border-radius: 0; font-size: inherit; }}
  pre.cmd {{ max-height: 240px; }}
  pre.sql {{ max-height: 420px; }}
  .cmd-note {{ color: var(--muted); font-size: 12px; margin: 2px 0 10px; }}
  .empty {{ color: var(--muted); }}
  .ok {{ color: var(--pass); }}
  .more {{ color: var(--muted); font-size: 12px; padding: 6px 10px; }}
  footer {{ margin-top: 36px; color: var(--muted); font-size: 12px; border-top: 1px solid var(--line); padding-top: 12px; }}
  @media (max-width: 900px) {{
    .cards {{ grid-template-columns: repeat(2, 1fr); }}
    .meta-grid {{ grid-template-columns: 1fr; }}
    .compare-grid {{ grid-template-columns: 1fr; }}
    .compare-grid > .col {{ padding: 0; }}
    .compare-grid > .col:last-child {{ border-left: 0; border-top: 1px solid var(--line); padding-top: 14px; margin-top: 14px; }}
    .case-summary {{ display: none; }}
  }}
  @media print {{
    body {{ background: #fff; }}
    .page {{ max-width: none; padding: 0; }}
    .case {{ break-inside: avoid; }}
    details {{ open: true; }}
    .table-wrap {{ overflow: visible; }}
  }}
</style>
</head>
<body>
<div class="page">
  <header>
    <h1>漏损智能体 CLI 接口对账报告</h1>
    <div class="sub">
      <span>对象：leakage-agent-cli（authority + invoke）</span>
      <span>范围：16 道业务核对题</span>
      <span>生成时间：{esc(payload.get('generated_at'))}</span>
    </div>
  </header>

  <div class="cards">{cards_html}</div>

  <p class="note">
    <strong>口径：</strong>本次测试的标准值 = 用原标准 SQL 在当前库跑出的结果；CLI 为本次实调。
    每题只比两列：CLI 实测 vs 标准值。字段、容差沿用题目的 value_fields / tolerance。
  </p>

  <p class="lead">{lead}</p>

  <section>
    <h2>一、16 题总表</h2>
    <div class="table-wrap wide">
      <table>
        <thead><tr><th>题号</th><th>问题</th><th>结论</th><th>CLI 结果</th><th>标准值</th><th>主要差异</th></tr></thead>
        <tbody>{''.join(overview_rows)}</tbody>
      </table>
    </div>
  </section>

  <section>
    <h2>二、接口级问题与优化点</h2>
    <p class="summary-line">按责任方归类。每条都对应本次实测证据，可直接拆给同事跟进。</p>
    {render_contract_findings()}
  </section>

  <section>
    <h2>三、逐题详情</h2>
    {cases_html}
  </section>

  <footer>
    数据源：leakage-agent-cli（BIZ_GATEWAY_URL=https://assistant.sz-water.com.cn）。
    标准值：用 data/golden/golden_dataset.json 的 business_verified 题目所附标准 SQL，
    在当前库只读重跑得到的结果；字段与容差沿用题目契约。
    原始 CLI 响应已落盘到 cli_eval/out/raw/，结构化结果见 cli_eval/out/results.json。
    首轮全量取数约 5 分钟（含 227 页预警、114 页 DMA）；本报告重跑命中本地缓存，数值不变。
    报告只做取数与比对，不修改业务库、不调用写入命令。
  </footer>
</div>
<script>
  function openHash() {{
    if (!location.hash) return;
    const target = document.querySelector(location.hash);
    if (!target) return;
    const details = target.querySelector('details');
    if (details) details.open = true;
    target.scrollIntoView({{ behavior: 'smooth', block: 'start' }});
  }}
  window.addEventListener('hashchange', openHash);
  window.addEventListener('load', openHash);
</script>
</body>
</html>
"""
    REPORT.write_text(html_doc, encoding="utf-8")
    print(f"written {REPORT}")


if __name__ == "__main__":
    main()
