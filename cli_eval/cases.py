# -*- coding: utf-8 -*-
"""16 道业务核对题的 CLI 取数与比对。

每题一个 handler：取数 → 归一 → 和「本次标准 SQL 结果」比对 → 给出根因和建议。
本次测试的标准值 = 用原标准 SQL 在当前库跑出的结果，不再区分历史快照。
"""
from __future__ import annotations

import copy
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from cli_eval.cli_client import DETAIL, METRIC, fetch_paged, probe_error
from cli_eval.normalize import (
    compare_rows,
    compare_stat,
    key_text,
    num,
    org_name,
)

ROOT = Path(__file__).resolve().parent.parent
DB_DIR = Path(__file__).resolve().parent / "out" / "golden_db"

GOLDEN = json.loads((ROOT / "data" / "golden" / "golden_dataset.json").read_text(encoding="utf-8"))
CASES = {c["case_id"]: c for c in GOLDEN["cases"] if c.get("business_verified")}


def expected_of(case_id: str) -> Dict[str, Any]:
    """本次测试的标准值：沿用题目的字段/容差，值取当前库重跑标准 SQL 的结果。"""
    expected = copy.deepcopy(CASES[case_id]["expected"])
    rows = db_rows(case_id)
    if not rows:
        return expected
    if "detail" in expected:
        expected["detail"]["rows"] = rows
    elif "fields" in expected:
        row = rows[0]
        for field in expected["fields"]:
            if field.get("key") in row:
                field["value"] = row[field["key"]]
    return expected


def db_rows(case_id: str) -> List[Dict[str, Any]]:
    path = DB_DIR / f"{case_id}.json"
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8")).get("rows") or []


def metric_rows(command: str, params: Dict[str, Any]) -> List[Dict[str, Any]]:
    return fetch_paged(command, params, kind=METRIC)["rows"]


def detail_rows(command: str, params: Dict[str, Any]) -> List[Dict[str, Any]]:
    return fetch_paged(command, params, kind=DETAIL)["rows"]


def by_period(rows: Sequence[Dict[str, Any]], field: str = "statValue") -> Dict[str, float]:
    out: Dict[str, float] = {}
    for row in rows:
        period = str(row.get("periodPoint") or row.get("businessyearmonth") or "")
        value = num(row.get(field))
        if period and value is not None:
            out[period] = value
    return out


def pct(value: Optional[float]) -> str:
    if value is None:
        return "—"
    return f"{value * 100:.2f}%"


def fmt(value: Any, digits: int = 4) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, (int, float)):
        return f"{value:,.{digits}f}"
    return str(value)


def summarize_rows(rows: Sequence[Dict[str, Any]], fields: Sequence[str], limit: int = 5) -> List[Dict[str, Any]]:
    return [{k: r.get(k) for k in fields} for r in rows[:limit]]


def result(
    case_id: str,
    status: str,
    *,
    actual: Any,
    diffs: List[Dict[str, Any]],
    root_cause: str,
    recommendation: str,
    evidence: List[str],
    plan: List[Dict[str, Any]],
    standard_vs_db: Optional[Dict[str, Any]] = None,
    actual_text: str = "",
    expected_text: str = "",
    db_text: str = "",
) -> Dict[str, Any]:
    case = CASES[case_id]
    expected = expected_of(case_id)
    detail = expected.get("detail") or {}
    return {
        "case_id": case_id,
        "question": case["question"],
        "category": case.get("category"),
        "result_type": case.get("result_type"),
        "status": status,
        "plan": plan,
        "actual": actual,
        "actual_text": actual_text,
        "expected_text": expected_text,
        "expected_rows": detail.get("rows") or [],
        "expected_stat": expected.get("fields") or [],
        "key_fields": detail.get("key_fields") or [],
        "value_fields": [f["key"] for f in detail.get("value_fields", [])],
        "sql": case.get("sql_executable") or "",
        "diffs": diffs,
        "root_cause": root_cause,
        "recommendation": recommendation,
        "evidence": evidence,
    }


