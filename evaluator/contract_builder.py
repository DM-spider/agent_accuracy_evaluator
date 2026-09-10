# -*- coding: utf-8 -*-
"""从黄金集生成 CaseContract 草稿，并把固定时间值迁移为命名参数。"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple

from evaluator.models import CaseContract, CoveragePolicy, MeasureSpec, Tolerance
from evaluator.run_context import normalize_sql, preview_sql, replace_literal

AS_RE = re.compile(r'\bAS\s+"([^"]+)"|\bAS\s+([A-Za-z_\u4e00-\u9fff][\w\u4e00-\u9fff%().]*)', re.I)
TIME_LIT_RE = re.compile(r"\b(20\d{2}-\d{2}-\d{2}|20\d{4})\b")
IN_LIST_RE = re.compile(r"IN\s*\(([^)]+)\)", re.I)

DIM_ALIASES = {
    "organization": ["组织", "二级部门", "bz_bm", "dwmc", "分公司", "单位"],
    "period": ["月份(期数)", "月份", "期数", "ym", "period", "期间"],
    "dma": ["DMA编码", "dmaid", "DMA"],
    "dma_name": ["DMA名称", "dmaname"],
    "work_order_id": ["工单编号", "id"],
    "meter_code": ["表计编码", "metercode"],
}

KEY_TO_DIM = {
    "月份(期数)": "period",
    "二级部门": "organization",
    "组织": "organization",
    "DMA编码": "dma",
    "DMA名称": "dma_name",
    "用水类别": "water_category",
    "工单编号": "work_order_id",
    "标题": "title",
    "工单类型": "order_type",
    "流转状态": "flow_status",
    "表计编码": "meter_code",
    "设施类型": "facility_type",
    "设施编码": "facility_code",
    "预警类型": "alarm_type",
    "区间": "bucket",
    "期间": "period_label",
    "榜单类型": "list_type",
    "变化方向": "direction",
    "差值": "date",
    "状态": "status",
}

COLUMN_CN = {
    "ym": "月份(期数)",
    "supply": "供水量",
    "sales": "售水量",
    "diff_vol": "产销差量",
    "cum_rate": "累计产销差率",
    "rate": "产销差率",
    "actual_rate": "实际产销差率",
    "target_rate": "目标产销差率",
    "completion": "完成率",
    "weight_pct": "影响权重",
    "gap": "差距",
    "avg_rate": "月均漏损率",
    "loss_vol": "漏损量",
    "unit_loss": "单位管长漏损量",
    "pipe_len_km": "管长",
    "dmaid": "DMA编码",
    "dmaname": "DMA名称",
    "bz_bm": "二级部门",
    "org": "二级部门",
    "dwmc": "二级部门",
    "warn_cnt": "预警次数",
    "dev_rate": "总分表偏差率",
    "dev_vol": "总分表偏差量",
    "complete_rate": "完整率",
    "burst_cnt": "爆管次数",
    "jlxl": "检漏效率",
}


def _norm_label(text: str) -> str:
    return re.sub(r"[（(][^）)]*[）)]$", "", str(text).strip()).strip()


def infer_unit(key: str, unit_hint: Optional[str] = None) -> str:
    if unit_hint:
        return str(unit_hint)
    blob = key
    if "pp" in blob or "百分点" in blob:
        return "pp"
    if "%" in blob or blob.endswith("率") or "率(" in blob:
        return "%"
    if "m³" in blob or "水量" in blob or "漏损量" in blob:
        return "m³"
    if "km" in blob:
        return "km"
    if any(token in blob for token in ("数", "次", "天数", "排名")):
        return "count"
    return ""


def infer_scale(unit: str, key: str) -> str:
    if unit == "pp" or "pp" in key:
        return "pp"
    if unit == "%" or "率" in key:
        return "percent"
    if unit == "count" or any(token in key for token in ("数", "次", "天数")):
        return "count"
    if unit == "m³":
        return "volume"
    return "volume"


def infer_tolerance(scale: str) -> Tolerance:
    # ratio01 存 0-1 比值，容差定义在 ×100 后的业务空间（%/pp），与 percent/pp 一致
    if scale in {"percent", "pp", "ratio01"}:
        return Tolerance(kind="abs", eps=0.01)
    if scale == "count":
        return Tolerance(kind="exact", eps=0.0)
    return Tolerance(kind="rel", eps=0.001, abs_floor=0.01)


def parse_select_aliases(sql: str) -> List[str]:
    aliases = []
    for match in AS_RE.finditer(sql or ""):
        aliases.append(match.group(1) or match.group(2))
    return aliases


def infer_resolver(case: Dict[str, Any]) -> str:
    params = case.get("params") or {}
    ts = str(case.get("time_scope_raw") or "")
    sql = case.get("sql") or ""
    if "yms" in params:
        return "last_6_months"
    if "ym_last" in params:
        return "yoy_mom"
    if "qc" in params:
        return "quarter_vs_prev"
    months = params.get("months")
    if isinstance(months, list) and len(months) >= 10:
        return "last_12_months"
    if isinstance(months, list) and len(months) == 3:
        return "last_3_months"
    if "week_start" in params:
        return "today_week_month"
    if "cutoff" in params:
        return "month_to_cutoff"
    if "ym_ytd" in params:
        return "ytd_and_last_month"
    if "ym_cur" in params or "ym_prev" in params:
        return "current_prev_month"
    if params.get("start") and params.get("end"):
        return "date_range"
    if "date_start" in params and "date_end" in params:
        if "近7天" in ts or "昨日" in ts:
            return "last_7_days"
        if "近30天" in ts:
            return "last_30_days"
        return "date_range"
    if "date" in params:
        return "as_of_date"
    if params.get("month") and re.match(r"^\d{4}-\d{2}$", str(params.get("month"))):
        return "month_range"
    if "ym" in params:
        if "1-5" in ts:
            return "jan_may"
        if "上月" in ts and "本月" not in ts:
            return "previous_month"
        if TIME_LIT_RE.search(sql) and re.search(r"\d{4}-\d{2}-\d{2}", sql):
            return "month_range"
        if "累计" in ts or "今年" in ts or "本年" in ts:
            return "year_month"
        return "single_month"
    if TIME_LIT_RE.search(sql):
        return "unresolved"
    return "none"


def _month_bounds(ym: int) -> Tuple[str, str, str]:
    year, month = ym // 100, ym % 100
    start = f"{year:04d}-{month:02d}-01"
    if month == 12:
        nxt = f"{year + 1:04d}-01-01"
        end = f"{year:04d}-12-31"
    else:
        nxt = f"{year:04d}-{month + 1:02d}-01"
        if month in (1, 3, 5, 7, 8, 10, 12):
            end = f"{year:04d}-{month:02d}-31"
        elif month == 2:
            end = f"{year:04d}-02-29" if year % 4 == 0 else f"{year:04d}-02-28"
        else:
            end = f"{year:04d}-{month:02d}-30"
    return start, end, nxt


def _add(reps: List[Tuple[str, str]], old: Any, placeholder: str) -> None:
    if old is None or old == "":
        return
    text = str(old)
    if text.startswith("20") or re.fullmatch(r"\d{6,8}", text):
        reps.append((text, placeholder))


def replacements_for_case(case: Dict[str, Any], resolver: str) -> Tuple[List[Tuple[str, str]], Dict[str, Any]]:
    params = case.get("params") or {}
    sql = case.get("sql") or ""
    reps: List[Tuple[str, str]] = []
    historical: Dict[str, Any] = {}

    def bind(name: str, value: Any, placeholder: Optional[str] = None) -> None:
        if value is None:
            return
        token = placeholder or f":{name}"
        if any(old == str(value) for old, _new in reps):
            return
        if name in historical:
            return
        historical[name] = value if not isinstance(value, str) or not re.fullmatch(r"\d{6}", value) else int(value)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if float(value).is_integer() and name.startswith("period"):
                historical[name] = int(value)
        _add(reps, value, token)

    if resolver == "last_6_months":
        yms = params.get("yms") or []
        for idx, ym in enumerate(yms):
            bind(f"period_{idx}", ym)
        if yms:
            start, _, _ = _month_bounds(int(yms[0]))
            _, _, nxt = _month_bounds(int(yms[-1]))
            bind("start_date", start)
            bind("end_date", nxt)
        joined = ",".join(str(v) for v in yms)
        spaced = ", ".join(str(v) for v in yms)
        named = ",".join(f":period_{i}" for i in range(len(yms)))
        for variant in (f"({joined})", f"({spaced})"):
            if variant in sql.replace(" ", "") or variant in sql:
                reps.append((f"({joined})", f"({named})"))
                reps.append((f"({spaced})", f"({named})"))
    elif resolver == "yoy_mom":
        bind("period", params.get("ym"))
        bind("period_yoy", params.get("ym_last"))
        bind("period_prev", params.get("prev"))
        bind("year_start", "2026-01-01")
    elif resolver == "quarter_vs_prev":
        qc = params.get("qc") or []
        qp = params.get("qp") or []
        if len(qc) == 2:
            bind("start_date", qc[0])
            bind("end_date", qc[1])
        if len(qp) == 2:
            bind("prev_quarter_start", qp[0])
            bind("prev_quarter_end", qp[1])
    elif resolver == "last_3_months":
        months = params.get("months") or []
        for idx, ym in enumerate(months):
            year, month = int(str(ym)[:4]), int(str(ym)[4:])
            start = f"{year:04d}-{month:02d}-01"
            if month == 12:
                end = f"{year:04d}-12-31"
            else:
                end_month = month + 1
                end = f"{year:04d}-{end_month:02d}-01"
            # 原 SQL 用月末日或下月 1 日，两者都替换
            bind(f"m{idx}_start", start)
            bind(f"m{idx}_end", f"{year:04d}-{month:02d}-31" if month in (1, 3, 5, 7, 8, 10, 12) else f"{year:04d}-{month:02d}-30")
            _add(reps, start, f":m{idx}_start")
    elif resolver == "last_12_months":
        bind("y12_start", params.get("date_start") or "2025-09-01")
        bind("y12_end", params.get("date_end") or "2026-08-31")
        if "dmaid" in params:
            pass
        months = params.get("months") or []
        for idx, ym in enumerate(months):
            bind(f"period_{idx}", ym)
    elif resolver == "today_week_month":
        bind("today", params.get("today"))
        bind("week_start", params.get("week_start"))
        lits = [x for x in TIME_LIT_RE.findall(sql) if "-" in x]
        month_dates = [d for d in lits if d not in {params.get("today"), params.get("week_start")}]
        if len(month_dates) >= 2:
            bind("start_date", month_dates[0])
            bind("end_date", month_dates[1])
        elif params.get("today"):
            # 本月完整区间常与今日同时出现
            pass
    elif resolver == "month_to_cutoff":
        bind("as_of_date", params.get("cutoff"))
        lits = [x for x in TIME_LIT_RE.findall(sql) if "-" in x and x != params.get("cutoff")]
        if len(lits) >= 2:
            bind("start_date", lits[0])
            bind("end_date", lits[1])
        elif len(lits) == 1:
            bind("start_date", lits[0])
    elif resolver == "ytd_and_last_month":
        bind("period", params.get("ym_ytd"))
        bind("period_prev", params.get("lm") if isinstance(params.get("lm"), int) else 202606)
        lits = [x for x in TIME_LIT_RE.findall(sql) if "-" not in x]
        if "202601" in lits:
            bind("year_start_ym", 202601)
            _add(reps, "202601", ":year_start_ym")
        for lit in lits:
            if lit not in {"202601"} and lit != str(params.get("ym_ytd")):
                bind("period_prev", int(lit))
    elif resolver == "current_prev_month":
        bind("period", params.get("ym_cur") or params.get("ym"))
        bind("period_prev", params.get("ym_prev"))
        if params.get("ym_cur") or params.get("ym"):
            start, end, nxt = _month_bounds(int(params.get("ym_cur") or params.get("ym")))
            bind("start_date", start)
            bind("end_date", end)
            bind("next_month_start", nxt)
        if params.get("ym_prev"):
            pstart, pend, pnxt = _month_bounds(int(params["ym_prev"]))
            bind("prev_month_start", pstart)
            bind("prev_month_end", pend)
            bind("prev_next_start", pnxt)
        lits = [x for x in TIME_LIT_RE.findall(sql) if "-" in x]
        if len(lits) >= 3:
            bind("start_date", lits[0])
            bind("end_date", lits[1])
            bind("next_month_start", lits[2])
        elif len(lits) == 2:
            bind("start_date", lits[0])
            bind("end_date", lits[1])
    elif resolver in {"last_7_days", "last_30_days", "date_range"}:
        bind("start_date", params.get("date_start") or params.get("start"))
        bind("end_date", params.get("date_end") or params.get("end"))
    elif resolver == "as_of_date":
        bind("as_of_date", params.get("date"))
    elif resolver == "jan_may":
        bind("period", params.get("ym"))
    elif resolver in {"single_month", "previous_month", "year_month"}:
        bind("period", params.get("ym"))
        if isinstance(params.get("ym"), int):
            start, end, nxt = _month_bounds(int(params["ym"]))
            bind("start_date", start)
            bind("end_date", end)
            bind("next_month_start", nxt)
        lits = TIME_LIT_RE.findall(sql)
        dates = [x for x in lits if "-" in x]
        if len(dates) >= 2:
            bind("start_date", dates[0])
            bind("end_date", dates[1])
        elif len(dates) == 1:
            bind("start_date", dates[0])
    elif resolver == "month_range":
        ym = params.get("ym")
        month = params.get("month")
        if ym is None and month:
            ym = int(str(month).replace("-", ""))
        bind("period", ym)
        if isinstance(ym, int) or (isinstance(ym, str) and str(ym).isdigit()):
            start, end, nxt = _month_bounds(int(ym))
            bind("start_date", start)
            bind("end_date", end)
            bind("next_month_start", nxt)
        if params.get("ym_prev"):
            pstart, pend, _ = _month_bounds(int(params["ym_prev"]))
            bind("prev_month_start", pstart)
            bind("prev_month_end", pend)
            bind("period_prev", params["ym_prev"])
        lits = [x for x in TIME_LIT_RE.findall(sql) if "-" in x]
        if len(lits) >= 2:
            bind("start_date", lits[0])
            bind("end_date", lits[1])
            if len(lits) >= 3:
                bind("next_month_start", lits[2])
        elif len(lits) == 1:
            bind("start_date", lits[0])

    mapped_old = {old for old, _new in reps}
    for lit in remaining_time_literals(sql):
        if lit in mapped_old:
            continue
        if lit.endswith("-01-01"):
            bind("year_start", lit)
        elif re.fullmatch(r"\d{6}", lit):
            bind("period", int(lit))
        else:
            bind("as_of_date", lit)
        mapped_old.add(lit)

    # 去重但保序，长字面量优先
    uniq: List[Tuple[str, str]] = []
    seen = set()
    for old, new in sorted(reps, key=lambda x: len(x[0]), reverse=True):
        key = (old, new)
        if key in seen or not old:
            continue
        seen.add(key)
        uniq.append(key)
    return uniq, historical


def apply_replacements(sql: str, replacements: List[Tuple[str, str]]) -> str:
    out = sql
    for old, new in replacements:
        if old.startswith("(") and old.endswith(")"):
            out = out.replace(old, new)
            compact = old.replace(" ", "")
            if compact != old:
                out = out.replace(compact, new)
        else:
            out = replace_literal(out, old, new)
    return out


def remaining_time_literals(sql: str) -> List[str]:
    return TIME_LIT_RE.findall(sql or "")


def coverage_for(case: Dict[str, Any]) -> CoveragePolicy:
    exp = case.get("expected") or {}
    if case.get("result_type") == "stat" and "detail" not in exp:
        return CoveragePolicy(mode="all_fields", minimum=1.0)
    detail = exp.get("detail") or {}
    total = int(detail.get("total_rows") or len(detail.get("rows") or []))
    if total > 10:
        return CoveragePolicy(mode="top_n", minimum=0.8, n=10)
    if total > 0:
        return CoveragePolicy(mode="all_rows", minimum=0.8)
    return CoveragePolicy(mode="all_fields", minimum=1.0)


def _measure_from_field(field: Dict[str, Any], sql_aliases: List[str]) -> Tuple[str, MeasureSpec]:
    original = str(field.get("key") or field.get("label") or "value")
    key = _norm_label(original)
    unit = infer_unit(original, field.get("unit"))
    # 黄金集字段声明了存储形态（如 v3 的 ratio01）时优先采用，"率"→percent 的推断仅作兜底
    scale = str(field.get("value_scale") or "") or infer_scale(unit, key)
    tol = field.get("tolerance") or {}
    tolerance = Tolerance(
        kind=tol.get("kind") or infer_tolerance(scale).kind,
        eps=float(tol.get("eps") if tol.get("eps") is not None else infer_tolerance(scale).eps),
        abs_floor=float(tol.get("abs_floor") or (0.01 if scale == "volume" else 0.0)),
    )
    sql_column = ""
    label_n = _norm_label(key)
    for alias in sql_aliases:
        if _norm_label(alias) == label_n or COLUMN_CN.get(alias) == label_n or alias == key:
            sql_column = alias
            break
    if not sql_column and len(sql_aliases) == 1:
        sql_column = sql_aliases[0]
    aliases = list(dict.fromkeys([key, original, _norm_label(original), str(field.get("label") or key)]))
    return key, MeasureSpec(
        label=str(field.get("label") or key),
        aliases=[a for a in aliases if a],
        unit=unit,
        tolerance=tolerance,
        sql_column=sql_column,
        value_scale=scale,
        required=True,
    )


def build_measures(case: Dict[str, Any]) -> Dict[str, MeasureSpec]:
    exp = case.get("expected") or {}
    sql_aliases = parse_select_aliases(case.get("sql") or "")
    measures: Dict[str, MeasureSpec] = {}
    for field in exp.get("fields") or []:
        key, spec = _measure_from_field(field, sql_aliases)
        measures[key] = spec
    for vf in (exp.get("detail") or {}).get("value_fields") or []:
        if not isinstance(vf, dict):
            vf = {"key": vf}
        key, spec = _measure_from_field(vf, sql_aliases)
        if key not in measures:
            measures[key] = spec
    # SQL 别名兜底：金标只声明了部分指标时仍记录列名
    if not measures:
        for alias in sql_aliases:
            label = COLUMN_CN.get(alias, alias)
            if _norm_label(label) in {"备注", "标记", "标题"}:
                continue
            unit = infer_unit(label)
            scale = infer_scale(unit, label)
            measures[label] = MeasureSpec(
                label=label,
                aliases=[label, alias],
                unit=unit,
                tolerance=infer_tolerance(scale),
                sql_column=alias,
                value_scale=scale,
                required=False,
            )
    return measures


def build_dimensions(case: Dict[str, Any]) -> Tuple[List[str], List[str], Dict[str, List[str]]]:
    exp = case.get("expected") or {}
    key_fields = list((exp.get("detail") or {}).get("key_fields") or [])
    dims: List[str] = []
    columns: Dict[str, List[str]] = {}
    if case.get("result_type") == "stat" and not key_fields:
        dims = ["period"]
        return dims, [], columns
    for key in key_fields:
        dim = KEY_TO_DIM.get(key, _norm_label(key) or key)
        dims.append(dim)
        columns.setdefault(dim, [])
        for alias in [key, _norm_label(key), *DIM_ALIASES.get(dim, [])]:
            if alias and alias not in columns[dim]:
                columns[dim].append(alias)
    return dims, dims, columns


def build_contract(case: Dict[str, Any]) -> CaseContract:
    numeric = str(case.get("result_type") or "") in {"stat", "detail"}
    sql_source = (case.get("sql") or "").strip()
    resolver = infer_resolver(case) if numeric and sql_source else "none"
    replacements, _historical = replacements_for_case(case, resolver) if numeric else ([], {})
    template = apply_replacements(sql_source, replacements) if sql_source else ""
    leftover = remaining_time_literals(template) if numeric else []
    realtime_ready = bool(numeric and sql_source and resolver != "unresolved" and not leftover)
    review: List[str] = []
    if numeric and not sql_source:
        review.append("sql")
        realtime_ready = False
    if leftover:
        review.append("time_literals")
        realtime_ready = False
    if resolver == "unresolved":
        review.append("parameter_resolver")
    measures = build_measures(case) if numeric else {}
    dimensions, row_key, dim_cols = build_dimensions(case) if numeric else ([], [], {})
    not_scored = None
    if not numeric:
        not_scored = "NON_NUMERIC"
    elif not realtime_ready:
        not_scored = "SQL_NOT_REALTIME_READY"
    return CaseContract(
        case_id=case["case_id"],
        question=case.get("question") or "",
        result_type=str(case.get("result_type") or ""),
        numeric_evaluable=numeric,
        realtime_ready=realtime_ready,
        sql_template=template,
        sql_source=sql_source,
        parameter_resolver=resolver,
        dimensions=dimensions,
        row_key=row_key,
        dimension_columns=dim_cols,
        measures=measures,
        coverage_policy=coverage_for(case) if numeric else CoveragePolicy(),
        scene_big=case.get("scene_big") or "",
        category=case.get("category") or "",
        indicator_type=case.get("indicator_type") or "",
        caliber=case.get("caliber") or "",
        notes=case.get("notes") or "",
        roles=list(case.get("roles") or []),
        review_required=review,
        not_scored_reason=not_scored,
    )


def historical_params_for(case: Dict[str, Any], contract: CaseContract) -> Dict[str, Any]:
    _, historical = replacements_for_case(case, contract.parameter_resolver)
    return historical


def template_matches_source(case: Dict[str, Any], contract: CaseContract) -> bool:
    if not contract.sql_source:
        return True
    params = historical_params_for(case, contract)
    rendered = preview_sql(contract.sql_template, params)
    return normalize_sql(rendered) == normalize_sql(contract.sql_source)


def build_all_contracts(cases: List[Dict[str, Any]]) -> List[CaseContract]:
    return [build_contract(case) for case in cases]


def dump_contracts(contracts: List[CaseContract], path) -> None:
    payload = {
        "version": "v1",
        "contracts": [c.model_dump() for c in contracts],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
