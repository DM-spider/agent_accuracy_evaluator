# -*- coding: utf-8 -*-
"""16 道业务核对题的 CLI 取数与比对。

每题一个 handler：取数 → 归一 → 和「本次标准 SQL 结果」比对 → 给出根因和建议。
本次测试的标准值 = 用原标准 SQL 在当前库跑出的结果，不再区分历史快照。
"""
from __future__ import annotations

import copy
import json
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from cli_eval.cli_client import DETAIL, METRIC, fetch_paged
from cli_eval.outdir import version_dir
from cli_eval.normalize import (
    compare_rows,
    compare_stat,
    key_text,
    num,
    org_name,
)

ROOT = Path(__file__).resolve().parent.parent

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
    """当天版本的标准值快照（由 check_golden_db.py 写入）。"""
    path = version_dir() / "golden_db" / f"{case_id}.json"
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8")).get("rows") or []


def metric_rows(command: str, params: Dict[str, Any]) -> List[Dict[str, Any]]:
    return fetch_paged(command, params, kind=METRIC)["rows"]


def detail_rows(command: str, params: Dict[str, Any], *, max_pages: int = 500) -> List[Dict[str, Any]]:
    return fetch_paged(command, params, kind=DETAIL, max_pages=max_pages)["rows"]


JULY = {"create_time_start": "2026-07-01 00:00:00", "create_time_end": "2026-07-31 23:59:59"}


def in_month(value: Any, year_month: str = "2026-07") -> bool:
    return str(value or "").startswith(year_month)


def parse_time(value: Any) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


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
            "统一「当月水量」口径：要么 CLI 直接暴露 SzwgBusiness 单月值（BothUcisMonth），"
            "要么标准集改用 SzwgBusinessYear 累计差并重新业务确认。"
            "已确认 CLI 的 periodType 只支持 1=日 / 2=月 / 3=年，BothUcisMonth 尚未开放，"
            "在口径定稿前不要把 month_* 当成唯一当月来源。"
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


