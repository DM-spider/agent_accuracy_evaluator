"""Read-only table comparison for review; never modifies official scores."""
from collections import Counter
import re

from evaluator.aliases import is_org_dim, is_period_dim, normalize_org, organization_hierarchy
from evaluator.answer_context import primary_block
from evaluator.comparator import (
    DERIVED_TOTAL_NOTE,
    TOTAL_WORDS,
    compare_claims,
    coordinate_key,
    normalize_coordinate_value,
)
from evaluator.extractor import extract_claims, claims_from_sql_rows, table_header_mapping
from evaluator.models import MeasureSpec, NumericClaim
from evaluator.normalizer import tolerance_rule, value_scale_rule, values_close


DIMENSION_LABELS = {"period": "月份", "date": "日期", "organization": "组织"}
DIMENSION_RULES = {
    "period": "YYYY-MM、YYYY年M月、数值型月份统一为YYYYMM",
    "date": "日期及带零小数尾数的日期统一为YYYYMMDD",
    "organization": "按组织别名表统一为标准组织名称",
}
DIMENSION_CANONICAL = {"period": "YYYYMM", "date": "YYYYMMDD", "organization": "标准组织名称"}


def _item_spec(contract, item):
    spec = contract.measures.get(item.get("metric"))
    if spec:
        return spec
    unit = item.get("unit") or ""
    scale = "pp" if unit == "pp" else "percent" if unit == "%" else "volume"
    return MeasureSpec(label=item.get("metric") or "数值", unit=unit, value_scale=scale)


def _caliber_alignment(contract, comparisons, agent_index=None, sql_index=None, raw_agent=None, raw_sql=None):
    has_claim_indexes = agent_index is not None or sql_index is not None
    agent_index = agent_index or {}
    sql_index = sql_index or {}
    raw_agent = raw_agent or {}
    raw_sql = raw_sql or {}
    rows = []
    seen_dimensions = set()
    for item in comparisons:
        key = (coordinate_key(item.get("coordinates") or {}, contract.row_key), item.get("metric"))
        agent_claim = agent_index.get(key)
        sql_claim = sql_index.get(key)
        dimensions = list(contract.row_key) or sorted((item.get("coordinates") or {}).keys())
        for dim in dimensions:
            fallback = (item.get("coordinates") or {}).get(dim)
            agent_raw = raw_agent.get(id(agent_claim), {}).get(dim) if agent_claim else fallback if not has_claim_indexes else None
            sql_raw = raw_sql.get(id(sql_claim), {}).get(dim) if sql_claim else fallback if not has_claim_indexes else None
            agent_source = agent_claim.coordinates.get(dim) if agent_claim else agent_raw
            sql_source = sql_claim.coordinates.get(dim) if sql_claim else sql_raw
            agent_value = normalize_coordinate_value(dim, agent_source) if agent_source is not None else None
            sql_value = normalize_coordinate_value(dim, sql_source) if sql_source is not None else None
            dedupe = (dim, str(agent_raw), str(sql_raw), agent_value, sql_value)
            if dedupe in seen_dimensions or (agent_raw is None and sql_raw is None):
                continue
            seen_dimensions.add(dedupe)
            rows.append({
                "kind": "dimension", "object": DIMENSION_LABELS.get(dim, dim),
                "agent_raw": agent_raw, "sql_raw": sql_raw,
                "agent_normalized": agent_value, "sql_normalized": sql_value,
                "unit": "", "canonical_unit": DIMENSION_CANONICAL.get(dim, "标准文本"),
                "normalization_rule": DIMENSION_RULES.get(dim, "去除首尾空白后精确匹配"),
                "rule": DIMENSION_RULES.get(dim, "去除首尾空白后精确匹配"),
                "status": "MATCH" if agent_value == sql_value else "MISSING" if agent_value is None or sql_value is None else "WRONG_VALUE",
            })

        spec = _item_spec(contract, item)
        expected_raw = sql_claim.raw_value if sql_claim else item.get("expected_raw")
        actual_raw = agent_claim.raw_value if agent_claim else item.get("actual_raw")
        expected_value = sql_claim.value if sql_claim else item.get("expected_value")
        actual_value = agent_claim.value if agent_claim else item.get("actual_value")
        # 双侧值已在 parse_number 按契约 value_scale 规范化，此处只呈现规则，不再二次换算
        normalization_rules = [value_scale_rule(spec)]
        rules = [*normalization_rules, tolerance_rule(spec)]
        rows.append({
            "kind": "measure", "object": item.get("metric") or "数值",
            "agent_raw": actual_raw, "sql_raw": expected_raw,
            "agent_normalized": actual_value, "sql_normalized": expected_value,
            "unit": spec.unit or item.get("unit") or "",
            "canonical_unit": spec.unit or item.get("unit") or "原数值",
            "normalization_rule": "；".join(dict.fromkeys(normalization_rules)),
            "rule": "；".join(dict.fromkeys(rules)),
            "status": item.get("status") or "",
        })
    return {
        "rules": [
            "月份统一为YYYYMM，日期统一为YYYYMMDD",
            "组织名称按别名表映射为标准名称",
            "百分数统一为%，0-1比例按契约或明确倍率转换",
            "百分点统一为pp并保留方向",
            "亿方、万方、立方米统一为m³",
            "计数统一为整数，最终按指标契约容差判断",
        ],
        "rows": rows,
    }


