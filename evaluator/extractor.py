# -*- coding: utf-8 -*-
"""从智能体回答中提取有业务坐标的数值断言。"""
from __future__ import annotations

import json
import re
from typing import Callable, Dict, List, Optional

from evaluator.aliases import canonical_metric, is_org_dim, normalize_org, organization_aliases, resolved_dimension_columns
from evaluator.models import CaseContract, NumericClaim
from evaluator.normalizer import VALUE_TAIL, conversion_meta, is_non_metric_entity, parse_number

JSON_BLOCK_RE = re.compile(r"<result>\s*(\{.*?\})\s*</result>", re.S)
KV_RE = re.compile(
    r"([\u4e00-\u9fffA-Za-z0-9（）()]{2,16})\s*[:：是为]\s*(" + VALUE_TAIL + r")",
)


def _metric_core(label: str) -> str:
    text = str(label or "").replace("**", "").replace("`", "").strip()
    text = re.sub(r"^[^\w\u4e00-\u9fff]+", "", text)
    text = re.sub(r"20\d{2}\s*年|(?:1[0-2]|0?[1-9])\s*月", "", text)
    text = re.sub(r"累计|年累计|本月|当月|单月|月度|实际|年度", "", text)
    text = re.sub(r"[（(][^）)]*[）)]|[%％]|\s+", "", text)
    return text


def detect_aggregation(label: str, default: str = "") -> str:
    text = str(label or "")
    if re.search(r"年累计|累计", text) and not re.search(r"单月|当月", text):
        return "ytd"
    if re.search(r"单月|当月|月度", text):
        return "single_month"
    return default


def detect_period_role(label: str) -> str:
    text = str(label or "")
    if re.search(r"环比|同比变化|同比变动", text):
        return "comparison"
    if re.search(r"上月|上期|上年同期|去年同期", text):
        return "previous"
    if re.search(r"本月|本期|当月", text):
        return "current"
    return ""


_UNIT_FAMILIES = {
    "m³": {"m³", "m3", "立方米", "立方", "方", "万m³", "亿m³", "万方", "亿方", "升"},
    "%": {"%", "％", "百分数", "百分比"},
    "‰": {"‰"},
    "pp": {"pp", "个百分点"},
    "count": {"单", "单数", "个", "件", "次", "条", "宗", "起", "处", "座", "台", "户"},
    "h": {"小时", "h"},
    "单/km": {"单/km", "km"},
    "元": {"元", "万元", "亿元"},
}


def _unit_token(label: str) -> str:
    match = re.search(r"[（(]([^（）()]*)[）)]\s*$", str(label or "").strip())
    return (match.group(1) if match else "").strip()


def _unit_compatible(token: str, unit: str) -> bool:
    if not token:
        return True
    allowed = _UNIT_FAMILIES.get(unit, {unit} if unit else set())
    return not allowed or token in allowed or token == unit


def _metric_word_match(label: str, contract: CaseContract):
    """指标词包含匹配 + 单位校验，返回 (metric, candidates)。

    仅允许"标签核心词 ⊆ 指标核心词"单向包含（如 漏损水量 ⊆ 维修漏损水量）；
    不做反向包含：_metric_core 会把「实际」等限定词剥掉（实际产销差率→产销差率），
    反向包含会让「目标产销差率」误映射到「实际产销差率」。
    仅一个候选指标时允许自动映射，多个候选不猜测、交人工确认；
    label 带单位词时必须与契约单位兼容，避免"维修工单数(单)"误映射到体积指标。
    """
    core = _metric_core(label)
    if len(core) < 2:
        return None, []
    candidates = []
    for key, spec in contract.measures.items():
        targets = {_metric_core(v) for v in [key, spec.label, *spec.aliases] if v}
        targets.discard("")
        if any(core in target for target in targets):
            candidates.append(key)
    if len(candidates) != 1:
        return None, sorted(candidates)
    spec = contract.measures[candidates[0]]
    if not _unit_compatible(_unit_token(label), spec.unit):
        return None, [candidates[0]]
    return candidates[0], [candidates[0]]


