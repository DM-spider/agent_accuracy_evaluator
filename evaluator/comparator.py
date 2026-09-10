# -*- coding: utf-8 -*-
"""按 时间 + 行键 + 指标 逐项比较。"""
from __future__ import annotations

import re
from typing import Dict, Iterable, List, Tuple

from evaluator.aliases import is_date_dim, is_org_dim, is_period_dim, normalize_org
from evaluator.models import CaseContract, ClaimStatus, ComparisonItem, NumericClaim
from evaluator.normalizer import values_close

CoordKey = Tuple[Tuple[str, str], ...]

# 智能体表中的汇总行（组织列写"合计"等），SQL 明细求和可与之派生比较
TOTAL_WORDS = {"合计", "总计", "汇总", "总合计", "全部合计"}
DERIVED_TOTAL_NOTE = "派生基准：SQL 明细求和（compare_derived_total）"


def _valid_ym(year: str, month: str) -> str | None:
    month_number = int(month)
    return f"{year}{month_number:02d}" if 1 <= month_number <= 12 else None


def normalize_coordinate_value(dim: str, value: str) -> str:
    text = str(value or "").strip()
    if is_org_dim(dim):
        return normalize_org(text)
    if is_period_dim(dim):
        compact = re.sub(r"\s+", "", text)
        decimal = re.fullmatch(r"(20\d{4})\.0+", compact)
        if decimal:
            return decimal.group(1)
        if re.fullmatch(r"\d{6}", compact):
            normalized = _valid_ym(compact[:4], compact[4:])
            return normalized or compact
        separated = re.match(r"^(20\d{2})[-/.年](\d{1,2})(?:月|[-/.]\d{1,2})?", compact)
        if separated:
            return _valid_ym(separated.group(1), separated.group(2)) or compact
        return compact
    if is_date_dim(dim):
        compact = re.sub(r"\s+", "", text)
        decimal = re.fullmatch(r"(20\d{6})\.0+", compact)
        if decimal:
            return decimal.group(1)
        matched = re.match(r"^(20\d{2})[-/.年](\d{1,2})[-/.月](\d{1,2})(?:日)?", compact)
        if matched:
            return f"{matched.group(1)}{int(matched.group(2)):02d}{int(matched.group(3)):02d}"
        digits = re.sub(r"[^\d]", "", compact)
        return digits[:8] if len(digits) >= 8 else compact
    return text


def coordinate_key(coords: Dict[str, str], row_key: Iterable[str]) -> CoordKey:
    items = []
    keys = list(row_key) or sorted(coords.keys())
    for dim in keys:
        if dim in coords:
            items.append((dim, normalize_coordinate_value(dim, coords[dim])))
    if not items:
        items = [(k, normalize_coordinate_value(k, v)) for k, v in sorted(coords.items())]
    return tuple(items)


def _index_claims(claims: Iterable[NumericClaim], contract: CaseContract) -> Dict[Tuple[CoordKey, str], NumericClaim]:
    out: Dict[Tuple[CoordKey, str], NumericClaim] = {}
    for claim in claims:
        if claim.confidence == "unparseable":
            continue
        key = (coordinate_key(claim.coordinates, contract.row_key), claim.metric)
        out[key] = claim
    return out