def _sql_periods(snapshot):
    values = []
    params = snapshot.get("params") or {}
    for key, value in params.items():
        if key == "period" or re.fullmatch(r"period_\d+", key):
            text = normalize_coordinate_value("period", value)
            if re.fullmatch(r"20\d{2}(0[1-9]|1[0-2])", text):
                values.append(text)
    for row in snapshot.get("rows") or []:
        for key in ("ym", "period", "期间", "月份", "月份(期数)"):
            text = normalize_coordinate_value("period", row.get(key, ""))
            if re.fullmatch(r"20\d{2}(0[1-9]|1[0-2])", text):
                values.append(text)
    return list(dict.fromkeys(values))


def _align_short_periods(claims, snapshot):
    """Resolve labels such as '3月' only against the SQL query's known periods."""
    candidates = _sql_periods(snapshot)
    by_month = {}
    for period in candidates:
        by_month.setdefault(int(period[-2:]), []).append(period)
    for claim in claims:
        period_dim = next((dim for dim in claim.coordinates if is_period_dim(dim)), "period")
        raw = str(claim.coordinates.get(period_dim, "")).strip()
        match = re.fullmatch(r"(?:20\d{2}年)?\s*(1[0-2]|0?[1-9])\s*月", raw)
        if match and len(by_month.get(int(match.group(1)), [])) == 1:
            claim.coordinates[period_dim] = by_month[int(match.group(1))][0]


def _period_label(values):
    periods = [str(value) for value in values if value]
    if not periods:
        return "未明确"
    return periods[0] if len(periods) == 1 else f"{periods[0]} 至 {periods[-1]}"