def _resolve_metric(label: str, contract: CaseContract):
    """表头→指标分级解析：①契约别名精确（含去括号单位）②指标词包含+单位校验。"""
    metric = canonical_metric(label, contract.measures)
    if metric:
        return metric, "contract_alias", "exact", []
    metric, candidates = _metric_word_match(label, contract)
    if metric:
        return metric, "metric_word", "fuzzy", candidates
    return None, "", "", candidates


def metric_for_label(label: str, contract: CaseContract) -> Optional[str]:
    """Map decorated headers only when the contract leaves one unambiguous metric."""
    role = detect_period_role(label)
    metric = canonical_metric(label, contract.measures)
    if role:
        if metric and contract.measures[metric].period_role == role:
            return metric
        role_matches = [key for key, spec in contract.measures.items() if spec.period_role == role]
        if len(role_matches) == 1:
            return role_matches[0]
        if role_matches:
            # 期间角色存在但无法唯一对应，不做模糊映射
            return None
    if metric:
        return metric
    matched, _candidates = _metric_word_match(label, contract)
    return matched


def table_header_mapping(text: str, contract: CaseContract) -> List[Dict]:
    """表头字段映射诊断：逐列给出映射状态、目标、方法与候选；未映射必须留原因。

    status: MAPPED（维度/指标）/ UNMAPPED（有候选但不确定）/ NON_TARGET（非本题基准字段）
    """
    dim_cols = resolved_dimension_columns(contract)
    rows: List[Dict] = []
    seen = set()
    for table in parse_md_tables(text or ""):
        for raw_header in table["header"]:
            col = str(raw_header).replace("**", "").replace("`", "").strip()
            if not col or col in seen:
                continue
            seen.add(col)
            entry = {
                "source_column": col, "target_metric": "", "target_kind": "",
                "mapping_method": "", "confidence": "", "status": "NON_TARGET",
                "candidate_metrics": [],
            }
            dim_hit = None
            for dim, aliases in dim_cols.items():
                names = set(aliases + [dim])
                if col in names or canonical_metric(col) == dim:
                    dim_hit = dim
                    break
            if dim_hit:
                entry.update(
                    target_metric=dim_hit, target_kind="dimension",
                    mapping_method="dimension_alias", confidence="exact", status="MAPPED",
                )
            else:
                metric, method, confidence, candidates = _resolve_metric(col, contract)
                if metric:
                    entry.update(
                        target_metric=metric, target_kind="measure",
                        mapping_method=method, confidence=confidence, status="MAPPED",
                    )
                elif candidates:
                    entry.update(status="UNMAPPED", candidate_metrics=candidates)
            rows.append(entry)
    return rows