def compare_claims(
    contract: CaseContract,
    sql_claims: List[NumericClaim],
    agent_claims: List[NumericClaim],
) -> List[ComparisonItem]:
    sql_index = _index_claims(sql_claims, contract)
    agent_index = _index_claims(agent_claims, contract)
    items: List[ComparisonItem] = []

    unparseable = [c for c in agent_claims if c.confidence == "unparseable"]
    for claim in unparseable:
        items.append(
            ComparisonItem(
                coordinates=claim.coordinates,
                metric=claim.metric,
                actual_value=claim.value,
                actual_raw=claim.raw_value,
                status=ClaimStatus.UNPARSEABLE,
                evidence=claim.evidence,
                evidence_claim_id=claim.claim_id,
                unit=claim.unit,
            )
        )

    required_metrics = {k for k, spec in contract.measures.items() if spec.required}
    for key, expected in sql_index.items():
        if expected.metric not in required_metrics and required_metrics:
            # 非必答字段：仅当智能体也返回时比较
            if key not in agent_index:
                continue
        spec = contract.measures.get(expected.metric)
        actual = agent_index.get(key)
        if actual is None:
            items.append(
                ComparisonItem(
                    coordinates=dict(expected.coordinates),
                    metric=expected.metric,
                    expected_value=expected.value,
                    expected_raw=expected.raw_value,
                    status=ClaimStatus.MISSING,
                    evidence=expected.evidence,
                    unit=expected.unit,
                    aggregation=expected.aggregation or (spec.aggregation if spec else ""),
                )
            )
            continue
        expected_agg = expected.aggregation or (spec.aggregation if spec else "")
        actual_agg = actual.aggregation or expected_agg
        rules = []
        if actual.conversion_factor and actual.scale_token:
            rules.append("%s×%s → %s" % (actual.scale_token, int(actual.conversion_factor), actual.unit or (spec.unit if spec else "m³")))
        if expected_agg and actual_agg and expected_agg != actual_agg:
            items.append(
                ComparisonItem(
                    coordinates=dict(expected.coordinates),
                    metric=expected.metric,
                    expected_value=expected.value,
                    actual_value=actual.value,
                    expected_raw=expected.raw_value,
                    actual_raw=actual.raw_value,
                    status=ClaimStatus.CALIBER_MISMATCH,
                    evidence=actual.evidence,
                    evidence_claim_id=actual.claim_id,
                    unit=actual.unit or expected.unit,
                    note="单月/累计口径不一致",
                    aggregation=actual_agg,
                    normalization_rule="；".join(rules) if rules else "聚合口径不一致，不按数值匹配",
                )
            )
            continue
        # 双侧值均已在 parse_number 按契约 value_scale 确定性规范化到同一单位空间，直接比较
        expected_value, actual_value = expected.value, actual.value
        ok, delta, how = values_close(expected_value, actual_value, spec)
        if expected_value is None and actual_value is None:
            status = ClaimStatus.MATCH
        elif ok and how == "exact":
            status = ClaimStatus.MATCH
        elif ok:
            status = ClaimStatus.MATCH_WITH_TOLERANCE
        else:
            status = ClaimStatus.WRONG_VALUE
        items.append(
            ComparisonItem(
                coordinates=dict(expected.coordinates),
                metric=expected.metric,
                expected_value=expected_value,
                actual_value=actual_value,
                expected_raw=expected.raw_value,
                actual_raw=actual.raw_value,
                delta=delta,
                tolerance=spec.tolerance.eps if spec else None,
                status=status,
                evidence=actual.evidence,
                evidence_claim_id=actual.claim_id,
                unit=actual.unit or expected.unit,
                aggregation=expected_agg,
                normalization_rule="；".join(rules),
            )
        )

    org_dim = next((dim for dim in contract.row_key if is_org_dim(dim)), "")
    derived_metrics = {claim.metric for claim in agent_claims if str(claim.coordinates.get(org_dim, "")).strip() in TOTAL_WORDS} if org_dim else set()
    items.extend(_derived_total_items(contract, sql_claims, agent_claims))

    for key, actual in agent_index.items():
        if key in sql_index:
            continue
        # 智能体合计行：交给派生比较，不按 UNEXPECTED 处理
        if (
            org_dim
            and str(actual.coordinates.get(org_dim, "")).strip() in TOTAL_WORDS
            and actual.metric in derived_metrics
        ):
            continue
        # 有业务坐标但 SQL 中找不到 → UNEXPECTED
        items.append(
            ComparisonItem(
                coordinates=dict(actual.coordinates),
                metric=actual.metric,
                actual_value=actual.value,
                actual_raw=actual.raw_value,
                status=ClaimStatus.UNEXPECTED,
                evidence=actual.evidence,
                evidence_claim_id=actual.claim_id,
                unit=actual.unit,
            )
        )
    return items


def _derived_total_items(contract: CaseContract, sql_claims, agent_claims) -> List[ComparisonItem]:
    """合计行派生比较：智能体合计 vs SQL 明细求和（compare_derived_total）。

    仅当 SQL 没有自己的合计行（正常配对无法处理）时派生；
    明细缺失行仍按 MISSING 呈现，合计另立一项判断"总量是否正确"，
    使组织归属错误（如把子单位并入上级）与总量错误可区分。
    """
    org_dim = next((dim for dim in contract.row_key if is_org_dim(dim)), "")
    if not org_dim:
        return []
    totals: Dict[str, List[NumericClaim]] = {}
    for claim in agent_claims:
        if claim.confidence == "unparseable" or claim.value is None:
            continue
        if str(claim.coordinates.get(org_dim, "")).strip() in TOTAL_WORDS:
            totals.setdefault(claim.metric, []).append(claim)
    items: List[ComparisonItem] = []
    for metric, claims in sorted(totals.items()):
        if any(
            claim.metric == metric and str(claim.coordinates.get(org_dim, "")).strip() in TOTAL_WORDS
            for claim in sql_claims
        ):
            continue  # SQL 自带合计行，走正常配对
        detail_values = [
            claim.value
            for claim in sql_claims
            if claim.metric == metric
            and claim.confidence != "unparseable"
            and claim.value is not None
            and str(claim.coordinates.get(org_dim, "")).strip() not in TOTAL_WORDS
        ]
        if not detail_values:
            continue
        agent_total = sum(claim.value for claim in claims)
        expected_sum = sum(detail_values)
        spec = contract.measures.get(metric)
        ok, delta, how = values_close(expected_sum, agent_total, spec)
        if ok and how == "exact":
            status = ClaimStatus.MATCH
        elif ok:
            status = ClaimStatus.MATCH_WITH_TOLERANCE
        else:
            status = ClaimStatus.WRONG_VALUE
        first = claims[0]
        items.append(
            ComparisonItem(
                coordinates={org_dim: "合计"},
                metric=metric,
                expected_value=expected_sum,
                actual_value=agent_total,
                expected_raw="SUM(%d 行 SQL 明细) = %s" % (len(detail_values), expected_sum),
                actual_raw="；".join(str(claim.raw_value) for claim in claims),
                delta=delta,
                tolerance=spec.tolerance.eps if spec else None,
                status=status,
                evidence=first.evidence,
                evidence_claim_id=first.claim_id,
                unit=first.unit or (spec.unit if spec else ""),
                note=DERIVED_TOTAL_NOTE,
            )
        )
    return items