def _annotate_org_rollup(contract, comparisons, agent_index, sql_index, info):
    """POSSIBLE_ORG_ROLLUP：上级差值（智能体-SQL）≈ 缺失子单位的 SQL 值时提示疑似合并。

    仅诊断不改判定：MISSING 行仍按缺失计分，总量正确与否看合计行派生比较。
    上下级关系来自 config/organization_hierarchy.json；未配置时在双侧都出现的
    组织里扫描差值吻合的候选。
    """
    parents = (organization_hierarchy() or {}).get("parents") or {}
    org_dim = next((dim for dim in contract.row_key if is_org_dim(dim)), "")
    if not org_dim:
        return
    for item in comparisons:
        if item.get("status") not in ("MISSING", "FIELD_UNRECOGNIZED"):
            continue
        coordinates = item.get("coordinates") or {}
        child = str(coordinates.get(org_dim, "")).strip()
        if not child or child in TOTAL_WORDS:
            continue
        missing_value = item.get("expected_value")
        if missing_value is None:
            continue

        def parent_diff(org_value):
            probe = dict(coordinates, **{org_dim: org_value})
            key = (coordinate_key(probe, contract.row_key), item["metric"])
            agent_claim, sql_claim = agent_index.get(key), sql_index.get(key)
            if not agent_claim or not sql_claim:
                return None
            if agent_claim.value is None or sql_claim.value is None:
                return None
            return agent_claim.value - sql_claim.value

        named = parents.get(child)
        if named:
            candidates = [named]
        else:
            candidates = []
            for (coords, metric) in sql_index:
                org_value = str(dict(coords).get(org_dim, "")).strip()
                if metric != item["metric"] or not org_value or org_value in TOTAL_WORDS or org_value == child:
                    continue
                if (coords, metric) in agent_index and org_value not in candidates:
                    candidates.append(org_value)
        spec = contract.measures.get(item["metric"])
        matched = []
        for org_value in candidates:
            diff = parent_diff(org_value)
            if diff is not None and values_close(diff, missing_value, spec)[0]:
                matched.append((org_value, diff))
        if not matched:
            continue
        item["note"] = "POSSIBLE_ORG_ROLLUP：疑似将「%s」并入上级统计（%s）" % (
            child,
            "；".join("上级「%s」差值≈缺失值" % org_value for org_value, _ in matched),
        )
        info["issues"].append(
            "疑似组织合并：「%s」可能已并入上级单位（%s）统计；明细仍按缺失计分，总量是否一致见合计行派生比较"
            % (child, "、".join(org_value for org_value, _ in matched))
        )