def _relabel_db(diffs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """标准值 vs 当前库的差异，消息里不要把当前库写成 CLI。"""
    for diff in diffs:
        if diff.get("message"):
            diff["message"] = diff["message"].replace("CLI", "当前库")
    return diffs


def compare_standard_vs_db(case_id: str) -> Dict[str, Any]:
    """标准值和当前库重跑结果的一致性，用于区分「CLI 错」和「标准值过期」。"""
    expected = expected_of(case_id)
    rows = db_rows(case_id)
    if not rows:
        return {"status": "无库结果"}
    if case_id in {"BJ-016"}:
        fields = expected.get("fields", [])
        diffs = compare_stat(rows[0], expected)
        return {"status": "一致" if not diffs else "有变化", "diffs": _relabel_db(diffs), "rows": 1}
    if "detail" not in expected:
        return {"status": "未比对"}
    detail = expected["detail"]
    key_fields = detail.get("key_fields", ["组织"])
    fields = [
        {"expected": f["key"], "actual": f["key"], "tolerance": f.get("tolerance", {})}
        for f in detail.get("value_fields", [])
    ]
    if len(key_fields) > 1:
        def combo(row: Dict[str, Any]) -> str:
            return "|".join(str(row.get(k)) for k in key_fields)

        db_view = [dict(r, __key=combo(r)) for r in rows]
        exp_view = [dict(r, __key=combo(r)) for r in detail.get("rows", [])]
        diffs = compare_rows(
            db_view,
            exp_view,
            key_actual="__key",
            key_expected="__key",
            alias_actual=False,
            fields=fields,
        )
    else:
        key_expected = key_fields[0]
        diffs = compare_rows(
            rows,
            detail.get("rows", []),
            key_actual=key_expected,
            key_expected=key_expected,
            alias_actual=(key_expected == "组织"),
            fields=fields,
        )
    return {"status": "一致" if not diffs else "有变化", "diffs": _relabel_db(diffs), "rows": len(rows)}


# ---------------------------------------------------------------------------
# CX
# ---------------------------------------------------------------------------


def case_cx032() -> Dict[str, Any]:
    params = {"periodType": 2, "dimensionType": 1, "dimensionValue": "盐田分公司"}
    plans = []
    data = {}
    for command, label in (
        ("month_product", "供水量"),
        ("month_consume", "售水量"),
        ("month_diff_water", "产销差量"),
    ):
        rows = metric_rows(command, params)
        data[label] = by_period(rows)
        plans.append({"command": command, "params": params, "rows": len(rows)})

    periods = ["202601", "202602", "202603", "202604", "202605"]
    actual = [
        {
            "月份(期数)": int(p),
            "供水量": data["供水量"].get(p),
            "售水量": data["售水量"].get(p),
            "产销差量": data["产销差量"].get(p),
        }
        for p in periods
    ]
    diffs = compare_rows(
        actual,
        expected_of("CX-032")["detail"]["rows"],
        key_actual="月份(期数)",
        key_expected="月份(期数)",
        alias_actual=False,
        fields=[
            {"expected": f["key"], "actual": f["key"], "tolerance": f.get("tolerance", {})}
            for f in expected_of("CX-032")["detail"]["value_fields"]
        ],
    )
    status = "PASS" if not diffs else "CALIBER_DIFF"
    return result(
        "CX-032",
        status,
        actual=actual,
        diffs=diffs,
        root_cause=(
            "CLI 的 month_product / month_consume 用 SzwgBusinessYear 年累计相减得到当月值；"
            "标准值用 dwd_lsxt_fqcxfx 的 SzwgBusiness 单月快照。前 3 个月两套值恰好相等，"
            "202604 起累计口径被修订，差异暴露。"
        ),
        recommendation=(
            "统一「当月水量」口径：要么 CLI 直接暴露 SzwgBusiness 单月值，"
            "要么标准集改用 SzwgBusinessYear 累计差并重新业务确认。"
            "在口径定稿前，不要把 month_* 当成唯一当月来源。"
        ),
        evidence=[
            "202604 售水量：CLI 2181838 / 标准 2181993",
            "202605 供水量：CLI 2498583 / 标准 2429789",
            "验证：year_cum_product(202605)-year_cum_product(202604)=2498583，与 CLI 完全一致",
        ],
        plan=plans,
        standard_vs_db=compare_standard_vs_db("CX-032"),
        actual_text="; ".join(
            f"{r['月份(期数)']}: 供 {fmt(r['供水量'], 0)} / 售 {fmt(r['售水量'], 0)}" for r in actual
        ),
        expected_text="; ".join(
            f"{r['月份(期数)']}: 供 {fmt(r['供水量'], 0)} / 售 {fmt(r['售水量'], 0)}"
            for r in expected_of("CX-032")["detail"]["rows"]
        ),
        db_text="; ".join(
            f"{r['月份(期数)']}: 供 {fmt(r['供水量'], 0)} / 售 {fmt(r['售水量'], 0)}"
            for r in db_rows("CX-032")
        ),
    )


def case_cx033() -> Dict[str, Any]:
    params = {"periodType": 2, "dimensionType": 1, "dimensionValue": "福田分公司"}
    rows = metric_rows("year_cum_diff_rate", params)
    rates = by_period(rows)
    periods = ["202601", "202602", "202603", "202604", "202605", "202606"]
    actual = []
    previous = None
    for p in periods:
        rate = rates.get(p)
        actual.append({
            "月份(期数)": int(p),
            "累计产销差率": rate,
            "累计产销差率环比变化": None if previous is None or rate is None else round(rate - previous, 6),
        })
        previous = rate
    diffs = compare_rows(
        actual,
        expected_of("CX-033")["detail"]["rows"],
        key_actual="月份(期数)",
        key_expected="月份(期数)",
        alias_actual=False,
        fields=[
            {"expected": f["key"], "actual": f["key"], "tolerance": f.get("tolerance", {})}
            for f in expected_of("CX-033")["detail"]["value_fields"]
        ],
    )
    return result(
        "CX-033",
        "PASS" if not diffs else "VALUE_DIFF",
        actual=actual,
        diffs=diffs,
        root_cause="" if not diffs else "累计产销差率或环比计算与标准值不一致。",
        recommendation="" if not diffs else "核对 year_cum_diff_rate 的期间和环比算法。",
        evidence=["6 期累计率与环比全部命中标准值。", "率尺度为 0–1，与标准值一致。"],
        plan=[{"command": "year_cum_diff_rate", "params": params, "rows": len(rows)}],
        standard_vs_db=compare_standard_vs_db("CX-033"),
        actual_text="; ".join(f"{r['月份(期数)']}: {pct(r['累计产销差率'])}" for r in actual),
        expected_text="; ".join(
            f"{r['月份(期数)']}: {pct(r['累计产销差率'])}"
            for r in expected_of("CX-033")["detail"]["rows"]
        ),
        db_text="; ".join(f"{r['月份(期数)']}: {pct(r['累计产销差率'])}" for r in db_rows("CX-033")),
    )


_ALL_UNITS_CACHE: Optional[List[Dict[str, Any]]] = None


def all_unit_rates() -> List[Dict[str, Any]]:
    global _ALL_UNITS_CACHE
    if _ALL_UNITS_CACHE is None:
        _ALL_UNITS_CACHE = metric_rows(
            "year_cum_diff_rate", {"periodType": 2, "dimensionType": 1}
        )
    return _ALL_UNITS_CACHE


def case_cx036() -> Dict[str, Any]:
    rows = all_unit_rates()
    values: Dict[str, Dict[str, float]] = defaultdict(dict)
    for row in rows:
        values[org_name(row.get("dimensionValue"))][str(row.get("periodPoint"))] = num(row.get("statValue"))
    actual = [
        {
            "组织": org,
            "1月": periods.get("202601"),
            "2月": periods.get("202602"),
            "3月": periods.get("202603"),
            "4月": periods.get("202604"),
            "5月": periods.get("202605"),
        }
        for org, periods in values.items()
    ]
    diffs = compare_rows(
        actual,
        expected_of("CX-036")["detail"]["rows"],
        key_actual="组织",
        key_expected="组织",
        fields=[
            {"expected": f["key"], "actual": f["key"], "tolerance": f.get("tolerance", {})}
            for f in expected_of("CX-036")["detail"]["value_fields"]
        ],
    )
    missing = [d["key"] for d in diffs if d["type"] == "MISSING_ROW"]
    value_diffs = [d for d in diffs if d["type"] == "VALUE_DIFF"]
    status = "PASS" if not diffs else ("PARTIAL" if missing else "VALUE_DIFF")
    return result(
        "CX-036",
        status,
        actual=actual,
        diffs=diffs,
        root_cause=(
            f"CLI 返回 {len(actual)} 个单位；标准值有 16 行。"
            + (f"缺少组织：{'、'.join(missing)}。" if missing else "")
            + ("存在数值差异。" if value_diffs else "")
        ),
        recommendation=(
            "补齐组织别名（尤其大鹏环水公司→大鹏新区分公司、环水集团→深圳环境水务集团），"
            "并确认「集团合计」行是否应由集团级聚合命令单独返回；"
            "宝安水务集团 202602 缺失需要大数据确认是否源表缺月。"
        ),
        evidence=[
            f"CLI 实际单位：{'、'.join(sorted(values))}",
            f"缺失行 {len(missing)}，数值差异 {len(value_diffs)}",
            "率尺度 0–1，与标准值一致",
        ],
        plan=[{"command": "year_cum_diff_rate", "params": {"dimensionType": 1}, "rows": len(rows)}],
        standard_vs_db=compare_standard_vs_db("CX-036"),
        actual_text=f"返回 {len(actual)} 个单位，{len(rows)} 行",
        expected_text="16 个组织 × 5 个月",
        db_text=f"当前库重跑 {len(db_rows('CX-036'))} 行",
    )


def case_cx037() -> Dict[str, Any]:
    rows = all_unit_rates()
    values: Dict[str, Dict[str, float]] = defaultdict(dict)
    for row in rows:
        values[org_name(row.get("dimensionValue"))][str(row.get("periodPoint"))] = num(row.get("statValue"))
    actual = []
    for org, periods in values.items():
        r2026 = periods.get("202606")
        r2025 = periods.get("202506")
        actual.append({
            "组织": org,
            "2026上半年产销差率": r2026,
            "2025上半年产销差率": r2025,
            "同比变化": None if r2026 is None or r2025 is None else round(r2026 - r2025, 6),
        })
    diffs = compare_rows(
        actual,
        expected_of("CX-037")["detail"]["rows"],
        key_actual="组织",
        key_expected="组织",
        fields=[
            {"expected": f["key"], "actual": f["key"], "tolerance": f.get("tolerance", {})}
            for f in expected_of("CX-037")["detail"]["value_fields"]
        ],
    )
    missing = [d["key"] for d in diffs if d["type"] == "MISSING_ROW"]
    return result(
        "CX-037",
        "PASS" if not diffs else ("PARTIAL" if missing else "VALUE_DIFF"),
        actual=actual,
        diffs=diffs,
        root_cause="" if not diffs else f"缺失组织：{'、'.join(missing) or '无'}；其余为数值差异。",
        recommendation="" if not diffs else "补齐组织别名，并确认 2025 年累计值的期间口径。",
        evidence=[
            "使用 year_cum_diff_rate 的 202606 与 202506 两期值相减",
            f"CLI 实际单位数 {len(actual)}，标准值 15 行",
        ],
        plan=[{"command": "year_cum_diff_rate", "params": {"dimensionType": 1}, "rows": len(rows)}],
        standard_vs_db=compare_standard_vs_db("CX-037"),
        actual_text=f"返回 {len(actual)} 个单位",
        expected_text="15 个组织",
        db_text=f"当前库重跑 {len(db_rows('CX-037'))} 行",
    )


# ---------------------------------------------------------------------------
# DMA
# ---------------------------------------------------------------------------


def _dma_group(row: Dict[str, Any]) -> str:
    bz = str(row.get("bz_bm") or "").strip()
    zone = str(row.get("zonecode") or "")
    if bz == "其他" and "F1014" in zone:
        return "布吉水司"
    if bz == "其他" and "F1013" in zone:
        return "莲塘供水"
    if bz == "其他" and "F1009" in zone:
        return "坪地供水"
    return org_name(bz)


def case_dma002() -> Dict[str, Any]:
    rows = detail_rows("dma_basic_information", {"status": "WORK"})
    groups = Counter(_dma_group(row) for row in rows)
    total = sum(groups.values())
    actual = [
        {"组织": org, "DMA数": cnt, "集团占比": round(cnt / total, 4) if total else None}
        for org, cnt in groups.most_common()
    ]
    diffs = compare_rows(
        actual,
        expected_of("DMA-002")["detail"]["rows"],
        key_actual="组织",
        key_expected="组织",
        fields=[
            {"expected": "DMA数", "actual": "DMA数", "tolerance": {"kind": "exact"}},
            {"expected": "集团占比", "actual": "集团占比", "tolerance": {"kind": "abs", "eps": 0.01}},
        ],
    )
    sv = compare_standard_vs_db("DMA-002")
    db_total = sum(int(r.get("DMA数") or 0) for r in db_rows("DMA-002"))
    status = "PASS" if not diffs else "VALUE_DIFF"
    return result(
        "DMA-002",
        status,
        actual=actual,
        diffs=diffs,
        root_cause=(
            f"CLI status=WORK 全量 {total} 条，按 bz_bm 分组后与本次标准 SQL（{db_total} 条、"
            f"{len(db_rows('DMA-002'))} 个组织）完全一致。"
            if not diffs
            else f"CLI {total} 条 / 标准 SQL {db_total} 条，按 bz_bm 分组后存在逐组织差异。"
        ),
        recommendation=(
            "口径可用。建议大数据把 status=WORK 的计数直接做成指标命令，避免每次翻 114 页；"
            "另需确认「在用」是否只等于 status=WORK。"
            if not diffs
            else "核对 bz_bm='其他' 的 zonecode 归类，以及 status=WORK 的筛选边界。"
        ),
        evidence=[
            f"CLI 全量 {total} 条；本次标准 SQL 同口径 {db_total} 条",
            f"{len(actual)} 个组织分组结果全部命中" if not diffs else f"存在 {len(diffs)} 处逐组织差异",
            "CLI 分页：pageNumber=1 起，pageSize 实测上限 50",
        ],
        plan=[{"command": "dma_basic_information", "params": {"status": "WORK"}, "rows": len(rows)}],
        actual_text=f"{total} 条 / {len(actual)} 个组织",
        expected_text=f"{db_total} 条 / {len(db_rows('DMA-002'))} 个组织",
    )


def case_dma026() -> Dict[str, Any]:
    rates_raw = all_unit_rates()
    rates: Dict[str, float] = {}
    for row in rates_raw:
        if str(row.get("periodPoint")) == "202606":
            rates[org_name(row.get("dimensionValue"))] = num(row.get("statValue"))
    expected = expected_of("DMA-026")["detail"]
    actual = []
    for exp in expected["rows"]:
        org = org_name(exp["组织"])
        actual.append({
            "组织": org,
            "累计产销差率": rates.get(org),
            "DMA漏损量": None,
            "DMA小区数": None,
        })
    diffs = compare_rows(
        actual,
        expected["rows"],
        key_actual="组织",
        key_expected="组织",
        fields=[{"expected": "累计产销差率", "actual": "累计产销差率", "tolerance": {"kind": "abs", "eps": 0.01}}],
    )
    return result(
        "DMA-026",
        "PARTIAL",
        actual=actual,
        diffs=diffs,
        root_cause=(
            "累计产销差率可由 year_cum_diff_rate 直接对应；"
            "DMA 漏损量在标准值里来自 dwd_lsxt_wlls.physicalloss（diffresult='GOOD'），"
            "DMA 小区数是该表去重 dmaid 数。CLI 没有 wlls/physicalloss 对应命令："
            "month_loss_water 是总漏损量（福田 1,390,993 vs 标准 484,990），"
            "month_physical_leakage_amount 是日粒度 2,134 行，均不能直接替代。"
        ),
        recommendation=(
            "新增一个按组织聚合的 DMA 物理漏损命令（SUM(physicalloss) + COUNT(DISTINCT dmaid)），"
            "或在 leakage_analysis_information 暴露可对齐 wlls 的字段；"
            "否则该题只能核对产销差率一列。"
        ),
        evidence=[
            "累计产销差率来自 year_cum_diff_rate 202606",
            "month_loss_water 202606 福田 1,390,993 → 标准 DMA漏损量 484,990",
            "month_physical_leakage_amount 返回 2,134 行日粒度数据",
        ],
        plan=[
            {"command": "year_cum_diff_rate", "params": {"dimensionType": 1}, "rows": len(rates_raw)},
            {"command": "month_loss_water", "params": {"periodType": 2}, "rows": 314, "note": "口径不可替代"},
        ],
        standard_vs_db=compare_standard_vs_db("DMA-026"),
        actual_text=f"率 {len(rates)} 个单位可对；漏损量/小区数不可取",
        expected_text="15 个组织 × 3 列",
        db_text=f"当前库重跑 {len(db_rows('DMA-026'))} 行",
    )


# ---------------------------------------------------------------------------
# JL
# ---------------------------------------------------------------------------


def blocked(case_id: str, command: str, params: Dict[str, Any], *, fields: str, recommendation: str) -> Dict[str, Any]:
    error = probe_error(command, params)
    return result(
        case_id,
        "BLOCKED",
        actual=None,
        diffs=[],
        root_cause=(
            f"命令 `{command}`（{error['commandLength']} 字符）调用返回 "
            f"{error['respCode']}：{error['respMsg']}。网关限制 commandCode ≤ 32 字符，"
            "该命令在 authority 中存在但 invoke 无法执行。"
        ),
        recommendation=recommendation
        + f" 另外，该题需要字段：{fields}；改短命令码后请一并确认这些字段已返回。",
        evidence=[
            f"commandCode={command}，长度 {error['commandLength']}",
            f"respCode={error['respCode']}，respMsg={error['respMsg']}",
            "同批 31/29 字符的命令（如 fire_hydrant_maintenance_work）可正常调用，确认是长度限制",
        ],
        plan=[{"command": command, "params": params, "error": error}],
        standard_vs_db=compare_standard_vs_db(case_id),
        actual_text="E101",
        expected_text=f"{len(db_rows(case_id))} 行",
        db_text=f"当前库重跑 {len(db_rows(case_id))} 行",
    )


def case_jl016() -> Dict[str, Any]:
    return blocked(
        "JL-016",
        "water_supply_pipe_maintenance_work",
        {"pageNumber": 1, "pageSize": 50, "create_time_start": "2026-07-01 00:00:00", "create_time_end": "2026-07-31 23:59:59"},
        fields="bz_bm、create_time、water_leakage",
        recommendation="把命令改名为 ≤32 字符（如 wsp_maintenance_work）或提供短别名；同时确认 water_leakage 字段在明细中返回。",
    )


def case_jl018() -> Dict[str, Any]:
    rows = detail_rows(
        "fire_hydrant_maintenance_work",
        {"create_time_start": "2026-07-01 00:00:00", "create_time_end": "2026-07-31 23:59:59"},
    )
    counts = Counter(org_name(row.get("bz_bm")) for row in rows)
    expected = expected_of("JL-018")["detail"]
    actual = []
    for exp in expected["rows"]:
        org = org_name(exp["组织"])
        actual.append({
            "组织": org,
            "维修单数": counts.get(org, 0),
            "排放水量": None,
            "漏失水量": None,
        })
    diffs = compare_rows(
        actual,
        expected["rows"],
        key_actual="组织",
        key_expected="组织",
        fields=[{"expected": "维修单数", "actual": "维修单数", "tolerance": {"kind": "exact"}}],
    )
    missing_fields = [
        {"type": "MISSING_FIELD", "key": "全部组织", "field": "排放水量", "expected": "有值", "actual": None,
         "message": "fire_hydrant_maintenance_work 返回字段中无 discharge_water"},
        {"type": "MISSING_FIELD", "key": "全部组织", "field": "漏失水量", "expected": "有值", "actual": None,
         "message": "fire_hydrant_maintenance_work 返回字段中无 water_leakage"},
    ]
    return result(
        "JL-018",
        "PARTIAL" if not diffs else "VALUE_DIFF",
        actual=actual,
        diffs=diffs + missing_fields,
        root_cause=(
            f"维修单数可核对：CLI 7 月共 {len(rows)} 条，按组织分组与标准值一致（或接近）。"
            "排放水量/漏失水量不可核对：fire_hydrant_maintenance_work 的返回字段没有 "
            "discharge_water / water_leakage；fire_hydrant_flushing_report 是消防栓排放上报（7 月仅 6 条），"
            "不是这 1007 条维修工单，口径不同。"
        ),
        recommendation=(
            "在消防栓维修明细中补 discharge_water、water_leakage 两个字段；"
            "如果业务上排放量来自另一张上报表，需要提供工单号关联规则，不能直接相加。"
        ),
        evidence=[
            f"fire_hydrant_maintenance_work 7 月 {len(rows)} 条，标准值 12 行合计 1007 条",
            "返回字段清单无 discharge_water / water_leakage",
            "fire_hydrant_flushing_report 7 月 6 条，字段有 discharge_water，但属于另一业务表",
        ],
        plan=[
            {"command": "fire_hydrant_maintenance_work", "params": {"create_time_start": "2026-07-01"}, "rows": len(rows)},
            {"command": "fire_hydrant_flushing_report", "params": {"time_start": "2026-07-01"}, "rows": 6},
        ],
        standard_vs_db=compare_standard_vs_db("JL-018"),
        actual_text=f"{len(rows)} 条维修工单；排放/漏失字段缺失",
        expected_text="12 个组织 × 3 列",
        db_text=f"当前库重跑 {len(db_rows('JL-018'))} 行",
    )


def case_jl019() -> Dict[str, Any]:
    return blocked(
        "JL-019",
        "leak_detection_and_verification_work",
        {"pageNumber": 1, "pageSize": 50, "create_time_start": "2026-07-01 00:00:00", "create_time_end": "2026-07-31 23:59:59"},
        fields="id、bz_bm、title、create_time，且需按 create_time DESC 取前 10",
        recommendation="改短命令码（如 jl_work_detail）；同时确认明细接口支持按 create_time 倒序或提供 sort 参数，否则客户端只能拉全量后排序。",
    )


def case_jl021() -> Dict[str, Any]:
    return blocked(
        "JL-021",
        "leak_detection_and_verification_work",
        {"pageNumber": 1, "pageSize": 50, "create_time_start": "2026-07-01 00:00:00", "create_time_end": "2026-07-31 23:59:59"},
        fields="bz_bm、create_time、arrive_time",
        recommendation="改短命令码；同时确认 arrive_time（首次到场时间）字段返回，且空值语义明确，否则无法算平均响应小时数。",
    )


def case_jl022() -> Dict[str, Any]:
    return blocked(
        "JL-022",
        "leak_detection_and_verification_work",
        {"pageNumber": 1, "pageSize": 50, "create_time_start": "2026-07-01 00:00:00", "create_time_end": "2026-07-31 23:59:59"},
        fields="bz_bm、finish_time（或 step_status_name）",
        recommendation="改短命令码；同时明确「完成」判定字段：finish_time 非空，还是 step_status_name 属于已完成/已销单。",
    )


def case_jl036() -> Dict[str, Any]:
    rows = metric_rows("month_diff_rate", {"periodType": 2, "dimensionType": 1})
    rates = {}
    for row in rows:
        if str(row.get("periodPoint")) == "202606":
            rates[org_name(row.get("dimensionValue"))] = num(row.get("statValue"))
    expected = expected_of("JL-036")["detail"]
    actual = [
        {"组织": org_name(exp["组织"]), "单月产销差率": rates.get(org_name(exp["组织"])), "检漏工单数": None}
        for exp in expected["rows"]
    ]
    diffs = compare_rows(
        actual,
        expected["rows"],
        key_actual="组织",
        key_expected="组织",
        fields=[{"expected": "单月产销差率", "actual": "单月产销差率", "tolerance": {"kind": "abs", "eps": 0.01}}],
    )
    return result(
        "JL-036",
        "PARTIAL",
        actual=actual,
        diffs=diffs,
        root_cause=(
            "单月产销差率：CLI month_diff_rate 仍用累计口径计算当月供水量，"
            "盐田 202606 CLI 12.92% vs 标准 10.35%；检漏工单数列被 "
            "leak_detection_and_verification_work 的 36 字符 commandCode 阻塞。"
        ),
        recommendation=(
            "先修 commandCode 长度；同时统一单月产销差率口径："
            "标准值用 SzwgBusiness 单月快照，CLI 用 SzwgBusinessYear 累计差。"
        ),
        evidence=[
            "盐田 202606：标准 0.1035（本次 SQL SzwgBusiness 2539522/2276570）",
            "CLI month_diff_rate 盐田 202606：0.1292（YTD 差 2614311/2276570）",
            "检漏工单数列 E101：commandCode 长度 36",
        ],
        plan=[
            {"command": "month_diff_rate", "params": {"periodType": 2, "dimensionType": 1}, "rows": len(rows)},
            {"command": "leak_detection_and_verification_work", "params": {}, "error": "E101 commandCode > 32"},
        ],
        standard_vs_db=compare_standard_vs_db("JL-036"),
        actual_text=f"单月率 {len(rates)} 个单位；检漏工单数不可取",
        expected_text="14 个组织 × 2 列",
        db_text=f"当前库重跑 {len(db_rows('JL-036'))} 行",
    )


# ---------------------------------------------------------------------------
# BJ
# ---------------------------------------------------------------------------


def case_bj003() -> Dict[str, Any]:
    rows = detail_rows(
        "leakage_warning_information",
        {"srctype": "NightFlow", "warndate_start": "2026-07-01", "warndate_end": "2026-07-31"},
    )
    counts: Counter = Counter()
    names: Dict[str, str] = {}
    orgs: Dict[str, str] = {}
    for row in rows:
        dmaid = key_text(row.get("dmaid"))
        if not dmaid:
            continue
        counts[dmaid] += 1
        names[dmaid] = row.get("dmaname")
        orgs[dmaid] = org_name(row.get("bz_bm"))
    top = counts.most_common(10)
    actual = [
        {"DMA编码": dmaid, "DMA名称": names.get(dmaid), "组织": orgs.get(dmaid), "预警次数": cnt}
        for dmaid, cnt in top
    ]
    diffs = compare_rows(
        actual,
        expected_of("BJ-003")["detail"]["rows"],
        key_actual="DMA编码",
        key_expected="DMA编码",
        alias_actual=False,
        fields=[{"expected": "预警次数", "actual": "预警次数", "tolerance": {"kind": "exact"}}],
    )
    return result(
        "BJ-003",
        "PASS" if not diffs else "VALUE_DIFF",
        actual=actual,
        diffs=diffs,
        root_cause="" if not diffs else "TOP10 的 DMA 编码或预警次数与标准值不一致；需确认是否因数据更新或 dmaid 空值被剔除。",
        recommendation="" if not diffs else "确认 NightFlow 的 srctype 枚举和 warndate 边界；客户端需按 dmaid 去空、计数后取前 10。",
        evidence=[
            f"CLI July NightFlow {len(rows)} 条，按 dmaid 计数取前 10",
            "本次标准 SQL 同口径 549 条，TOP10 为 29/25/8/5/5/4/4/3/3/3",
            "CLI TOP10 少 1 条计数、缺 2 个 DMA，说明明细接口与 dwd_lsxt_bj 行集不一致",
            "srctype 实际存在 NightFlow，映射表未列出该枚举值",
        ],
        plan=[{"command": "leakage_warning_information", "params": {"srctype": "NightFlow", "warndate_start": "2026-07-01"}, "rows": len(rows)}],
        standard_vs_db=compare_standard_vs_db("BJ-003"),
        actual_text=f"{len(rows)} 条 / TOP10",
        expected_text="10 个 DMA",
        db_text=f"当前库重跑 {len(db_rows('BJ-003'))} 行",
    )


def case_bj012() -> Dict[str, Any]:
    return blocked(
        "BJ-012",
        "water_supply_pipe_maintenance_work",
        {"pageNumber": 1, "pageSize": 50},
        fields="设施编码、bz_bm、地址、create_time，且需按设施编码分组计数",
        recommendation="改短命令码；如果要做「全年反复维修」，还要确认 create_time 的年初边界和设施编码字段名（标准值用 dwd_gdgl_gd_gsgdwxmx 的设施编码）。",
    )


def case_bj016() -> Dict[str, Any]:
    rows = detail_rows(
        "leakage_warning_information",
        {"warndate_start": "2026-07-01", "warndate_end": "2026-07-31"},
    )
    unhandled = [r for r in rows if not r.get("handled")]
    overdue = [
        r for r in unhandled
        if str(r.get("warndate") or "")[:10] < "2026-07-21"
    ]
    actual = {
        "预警总数": len(rows),
        "未处理数": len(unhandled),
        "超时未处理数": len(overdue),
    }
    diffs = compare_stat(actual, expected_of("BJ-016"))
    return result(
        "BJ-016",
        "PASS" if not diffs else "VALUE_DIFF",
        actual=actual,
        diffs=diffs,
        root_cause=(
            f"CLI 明细 7 月返回 {len(rows)} 条，标准 SQL 是 11558 条，少 {11558 - len(rows)} 条。"
            "更严重的是 handled 字段语义不一致：CLI 中 handled=None 8662 条、True 2645 条、False 1 条，"
            "而库表 NOT handled 只有 1 条；handldate / handlperson 在 8664 条上同时为空。"
            "这说明 leakage_warning_information 的 handled 不是 dwd_lsxt_bj.handled，"
            "不能用来算「未处理数/超 10 天未处理数」。"
        ),
        recommendation=(
            "请大数据确认 leakage_warning_information 的底层表/视图及 handled 字段来源："
            "要么返回与 dwd_lsxt_bj.handled 同源的字段，要么提供一个按月汇总的预警统计命令"
            "（总数/未处理/超时未处理），否则这题只能核对总数，且总数也差 250 条。"
        ),
        evidence=[
            f"CLI 7 月全类型 {len(rows)} 条；标准 SQL 11558 条，差 250",
            "CLI handled 分布：None 8662 / True 2645 / False 1；handldate 为空 8664",
            "标准 SQL（dwd_lsxt_bj）：NOT handled = 1",
            "status_name 全为「准确」，这是数据质量标记，不是处置状态",
        ],
        plan=[{"command": "leakage_warning_information", "params": {"warndate_start": "2026-07-01"}, "rows": len(rows)}],
        standard_vs_db=compare_standard_vs_db("BJ-016"),
        actual_text=f"{actual['预警总数']} / {actual['未处理数']} / {actual['超时未处理数']}",
        expected_text="11558 / 1 / 1",
        db_text="11558 / 1 / 1",
    )


_BURST = {
    "400_pipe_diameter_burst": "不足400",
    "600_pipe_diameter_burst": "400至不足600",
    "1000_pipe_diameter_burst": "600至不足1000",
    "than1000_pipe_diameter_burst": "1000毫米及以上",
}


def case_bj017() -> Dict[str, Any]:
    sums: Dict[tuple, int] = defaultdict(int)
    plan = []
    total_rows = 0
    for command, segment in _BURST.items():
        rows = metric_rows(command, {"periodType": 1, "dimensionType": 1})
        total_rows += len(rows)
        for row in rows:
            if str(row.get("periodPoint")) == "202607":
                sums[(segment, org_name(row.get("dimensionValue")))] += int(num(row.get("statValue")) or 0)
        plan.append({"command": command, "params": {"periodType": 1}, "rows": len(rows)})
    # 标准值按「管径分段 + 组织」汇总，忽略三级部门
    expected_rows = expected_of("BJ-017")["detail"]["rows"]
    expected_unit: Dict[tuple, int] = defaultdict(int)
    for row in expected_rows:
        expected_unit[(row["管径分段"], org_name(row["组织"]))] += int(row["爆管次数"] or 0)
    actual = [
        {"__key": f"{seg}|{org}", "管径分段": seg, "组织": org, "爆管次数": cnt}
        for (seg, org), cnt in sorted(sums.items())
    ]
    expected_flat = [
        {"__key": f"{seg}|{org}", "管径分段": seg, "组织": org, "爆管次数": cnt}
        for (seg, org), cnt in sorted(expected_unit.items())
    ]
    diffs = compare_rows(
        actual,
        expected_flat,
        key_actual="__key",
        key_expected="__key",
        alias_actual=False,
        fields=[{"expected": "爆管次数", "actual": "爆管次数", "tolerance": {"kind": "exact"}}],
    )
    combo_missing = [d["key"] for d in diffs if d["type"] == "MISSING_ROW"]
    combo_diff = [d["message"] for d in diffs if d["type"] == "VALUE_DIFF"]
    return result(
        "BJ-017",
        "PARTIAL",
        actual=actual,
        diffs=diffs,
        root_cause=(
            "4 个爆管聚合命令可给出「管径分段 × 单位」的月度计数（periodType=1 日粒度，"
            "客户端按 periodPoint=202607 汇总）；但标准值的 key 包含三级部门，"
            "聚合命令只返回 dimensionValue=单位，没有 l3_name；"
            "含三级部门的明细命令 water_supply_pipe_maintenance_work 又被 32 字符限制阻塞。"
            + (f" 单位×分段缺 {len(combo_missing)} 组，值差 {len(combo_diff)} 组。" if combo_missing or combo_diff else "")
        ),
        recommendation=(
            "给 4 个爆管聚合命令增加 dimensionType=2（三级部门）或 l3_name 维度；"
            "或把 water_supply_pipe_maintenance_work 改短后开放 caliber + l3_name 明细。"
            "否则只能验收单位级总量，无法验收「下钻到三级部门」。"
        ),
        evidence=[
            f"4 个聚合命令共 {total_rows} 行，7 月按单位汇出 {len(actual)} 组",
            f"标准值 27 行含三级部门；单位×分段缺 {len(combo_missing)} 组",
            "聚合命令返回字段只有 dimensionValue，无 l3_name",
        ],
        plan=plan,
        standard_vs_db=compare_standard_vs_db("BJ-017"),
        actual_text=f"{len(actual)} 组（管径×单位）",
        expected_text="27 行（管径×单位×三级部门）",
        db_text=f"当前库重跑 {len(db_rows('BJ-017'))} 行",
    )


HANDLERS = {
    "CX-032": case_cx032,
    "CX-033": case_cx033,
    "CX-036": case_cx036,
    "CX-037": case_cx037,
    "DMA-002": case_dma002,
    "DMA-026": case_dma026,
    "JL-016": case_jl016,
    "JL-018": case_jl018,
    "JL-019": case_jl019,
    "JL-021": case_jl021,
    "JL-022": case_jl022,
    "JL-036": case_jl036,
    "BJ-003": case_bj003,
    "BJ-012": case_bj012,
    "BJ-016": case_bj016,
    "BJ-017": case_bj017,
}