def parse_md_tables(text: str) -> List[Dict[str, List[List[str]]]]:
    tables = []
    header = None
    rows: List[List[str]] = []
    for line in (text or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("|"):
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            if all(re.fullmatch(r":?-{2,}:?", c or "") for c in cells):
                continue
            if header is None:
                header = cells
            else:
                rows.append(cells)
        else:
            if header is not None:
                tables.append({"header": header, "rows": rows})
            header, rows = None, []
    if header is not None:
        tables.append({"header": header, "rows": rows})
    return tables


def _claim_id(case_id: str, index: int, source: str) -> str:
    return f"{case_id}:{source}:{index}"


def _coords_from_row(row: Dict[str, str], contract: CaseContract) -> Dict[str, str]:
    coords: Dict[str, str] = {}
    dim_cols = resolved_dimension_columns(contract)
    for dim in contract.row_key:
        aliases = dim_cols.get(dim, [dim])
        value = None
        for alias in aliases + [dim]:
            if alias in row and str(row[alias]).strip():
                value = row[alias]
                break
            for key, cell in row.items():
                if key.startswith("_"):
                    continue
                if canonical_metric(key) == dim or key == alias or key.endswith(alias):
                    value = cell
                    break
            if value:
                break
        if value is None:
            continue
        text = str(value).strip()
        if is_org_dim(dim):
            text = normalize_org(text)
        coords[dim] = text
    return coords


def _make_claim(
    contract: CaseContract,
    metric: str,
    raw: object,
    evidence: str,
    extractor: str,
    coordinates: Dict[str, str],
    index: int,
    source: str = "agent",
    confidence: str = "deterministic",
) -> Optional[NumericClaim]:
    spec = contract.measures.get(metric)
    if spec is None:
        return None
    if is_non_metric_entity(raw) and not re.fullmatch(r"[-+]?\d+(?:\.\d+)?", str(raw)) and spec.value_scale != "text":
        return None
    value, unit, err = parse_number(raw, spec, source)
    conf = "unparseable" if err == "unparseable_yoy" else confidence
    if err == "unparseable_yoy":
        value = None
    token, factor = conversion_meta(raw, spec)
    evidence_label = evidence[:300]
    aggregation = detect_aggregation(evidence_label, spec.aggregation or "")
    if source == "sql":
        aggregation = spec.aggregation or aggregation
    return NumericClaim(
        claim_id=_claim_id(contract.case_id, index, source),
        case_id=contract.case_id,
        source=source,
        coordinates=coordinates,
        metric=metric,
        raw_value=None if raw is None else str(raw),
        value=value,
        unit=unit or spec.unit,
        evidence=evidence_label,
        extractor=extractor,
        aggregation=aggregation,
        period_role=spec.period_role or detect_period_role(evidence_label),
        scale_token=token,
        conversion_factor=factor,
        confidence=conf,
    )


def extract_json_block(text: str, contract: CaseContract, start_index: int) -> List[NumericClaim]:
    claims: List[NumericClaim] = []
    match = JSON_BLOCK_RE.search(text or "")
    if not match:
        # 约定 JSON 代码块
        fence = re.search(r"```json\s*(\{.*?\})\s*```", text or "", re.S)
        if not fence:
            return []
        raw_json = fence.group(1)
        evidence = fence.group(0)
    else:
        raw_json = match.group(1)
        evidence = match.group(0)
    try:
        payload = json.loads(raw_json)
    except json.JSONDecodeError:
        return []
    idx = start_index
    fields = payload.get("fields") if isinstance(payload, dict) else None
    if isinstance(fields, dict):
        for key, raw in fields.items():
            metric = canonical_metric(str(key), contract.measures) or (
                str(key) if str(key) in contract.measures else None
            )
            if not metric:
                continue
            claim = _make_claim(contract, metric, raw, evidence[:200], "json_block", {}, idx)
            if claim:
                claims.append(claim)
                idx += 1
    details = payload.get("detail") if isinstance(payload, dict) else None
    if isinstance(details, list):
        for row in details:
            if not isinstance(row, dict):
                continue
            coords = _coords_from_row({str(k): str(v) for k, v in row.items()}, contract)
            if contract.row_key and not coords:
                continue
            for key, raw in row.items():
                metric = canonical_metric(str(key), contract.measures)
                if not metric:
                    continue
                claim = _make_claim(contract, metric, raw, json.dumps(row, ensure_ascii=False)[:200], "json_block", coords, idx)
                if claim:
                    claims.append(claim)
                    idx += 1
    return claims


def extract_tables(text: str, contract: CaseContract, start_index: int) -> List[NumericClaim]:
    claims: List[NumericClaim] = []
    idx = start_index
    for table in parse_md_tables(text):
        header = [h.replace("**", "").replace("`", "").strip() for h in table["header"]]
        rows = table["rows"]
        if not header:
            continue
        mapped = [metric_for_label(h, contract) for h in header]
        dim_idx = []
        dim_cols = resolved_dimension_columns(contract)
        for i, col in enumerate(header):
            for dim, aliases in dim_cols.items():
                names = set(aliases + [dim])
                if col in names or canonical_metric(col) == dim:
                    dim_idx.append((i, dim))
                    break
        is_kv = len(header) == 2 and (
            header[0] in {"指标", "项目", "指标名称", "名称"} or metric_for_label(header[0], contract)
        )
        if is_kv:
            for row in rows:
                label = row[0] if row else ""
                raw = row[1] if len(row) > 1 else ""
                metric = metric_for_label(label, contract)
                if not metric:
                    continue
                evidence = "|".join(row)
                claim = _make_claim(contract, metric, raw, evidence, "markdown_table", {}, idx)
                if claim:
                    claims.append(claim)
                    idx += 1
            continue
        for row in rows:
            row_map = {header[i]: (row[i] if i < len(row) else "") for i in range(len(header))}
            coords: Dict[str, str] = {}
            for i, dim in dim_idx:
                value = row[i] if i < len(row) else ""
                if is_org_dim(dim):
                    value = normalize_org(value)
                if str(value).strip():
                    coords[dim] = str(value).strip()
            if not coords:
                coords = _coords_from_row(row_map, contract)
            if contract.row_key and not any(k in coords for k in contract.row_key):
                # 无行键的明细数值不进入自动评分
                continue
            for i, metric in enumerate(mapped):
                if not metric:
                    continue
                raw = row[i] if i < len(row) else ""
                claim = _make_claim(contract, metric, raw, "|".join(row), "markdown_table", coords, idx)
                if claim:
                    claims.append(claim)
                    idx += 1
    return claims


def extract_kv_sentences(text: str, contract: CaseContract, start_index: int) -> List[NumericClaim]:
    claims: List[NumericClaim] = []
    idx = start_index
    for match in KV_RE.finditer(text or ""):
        label, raw = match.group(1), match.group(2)
        metric = metric_for_label(label, contract)
        coords: Dict[str, str] = {}
        window = (text or "")[max(0, match.start() - 20): match.end()]
        for org_alias, canonical in organization_aliases().items():
            if org_alias and org_alias in window.replace(" ", ""):
                coords["organization"] = canonical
                break
        if not metric:
            continue
        if contract.row_key and "organization" in contract.row_key and "organization" not in coords:
            continue
        claim = _make_claim(contract, metric, raw, match.group(0), "kv_sentence", coords, idx)
        if claim:
            claims.append(claim)
            idx += 1
    return claims


def extract_alias_anchors(text: str, contract: CaseContract, start_index: int) -> List[NumericClaim]:
    claims: List[NumericClaim] = []
    idx = start_index
    existing = {c.metric for c in claims}
    for metric, spec in contract.measures.items():
        if metric in existing:
            continue
        for alias in [metric, spec.label, *spec.aliases]:
            if not alias:
                continue
            pat = re.compile(re.escape(alias) + r"[^0-9\-]{0,8}(" + VALUE_TAIL + r")")
            match = pat.search(text or "")
            if not match:
                continue
            claim = _make_claim(contract, metric, match.group(1), match.group(0), "alias_anchor", {}, idx)
            if claim:
                claims.append(claim)
                idx += 1
                break
    return claims


def verify_llm_evidence(text: str, raw_value: str) -> bool:
    if not raw_value:
        return False
    compact = (text or "").replace(",", "").replace(" ", "")
    token = str(raw_value).replace(",", "").replace(" ", "")
    return token in compact or str(raw_value) in (text or "")


def extract_claims(
    contract: CaseContract,
    answer_text: str,
    llm_extractor: Optional[Callable[[CaseContract, str], List[Dict]]] = None,
) -> List[NumericClaim]:
    """提取顺序：JSON → Markdown 表 → 键值句 → 别名锚定 → 可选 LLM。"""
    if not contract.numeric_evaluable:
        return []
    claims: List[NumericClaim] = []
    claims.extend(extract_json_block(answer_text, contract, 0))
    if not claims:
        claims.extend(extract_tables(answer_text, contract, len(claims)))
    covered = {c.metric for c in claims}
    if len(covered) < len(contract.measures) or (contract.row_key and not claims):
        extra = extract_kv_sentences(answer_text, contract, len(claims))
        claims.extend(extra)
        extra2 = extract_alias_anchors(answer_text, contract, len(claims))
        seen = {(tuple(sorted(c.coordinates.items())), c.metric) for c in claims}
        for claim in extra2:
            key = (tuple(sorted(claim.coordinates.items())), claim.metric)
            if key not in seen:
                claims.append(claim)
                seen.add(key)
    if llm_extractor and contract.measures:
        try:
            payload = llm_extractor(contract, answer_text) or []
        except Exception:
            payload = []
        for item in payload:
            raw = str(item.get("raw_value") or item.get("value") or "")
            evidence = str(item.get("evidence") or "")
            if not verify_llm_evidence(answer_text, raw) and not verify_llm_evidence(answer_text, evidence):
                continue
            metric = canonical_metric(str(item.get("metric") or ""), contract.measures)
            if not metric:
                continue
            coords = {k: str(v) for k, v in (item.get("coordinates") or {}).items()}
            if "organization" in coords:
                coords["organization"] = normalize_org(coords["organization"])
            claim = _make_claim(
                contract,
                metric,
                raw,
                evidence or raw,
                "llm",
                coords,
                len(claims),
                confidence="llm",
            )
            if claim:
                claims.append(claim)
    return claims


def claims_from_sql_rows(contract: CaseContract, rows: List[Dict]) -> List[NumericClaim]:
    claims: List[NumericClaim] = []
    idx = 0
    for row in rows or []:
        row_s = {str(k): row[k] for k in row}
        coords = _coords_from_row({k: "" if v is None else str(v) for k, v in row_s.items()}, contract)
        if not coords and contract.row_key:
            # 尝试英文别名
            lowered = {str(k).lower(): v for k, v in row.items()}
            for dim, aliases in contract.dimension_columns.items():
                for alias in aliases + [dim]:
                    if alias in row:
                        val = row[alias]
                        coords[dim] = normalize_org(str(val)) if dim == "organization" else str(val)
                        break
                    if alias.lower() in lowered:
                        val = lowered[alias.lower()]
                        coords[dim] = normalize_org(str(val)) if dim == "organization" else str(val)
                        break
        for metric, spec in contract.measures.items():
            raw = None
            for candidate in filter(None, [spec.sql_column, metric, spec.label, *spec.aliases]):
                if candidate in row:
                    raw = row[candidate]
                    break
                for key in row:
                    if canonical_metric(str(key), contract.measures) == metric:
                        raw = row[key]
                        break
                if raw is not None:
                    break
            if raw is None:
                continue
            claim = _make_claim(
                contract,
                metric,
                raw,
                f"sql:{metric}",
                "sql_result",
                coords,
                idx,
                source="sql",
            )
            if claim:
                claims.append(claim)
                idx += 1
    return claims


def expected_table(case: Dict) -> tuple[list, list]:
    """把黄金集 expected 转成页面可展示的表格（Mock 未连库时用）。"""
    exp = case.get("expected") or {}
    columns: list = []
    rows: list = []
    fields = exp.get("fields") or []
    detail_rows = list((exp.get("detail") or {}).get("rows") or [])
    if fields and not detail_rows:
        columns = ["指标", "数值", "单位"]
        for field in fields:
            rows.append({
                "指标": field.get("label") or field.get("key"),
                "数值": field.get("value"),
                "单位": field.get("unit") or "",
            })
        return columns, rows
    if detail_rows:
        columns = list(detail_rows[0].keys())
        rows = detail_rows
        if fields:
            extra = {field.get("label") or field.get("key"): field.get("value") for field in fields}
            rows = [extra] + rows
            for key in extra:
                if key not in columns:
                    columns.insert(0, key)
        return columns, rows
    return [], []


def claims_from_expected(contract: CaseContract, case: Dict) -> List[NumericClaim]:
    exp = case.get("expected") or {}
    rows: List[Dict] = []
    if exp.get("fields"):
        stat_row = {}
        for field in exp["fields"]:
            stat_row[field["key"]] = field.get("value")
        rows.append(stat_row)
    detail = exp.get("detail") or {}
    for row in detail.get("rows") or []:
        rows.append(row)
    return claims_from_sql_rows(contract, rows)