def table_comparison(contract, detail):
    rechecks = detail.get("sql_rechecks") or []
    usable_rechecks = [item for item in rechecks if not item.get("error") and (item.get("columns") or item.get("rows"))]
    snapshot = usable_rechecks[-1] if usable_rechecks else (detail.get("sql_snapshot") or {})
    source = "recheck" if usable_rechecks else "original"
    info = {"items": [], "issues": [], "reference_only": True,
            "source": source,
            "params": snapshot.get("params", {})}
    if not snapshot or snapshot.get("error"):
        info["issues"].append("无可用 SQL 快照")
        return info
    text = primary_block((detail.get("agent_answer") or {}).get("text", ""))
    # 表头字段映射诊断：基于持久化的回答文本即时计算，历史运行同样可展示
    field_mapping = table_header_mapping(text, contract)
    if field_mapping:
        info["field_mapping"] = field_mapping
        unmapped = [row["source_column"] for row in field_mapping if row["status"] == "UNMAPPED"]
        if unmapped:
            info["issues"].append("字段未映射：" + "、".join(unmapped) + "（有多个候选指标或单位不兼容，需人工确认）")
    if contract.case_id == "CX04":
        from evaluator.target_table_comparison import target_comparison
        info.update(target_comparison(text, snapshot))
        info["caliber_alignment"] = _caliber_alignment(contract, info.get("items") or [])
        return info
    saved_agent = detail.get("agent_claims") or []
    agent = ([NumericClaim.model_validate(item) for item in saved_agent]
             if saved_agent else extract_claims(contract, text))
    saved_sql = detail.get("sql_claims") or []
    sql = ([NumericClaim.model_validate(item) for item in saved_sql]
           if source == "original" and saved_sql
           else claims_from_sql_rows(contract, snapshot.get("rows") or []))
    raw_agent = {id(claim): dict(claim.coordinates) for claim in agent}
    raw_sql = {id(claim): dict(claim.coordinates) for claim in sql}
    _align_short_periods(agent, snapshot)
    ambiguous = set()
    def unique(claims):
        for claim in claims:
            for dim, value in list(claim.coordinates.items()):
                if is_org_dim(dim):
                    claim.coordinates[dim] = normalize_org(str(value).replace("**", "").replace("`", ""))
        keys = [(coordinate_key(c.coordinates, contract.row_key), c.metric) for c in claims]
        counts = Counter(keys)
        ambiguous.update(key for key, count in counts.items() if count > 1)
        if any(count > 1 for count in counts.values()):
            info["issues"].append("重复坐标字段已跳过，需确认对应期间")
        return [c for c, key in zip(claims, keys) if counts[key] == 1 and all(k in c.coordinates for k in contract.row_key)]
    agent, sql = unique(agent), unique(sql)
    agent = [c for c in agent if (coordinate_key(c.coordinates, contract.row_key), c.metric) not in ambiguous]
    sql = [c for c in sql if (coordinate_key(c.coordinates, contract.row_key), c.metric) not in ambiguous]
    if not agent:
        info["issues"].append("主表没有可识别的契约指标，需补充字段映射")
    if snapshot.get("truncated"):
        info["issues"].append("SQL 快照已截断")
    comparisons = [i.model_dump(mode="json") for i in compare_claims(contract, sql, agent)]
    recognized = {c.metric for c in agent}
    agent_index = {(coordinate_key(c.coordinates, contract.row_key), c.metric): c for c in agent}
    sql_index = {(coordinate_key(c.coordinates, contract.row_key), c.metric): c for c in sql}
    alignment = (detail.get("result") or {}).get("alignment") or detail.get("alignment") or {}
    agent_period = _period_label(alignment.get("reported_periods") or [])
    sql_period = _period_label(_sql_periods(snapshot))
    paired = agent_only = sql_only = 0
    for item in comparisons:
        key = (coordinate_key(item.get("coordinates") or {}, contract.row_key), item["metric"])
        agent_claim, sql_claim = agent_index.get(key), sql_index.get(key)
        item["agent_period"] = (item.get("coordinates") or {}).get("period") or agent_period
        item["sql_period"] = (item.get("coordinates") or {}).get("period") or sql_period
        item["agent_field"] = agent_claim.evidence if agent_claim else ""
        item["sql_field"] = (contract.measures.get(item["metric"]).sql_column
                             if contract.measures.get(item["metric"]) else "")
        if agent_claim and sql_claim:
            paired += 1
            item["mapping_state"] = "PAIRED"
            item["mapping_reason"] = "指标" + (" + " + " + ".join(contract.row_key) if contract.row_key else "") + "一致"
        elif sql_claim:
            sql_only += 1
            item["mapping_state"] = "AGENT_UNMATCHED"
            item["mapping_reason"] = "SQL 有基准，但智能体未提取到相同指标和坐标"
        else:
            agent_only += 1
            item["mapping_state"] = "SQL_UNMATCHED"
            item["mapping_reason"] = "智能体已提取，但 SQL 没有相同指标和坐标"
        if item.get("note") == DERIVED_TOTAL_NOTE:
            item["mapping_state"] = "DERIVED_TOTAL"
            item["mapping_reason"] = "智能体合计行与 SQL 明细求和对比"
        elif item["status"] == "MISSING" and item["metric"] not in recognized:
            item["status"] = "FIELD_UNRECOGNIZED"
        elif item["status"] == "UNEXPECTED":
            item["status"] = "NO_BENCHMARK"
            item["mapping_reason"] = "智能体额外给出的数值，不参与合格判定"
    _annotate_org_rollup(contract, comparisons, agent_index, sql_index, info)
    info["items"] = comparisons
    info["extraction"] = {
        "agent_claims": len(agent), "sql_claims": len(sql), "paired": paired,
        "agent_unmatched": agent_only, "sql_unmatched": sql_only,
    }
    info["caliber_alignment"] = _caliber_alignment(
        contract, comparisons, agent_index, sql_index, raw_agent, raw_sql,
    )
    return info