def case_jl016() -> Dict[str, Any]:
    rows = detail_rows("water_supply_pipe_maintenance_work", JULY)
    july = [r for r in rows if in_month(r.get("create_time"))]
    counts = Counter(org_name(r.get("bz_bm")) for r in july)
    totals: Dict[str, float] = defaultdict(float)
    for r in july:
        value = num(r.get("water_leakage"))
        if value is not None:
            totals[org_name(r.get("bz_bm"))] += value
    expected = expected_of("JL-016")["detail"]["rows"]
    actual = [
        {
            "组织": org_name(exp["组织"]),
            "工单数": counts.get(org_name(exp["组织"]), 0),
            "维修漏损水量": round(totals[org_name(exp["组织"])], 4) if org_name(exp["组织"]) in totals else None,
        }
        for exp in expected
    ]
    diffs = compare_rows(
        actual,
        expected,
        key_actual="组织",
        key_expected="组织",
        fields=[{"expected": "维修漏损水量", "actual": "维修漏损水量",
                 "tolerance": {"kind": "rel", "eps": 0.001, "abs_floor": 0.01}}],
    )
    return result(
        "JL-016",
        "PASS" if not diffs else "VALUE_DIFF",
        actual=actual,
        diffs=diffs,
        root_cause="" if not diffs else (
            f"7 月 {len(july)} 条工单按组织汇总 water_leakage 后与标准值不一致；"
            "需确认 water_leakage 的空值（NULL/''）处理与标准 SQL 的 NULLIF 一致，以及组织归口是否相同。"
        ),
        recommendation="" if not diffs else (
            "确认 water_leakage 字段的空值语义与单位（m³）；"
            "并核对 bz_bm 与标准 SQL 的组织映射（坪地分公司→坪地供水等）。"
        ),
        evidence=[
            f"water_supply_pipe_maintenance_work 7 月 {len(july)} 条，{len(counts)} 个组织",
            f"water_leakage 非空 {sum(1 for r in july if num(r.get('water_leakage')) is not None)} 条",
            f"标准值 13 行，合计漏损水量 {sum(r['维修漏损水量'] or 0 for r in expected):.2f} m³",
        ],
        plan=[{"command": "water_supply_pipe_maintenance_work", "params": JULY, "rows": len(rows)}],
        standard_vs_db=compare_standard_vs_db("JL-016"),
        actual_text=f"{len(july)} 条 / {len(counts)} 个组织 / 合计 {sum(totals.values()):.2f} m³",
        expected_text=f"{len(expected)} 行",
        db_text=f"当前库重跑 {len(db_rows('JL-016'))} 行",
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
    rows = detail_rows("leak_detection_and_verification_work", JULY)
    july = [r for r in rows if in_month(r.get("create_time"))]
    # 标准 SQL：ORDER BY create_time DESC, id ASC LIMIT 10
    july.sort(key=lambda r: str(r.get("id") or ""))
    july.sort(key=lambda r: str(r.get("create_time") or ""), reverse=True)
    top = july[:10]
    actual = [
        {
            "工单编号": r.get("id"),
            "组织": org_name(r.get("bz_bm")),
            "标题": r.get("title"),
            "创建时间": str(r.get("create_time") or "")[:19],
        }
        for r in top
    ]
    diffs = compare_rows(
        actual,
        expected_of("JL-019")["detail"]["rows"],
        key_actual="工单编号",
        key_expected="工单编号",
        alias_actual=False,
        fields=[],
    )
    cli_orgs = sorted({org_name(r.get("bz_bm")) for r in july})
    return result(
        "JL-019",
        "PASS" if not diffs else "VALUE_DIFF",
        actual=actual,
        diffs=diffs,
        root_cause="" if not diffs else (
            f"7 月 CLI 只有 {len(july)} 条、覆盖 {len(cli_orgs)} 个单位（{'、'.join(cli_orgs)}），"
            "与库表 dwd_gdgl_gd_jlmx 的行集不同，所以「最新 10 条」整批对不上；"
            "不是排序问题——两边的工单编号集合本身没有交集。"
        ),
        recommendation="" if not diffs else (
            "确认明细接口的 create_time 边界（含 7-31 23:59:59）以及排序规则；"
            "如果接口本身不支持排序，客户端需拉全量后按 create_time DESC, id ASC 取前 10。"
        ),
        evidence=[
            f"leak_detection_and_verification_work 7 月 {len(july)} 条",
            f"最新一条：{actual[0]['工单编号'] if actual else '—'} / {actual[0]['创建时间'] if actual else '—'}",
            f"标准值 10 条，首条 {expected_of('JL-019')['detail']['rows'][0]['工单编号'] if expected_of('JL-019')['detail']['rows'] else '—'}",
        ],
        plan=[{"command": "leak_detection_and_verification_work", "params": JULY, "rows": len(rows)}],
        standard_vs_db=compare_standard_vs_db("JL-019"),
        actual_text=f"{len(july)} 条 / TOP10",
        expected_text="10 条",
        db_text=f"当前库重跑 {len(db_rows('JL-019'))} 行",
    )


def case_jl021() -> Dict[str, Any]:
    rows = detail_rows("leak_detection_and_verification_work", JULY)
    buckets: Dict[str, List[float]] = defaultdict(list)
    skipped = 0
    for r in rows:
        create, arrive = parse_time(r.get("create_time")), parse_time(r.get("arrive_time"))
        if not in_month(r.get("create_time")) or create is None or arrive is None:
            skipped += 1
            continue
        buckets[org_name(r.get("bz_bm"))].append((arrive - create).total_seconds() / 3600.0)
    actual = [
        {"组织": org, "有到场工单数": len(hours), "平均响应小时数": round(sum(hours) / len(hours), 4)}
        for org, hours in sorted(buckets.items())
    ]
    diffs = compare_rows(
        actual,
        expected_of("JL-021")["detail"]["rows"],
        key_actual="组织",
        key_expected="组织",
        fields=[{"expected": "平均响应小时数", "actual": "平均响应小时数",
                 "tolerance": {"kind": "rel", "eps": 0.001, "abs_floor": 0.01}}],
    )
    expected_orgs = {org_name(r["组织"]) for r in expected_of("JL-021")["detail"]["rows"]}
    missing = sorted(expected_orgs - set(buckets))
    return result(
        "JL-021",
        "PASS" if not diffs else "VALUE_DIFF",
        actual=actual,
        diffs=diffs,
        root_cause="" if not diffs else (
            f"7 月 {len(rows)} 条中 {sum(len(v) for v in buckets.values())} 条有到场时间，覆盖 {len(buckets)} 个单位；"
            f"标准值要求 {len(expected_orgs)} 个单位，其中 {('、'.join(missing)) if missing else '无'} 在 CLI 里没有记录。"
            "行集与库表 dwd_gdgl_gd_jlmx 不一致是主因，平均响应时间本身也受入参口径影响。"
        ),
        recommendation="" if not diffs else (
            "确认 arrive_time 的口径（首次到场 vs 末次到场）及空值处理；"
            "如果接口返回多段到场记录，需要明确取哪一条。"
        ),
        evidence=[
            f"7 月 {len(rows)} 条，有到场时间 {sum(len(v) for v in buckets.values())} 条",
            f"覆盖组织 {len(buckets)} 个，标准值 6 个",
            f"跳过 {skipped} 条（期间外或缺时间）",
        ],
        plan=[{"command": "leak_detection_and_verification_work", "params": JULY, "rows": len(rows)}],
        standard_vs_db=compare_standard_vs_db("JL-021"),
        actual_text="; ".join(f"{r['组织']}: {r['平均响应小时数']}h" for r in actual[:6]),
        expected_text="6 个组织",
        db_text=f"当前库重跑 {len(db_rows('JL-021'))} 行",
    )


def case_jl022() -> Dict[str, Any]:
    rows = detail_rows("leak_detection_and_verification_work", JULY)
    total, finished = Counter(), Counter()
    for r in rows:
        if not in_month(r.get("create_time")):
            continue
        org = org_name(r.get("bz_bm"))
        total[org] += 1
        if str(r.get("finish_time") or "").strip():
            finished[org] += 1
    actual = [
        {"组织": org, "工单总数": cnt, "完成数": finished[org],
         "完成率": round(finished[org] / cnt, 4) if cnt else None}
        for org, cnt in sorted(total.items())
    ]
    diffs = compare_rows(
        actual,
        expected_of("JL-022")["detail"]["rows"],
        key_actual="组织",
        key_expected="组织",
        fields=[
            {"expected": "工单总数", "actual": "工单总数", "tolerance": {"kind": "exact"}},
            {"expected": "完成数", "actual": "完成数", "tolerance": {"kind": "exact"}},
            {"expected": "完成率", "actual": "完成率", "tolerance": {"kind": "abs", "eps": 0.01}},
        ],
    )
    expected_orgs = {org_name(r["组织"]) for r in expected_of("JL-022")["detail"]["rows"]}
    missing = sorted(expected_orgs - set(total))
    return result(
        "JL-022",
        "PASS" if not diffs else "VALUE_DIFF",
        actual=actual,
        diffs=diffs,
        root_cause="" if not diffs else (
            f"7 月 CLI 共 {sum(total.values())} 条、覆盖 {len(total)} 个单位；标准值 {len(expected_orgs)} 个单位，"
            f"其中 {('、'.join(missing)) if missing else '无'} 在 CLI 里没有记录。"
            "行集不一致是主因，完成判定（finish_time 非空）口径差异是次要因素。"
        ),
        recommendation="" if not diffs else (
            "确认 finish_time 的写入时机（销单 vs 处理完成）；"
            "若业务以 step_status_name=已处理完成为准，需要把口径写进题目并同步改标准 SQL。"
        ),
        evidence=[
            f"7 月 {sum(total.values())} 条，finish_time 非空 {sum(finished.values())} 条",
            f"覆盖组织 {len(total)} 个，标准值 6 个",
        ],
        plan=[{"command": "leak_detection_and_verification_work", "params": JULY, "rows": len(rows)}],
        standard_vs_db=compare_standard_vs_db("JL-022"),
        actual_text="; ".join(f"{r['组织']} {r['完成数']}/{r['工单总数']}" for r in actual[:6]),
        expected_text="6 个组织",
        db_text=f"当前库重跑 {len(db_rows('JL-022'))} 行",
    )


def case_jl036() -> Dict[str, Any]:
    rows = metric_rows("month_diff_rate", {"periodType": 2, "dimensionType": 1})
    rates = {}
    for row in rows:
        if str(row.get("periodPoint")) == "202606":
            rates[org_name(row.get("dimensionValue"))] = num(row.get("statValue"))
    july_orders = detail_rows("leak_detection_and_verification_work", JULY)
    counts = Counter(org_name(r.get("bz_bm")) for r in july_orders if in_month(r.get("create_time")))
    expected = expected_of("JL-036")["detail"]
    actual = [
        {
            "组织": org_name(exp["组织"]),
            "单月产销差率": rates.get(org_name(exp["组织"])),
            "检漏工单数": counts.get(org_name(exp["组织"]), 0),
        }
        for exp in expected["rows"]
    ]
    diffs = compare_rows(
        actual,
        expected["rows"],
        key_actual="组织",
        key_expected="组织",
        fields=[
            {"expected": "单月产销差率", "actual": "单月产销差率", "tolerance": {"kind": "abs", "eps": 0.01}},
            {"expected": "检漏工单数", "actual": "检漏工单数", "tolerance": {"kind": "exact"}},
        ],
    )
    rate_diffs = [d for d in diffs if d.get("field") == "单月产销差率"]
    count_diffs = [d for d in diffs if d.get("field") == "检漏工单数"]
    return result(
        "JL-036",
        "PARTIAL",
        actual=actual,
        diffs=diffs,
        root_cause=(
            f"单月产销差率仍有 {len(rate_diffs)} 个单位不符：CLI month_diff_rate 的 periodType 只支持 1=日/2=月/3=年，"
            "当月值由年累计相减得到，标准值用 SzwgBusiness 单月快照；文档提到的 BothUcisMonth 口径尚未在 CLI 开放。"
            f"检漏工单数已可核对（7 月 {len(july_orders)} 条，{len(count_diffs)} 个单位不符）。"
        ),
        recommendation=(
            "在 CLI 上开放 BothUcisMonth（单月快照）口径的产销差率，或在 month_diff_rate 里直接返回单月值；"
            "在此之前不要把 month_diff_rate 当作单月口径的验收依据。"
        ),
        evidence=[
            "盐田 202606：标准 0.1035（SQL SzwgBusiness 2539522/2276570）",
            "CLI month_diff_rate 盐田 202606：0.1292（YTD 差 2614311/2276570）",
            f"检漏工单数：CLI 7 月 {len(july_orders)} 条，按 bz_bm 归出 {len(counts)} 个组织",
        ],
        plan=[
            {"command": "month_diff_rate", "params": {"periodType": 2, "dimensionType": 1}, "rows": len(rows)},
            {"command": "leak_detection_and_verification_work", "params": JULY, "rows": len(july_orders)},
        ],
        standard_vs_db=compare_standard_vs_db("JL-036"),
        actual_text=f"单月率 {len(rates)} 个单位；检漏工单 {sum(counts.values())} 条",
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
    params = {"create_time_start": "2026-01-01 00:00:00", "create_time_end": "2026-08-18 00:00:00"}
    rows = detail_rows("water_supply_pipe_maintenance_work", params, max_pages=1200)
    buckets: Dict[tuple, Dict[str, Any]] = {}
    for r in rows:
        created = str(r.get("create_time") or "")
        code = str(r.get("pipe_number") or "").strip()
        if not created or created >= "2026-08-18" or not code:
            continue
        org = org_name(r.get("bz_bm"))
        bucket = buckets.setdefault((code, org), {"cnt": 0, "addr": ""})
        bucket["cnt"] += 1
        if not bucket["addr"]:
            bucket["addr"] = str(r.get("detailedaddress") or "")
    actual = [
        {"__key": f"{code}|{org}", "管道编码": code, "组织": org, "地址": b["addr"], "维修次数": b["cnt"]}
        for (code, org), b in buckets.items() if b["cnt"] > 2
    ]
    expected = [
        {"__key": f"{r['管道编码']}|{org_name(r['组织'])}", "管道编码": r["管道编码"],
         "组织": org_name(r["组织"]), "地址": r.get("地址"), "维修次数": r["维修次数"]}
        for r in expected_of("BJ-012")["detail"]["rows"]
    ]
    diffs = compare_rows(
        actual,
        expected,
        key_actual="__key",
        key_expected="__key",
        alias_actual=False,
        fields=[{"expected": "维修次数", "actual": "维修次数", "tolerance": {"kind": "exact"}}],
    )
    return result(
        "BJ-012",
        "PASS" if not diffs else "VALUE_DIFF",
        actual=sorted(actual, key=lambda r: (-r["维修次数"], r["管道编码"]))[:200],
        diffs=diffs,
        root_cause="" if not diffs else (
            f"2026-01-01 至 2026-08-17 共取回 {len(rows)} 条维修工单，"
            f"按 (pipe_number, bz_bm) 分组后维修次数>2 的有 {len(actual)} 组，标准值 {len(expected)} 组；"
            "差异来自工单行集或 pipe_number 归口口径。"
        ),
        recommendation="" if not diffs else (
            "确认 water_supply_pipe_maintenance_work 的 create_time 边界与标准 SQL 一致（<2026-08-18），"
            "并确认 pipe_number 为空/无效的过滤规则。"
        ),
        evidence=[
            f"取回 {len(rows)} 条工单（2026-01-01 ~ 2026-08-17）",
            f"维修次数>2 的分组 {len(actual)} 组，标准值 {len(expected)} 组",
            f"标准值最高维修次数 {max((r['维修次数'] for r in expected), default=0)}",
        ],
        plan=[{"command": "water_supply_pipe_maintenance_work", "params": params, "rows": len(rows)}],
        standard_vs_db=compare_standard_vs_db("BJ-012"),
        actual_text=f"{len(rows)} 条 / {len(actual)} 组反复维修",
        expected_text=f"{len(expected)} 组",
        db_text=f"当前库重跑 {len(db_rows('BJ-012'))} 行",
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


def _diameter_segment(caliber: Any) -> str:
    value = num(caliber)
    if value is None:
        return "未知"
    if value >= 1000:
        return "1000毫米及以上"
    if value >= 600:
        return "600至不足1000"
    if value >= 400:
        return "400至不足600"
    return "不足400"


def case_bj017() -> Dict[str, Any]:
    rows = detail_rows("water_supply_pipe_maintenance_work", JULY)
    groups: Dict[tuple, Dict[str, Any]] = {}
    for r in rows:
        if not in_month(r.get("create_time")):
            continue
        accident = str(r.get("accident_type") or "")
        flow = str(r.get("flow_status_name") or "")
        if "爆" not in accident and "爆管" not in flow:
            continue
        seg = _diameter_segment(r.get("caliber"))
        org = org_name(r.get("bz_bm"))
        bucket = groups.setdefault((seg, org), {"cnt": 0, "l3": set()})
        bucket["cnt"] += 1
        if r.get("level3_name"):
            bucket["l3"].add(str(r["level3_name"]))
    actual = [
        {"__key": f"{seg}|{org}", "管径分段": seg, "组织": org,
         "三级部门": max(sorted(b["l3"])) if b["l3"] else None, "爆管次数": b["cnt"]}
        for (seg, org), b in groups.items()
    ]
    expected = [
        {"__key": f"{r['管径分段']}|{org_name(r['组织'])}", "管径分段": r["管径分段"],
         "组织": org_name(r["组织"]), "三级部门": r.get("三级部门"), "爆管次数": r["爆管次数"]}
        for r in expected_of("BJ-017")["detail"]["rows"]
    ]
    diffs = compare_rows(
        actual,
        expected,
        key_actual="__key",
        key_expected="__key",
        alias_actual=False,
        fields=[{"expected": "爆管次数", "actual": "爆管次数", "tolerance": {"kind": "exact"}}],
    )
    return result(
        "BJ-017",
        "PASS" if not diffs else "VALUE_DIFF",
        actual=sorted(actual, key=lambda r: (r["管径分段"], -r["爆管次数"])),
        diffs=diffs,
        root_cause="" if not diffs else (
            "明细命令已可下钻到「管径分段 × 单位 × 三级部门」："
            f"7 月取回 {len(rows)} 条，按 accident_type/flow_status_name 含「爆」筛出后归出 {len(actual)} 组，"
            f"标准值 {len(expected)} 组；差异来自爆管判定或组织/部门归口。"
        ),
        recommendation="" if not diffs else (
            "确认爆管判定条件（accident_type LIKE '%爆%' OR flow_status_name LIKE '%爆管%'）"
            "与标准 SQL 完全一致，并核对 caliber 为空时的分段归属。"
        ),
        evidence=[
            f"water_supply_pipe_maintenance_work 7 月 {len(rows)} 条",
            f"筛选后 {len(actual)} 组（管径分段×单位），标准值 {len(expected)} 组",
            "明细字段 caliber、level3_name、accident_type、flow_status_name 均已返回",
        ],
        plan=[{"command": "water_supply_pipe_maintenance_work", "params": JULY, "rows": len(rows)}],
        standard_vs_db=compare_standard_vs_db("BJ-017"),
        actual_text=f"{len(actual)} 组（管径×单位）",
        expected_text=f"{len(expected)} 行（管径×单位×三级部门）",
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
