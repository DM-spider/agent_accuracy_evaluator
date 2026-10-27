# -*- coding: utf-8 -*-
"""LLM 结构化评估契约：固定维度、问题码、决策校验与数值复核。

本模块只约束 LLM 输出的一致性，不重新构造规则比较结论。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from evaluator.aliases import canonical_metric
from evaluator.models import (
    ISSUE_CODES,
    PRIMARY_ISSUE_ORDER,
    PRIMARY_ISSUE_PRIORITY,
    REQUIRED_DIMENSIONS,
    DimensionEvaluation,
    DimensionStatus,
    EvaluationDifference,
    EvaluationVerdict,
    LlmEvaluationArtifact,
    LlmEvaluationResult,
)
from evaluator.normalizer import parse_number, values_close

# 七个固定诊断维度与问题码白名单在 evaluator.models 中定义，这里保持可导入。
__all__ = [
    "REQUIRED_DIMENSIONS",
    "ISSUE_CODES",
    "PRIMARY_ISSUE_PRIORITY",
    "validate_decision",
    "enforce_decision",
    "unevaluable_result",
    "unevaluable_artifact",
    "artifact_from_evaluation",
    "validate_numeric_differences",
    "coerce_payload",
    "apply_reconciliation",
]

PARTIAL_SUPPORT_CODES = {"MISSING_FIELD", "MISSING_ROW"}
HARD_MISMATCH_DIMENSIONS = {"period", "scope", "grain", "unit_caliber", "numeric_accuracy"}
LLM_FAILURE_CODES = {"LLM_CALL_FAILED", "LLM_RESPONSE_INVALID", "LLM_INPUT_TOO_LARGE"}
# 程序复核后仍然一票否决的根因：期间、粒度、单位、口径错，数字对得再齐也不算合格。
VETO_CODES = ("PERIOD_MISMATCH", "GRAIN_MISMATCH", "UNIT_MISMATCH", "CALIBER_MISMATCH")

# 判分模型偶尔自造问题码。与其整题作废，不如映射到最近的合法码。
ISSUE_CODE_ALIASES = {
    "VALUE_MISMATCH": "WRONG_VALUE",
    "WRONG_VALUES": "WRONG_VALUE",
    "INCORRECT_VALUE": "WRONG_VALUE",
    "NUMERIC_MISMATCH": "WRONG_VALUE",
    "ROW_COVERAGE_MISMATCH": "MISSING_ROW",
    "MISSING_ROWS": "MISSING_ROW",
    "ROW_MISSING": "MISSING_ROW",
    "MISSING_ROW_DATA": "MISSING_ROW",
    "MISSING_VALUE": "MISSING_FIELD",
    "VALUE_MISSING": "MISSING_FIELD",
    "MISSING_VALUES": "MISSING_FIELD",
    "MISSING_COLUMN": "MISSING_FIELD",
    "COLUMN_MISMATCH": "MISSING_FIELD",
    "EXTRA_ROW": "SCOPE_MISMATCH",
    "EXTRA_ROWS": "SCOPE_MISMATCH",
    "SCOPE_EXTRA": "SCOPE_MISMATCH",
    "SCOPE_MISSING": "SCOPE_MISMATCH",
    "PERIOD_COVERAGE_MISMATCH": "PERIOD_MISMATCH",
    "DATE_MISMATCH": "PERIOD_MISMATCH",
    "UNIT_CALIBER_MISMATCH": "UNIT_MISMATCH",
    "UNIT_ERROR": "UNIT_MISMATCH",
    "CALIBER_ERROR": "CALIBER_MISMATCH",
    "AGGREGATION_MISMATCH": "CALIBER_MISMATCH",
    "GRAIN_ERROR": "GRAIN_MISMATCH",
    "CONTRADICTION": "CONTRADICTORY_TEXT",
}
STATUS_ALIASES = {
    "OK": "MATCH",
    "PASS": "MATCH",
    "CORRECT": "MATCH",
    "FAIL": "MISMATCH",
    "FAILED": "MISMATCH",
    "WRONG": "MISMATCH",
    "ERROR": "MISMATCH",
    "MISSING": "MISMATCH",
    "PARTIALLY_MATCHED": "PARTIAL",
    "PARTIALLY_MATCH": "PARTIAL",
    "N/A": "NA",
    "NOT_APPLICABLE": "NA",
}
VERDICT_ALIASES = {
    "PASS": "QUALIFIED",
    "PASSED": "QUALIFIED",
    "FAIL": "UNQUALIFIED",
    "FAILED": "UNQUALIFIED",
    "PARTIALLY_QUALIFIED": "PARTIAL",
    "INVALID": "UNEVALUABLE",
    "UNKNOWN": "UNEVALUABLE",
    "CANNOT_EVALUATE": "UNEVALUABLE",
}
SEVERITY_ALIASES = {"CRITICAL": "ERROR", "MAJOR": "ERROR", "FATAL": "ERROR", "MINOR": "WARNING", "INFO": "WARNING"}
TOP_LEVEL_FIELDS = set(LlmEvaluationResult.model_fields)


def _now() -> str:
    return datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds")


def _prioritized(codes) -> Optional[str]:
    ranked = [code for code in dict.fromkeys(codes) if code in PRIMARY_ISSUE_ORDER]
    if not ranked:
        return None
    return min(ranked, key=lambda code: PRIMARY_ISSUE_ORDER[code])


def validate_decision(result: LlmEvaluationResult) -> List[str]:
    """返回 LLM 输出的一致性意见，不修改结果。"""
    notes: List[str] = []
    codes = list(dict.fromkeys(result.issue_codes))
    error_codes = [item.type for item in result.differences if item.severity == "ERROR"]
    if result.primary_issue_code and result.primary_issue_code not in codes:
        notes.append("primary_issue_code 未包含在 issue_codes 中")
    top = _prioritized(codes)
    if (
        top
        and result.primary_issue_code
        and PRIMARY_ISSUE_ORDER.get(result.primary_issue_code, 999) > PRIMARY_ISSUE_ORDER[top]
    ):
        notes.append(f"primary_issue_code 应为更高优先级的 {top}")

    dims = result.dimensions
    hard_mismatch = [key for key, value in dims.items() if key in HARD_MISMATCH_DIMENSIONS and value.status == DimensionStatus.MISMATCH]
    if result.overall_verdict == EvaluationVerdict.QUALIFIED:
        if error_codes:
            notes.append("QUALIFIED 不允许存在 ERROR 级差异")
    if result.overall_verdict == EvaluationVerdict.PARTIAL:
        # 部分作答本来就有对有错，不再要求差异必须是 WARNING，也不要求只能漏行/漏字段。
        if not codes and not error_codes and not hard_mismatch:
            notes.append("PARTIAL 必须给出问题码或差异")
    if result.overall_verdict == EvaluationVerdict.UNQUALIFIED:
        if not codes and not error_codes and not hard_mismatch:
            notes.append("UNQUALIFIED 必须给出问题码或错误差异")
    if result.overall_verdict == EvaluationVerdict.UNEVALUABLE:
        if codes and not set(codes) & {"INSUFFICIENT_EVIDENCE", *LLM_FAILURE_CODES}:
            notes.append("UNEVALUABLE 应给出证据不足或调用失败问题码")
    return notes


def enforce_decision(result: LlmEvaluationResult) -> Tuple[LlmEvaluationResult, List[str]]:
    """按固定决策规则修正不一致的 LLM 结论，并返回修正说明。"""
    notes = validate_decision(result)
    if not notes:
        return result, []
    data = result.model_dump(mode="json")
    error_diffs = any(item.severity == "ERROR" for item in result.differences)
    dims = result.dimensions
    hard_mismatch = any(
        key in HARD_MISMATCH_DIMENSIONS and value.status == DimensionStatus.MISMATCH
        for key, value in dims.items()
    )
    codes = set(result.issue_codes)
    verdict = result.overall_verdict
    if verdict == EvaluationVerdict.QUALIFIED:
        # WARNING 级问题码不推翻合格结论
        verdict = EvaluationVerdict.UNQUALIFIED if (error_diffs or hard_mismatch) else EvaluationVerdict.QUALIFIED
        if verdict == EvaluationVerdict.QUALIFIED and codes:
            data["issue_codes"] = []
            data["primary_issue_code"] = None
            codes = set()
    elif verdict == EvaluationVerdict.PARTIAL:
        if not codes and not error_diffs and not hard_mismatch:
            verdict = EvaluationVerdict.UNEVALUABLE
    elif verdict == EvaluationVerdict.UNQUALIFIED:
        if not codes and not error_diffs and not hard_mismatch:
            known = [value.status for value in dims.values() if value.status not in {DimensionStatus.UNKNOWN, DimensionStatus.NA}]
            verdict = (
                EvaluationVerdict.QUALIFIED
                if known and all(status == DimensionStatus.MATCH for status in known)
                else EvaluationVerdict.UNEVALUABLE
            )
    data["overall_verdict"] = verdict.value
    top = _prioritized(codes)
    if top is not None and (
        data.get("primary_issue_code") is None
        or PRIMARY_ISSUE_ORDER.get(data.get("primary_issue_code"), 999) > PRIMARY_ISSUE_ORDER[top]
    ):
        data["primary_issue_code"] = top
    if isinstance(data.get("primary_issue_code"), str) and data["primary_issue_code"] not in codes:
        data["primary_issue_code"] = top
    data["needs_human_review"] = bool(result.needs_human_review or verdict == EvaluationVerdict.UNEVALUABLE)
    return LlmEvaluationResult.model_validate(data), notes


def unevaluable_result(
    code: str,
    summary: str,
    *,
    confidence: float = 0.0,
    dimensions: Optional[Dict[str, DimensionEvaluation]] = None,
) -> LlmEvaluationResult:
    """构造无法评估结论：七个维度全部 UNKNOWN，供调用失败、证据不足时使用。"""
    dims = {
        key: DimensionEvaluation(status=DimensionStatus.UNKNOWN, reason="")
        for key in REQUIRED_DIMENSIONS
    }
    if dimensions:
        for key, value in dimensions.items():
            if key in dims:
                dims[key] = value
    valid_code = code if code in ISSUE_CODES else "INSUFFICIENT_EVIDENCE"
    return LlmEvaluationResult(
        overall_verdict=EvaluationVerdict.UNEVALUABLE,
        confidence=confidence,
        summary=summary,
        primary_issue_code=valid_code,
        issue_codes=[valid_code],
        dimensions=dims,
        differences=[],
        needs_human_review=True,
    )


def artifact_from_evaluation(
    result: LlmEvaluationResult,
    *,
    provider: str = "openai-compatible",
    model: str = "",
    prompt_version: str = "v1",
    input_hash: str = "",
    latency_ms: int = 0,
    token_usage: Optional[Dict[str, int]] = None,
    validation_notes: Optional[List[str]] = None,
    error: Optional[str] = None,
) -> LlmEvaluationArtifact:
    return LlmEvaluationArtifact(
        evaluation=result,
        provider=provider,
        model=model,
        prompt_version=prompt_version,
        input_hash=input_hash,
        created_at=_now(),
        latency_ms=latency_ms,
        token_usage=dict(token_usage or {}),
        validation_notes=list(validation_notes or []),
        error=error,
    )


def unevaluable_artifact(
    code: str,
    summary: str,
    *,
    provider: str = "openai-compatible",
    model: str = "",
    prompt_version: str = "v1",
    input_hash: str = "",
    latency_ms: int = 0,
    error: Optional[str] = None,
) -> LlmEvaluationArtifact:
    return artifact_from_evaluation(
        unevaluable_result(code, summary),
        provider=provider,
        model=model,
        prompt_version=prompt_version,
        input_hash=input_hash,
        latency_ms=latency_ms,
        error=error,
    )


def _coerce_code(value) -> Optional[str]:
    text = str(value or "").strip().upper().replace("-", "_").replace(" ", "_")
    if not text:
        return None
    if text in ISSUE_CODES:
        return text
    return ISSUE_CODE_ALIASES.get(text)


def coerce_payload(data) -> Tuple[dict, List[str]]:
    """把判分模型的实际输出掰进契约：别名映射、缺失补齐、越界收敛。

    宁可丢一个自造的问题码，也不要因为一个字段名把整题判成无法评估。
    """
    notes: List[str] = []
    if not isinstance(data, dict):
        return {}, ["判分返回的不是 JSON 对象"]
    out = {key: value for key, value in data.items() if key in TOP_LEVEL_FIELDS}
    dropped = [key for key in data if key not in TOP_LEVEL_FIELDS]
    if dropped:
        notes.append(f"忽略未知字段：{', '.join(str(item) for item in dropped[:5])}")

    verdict = str(out.get("overall_verdict") or "").strip().upper().replace("-", "_")
    out["overall_verdict"] = VERDICT_ALIASES.get(verdict, verdict)

    raw_codes = out.get("issue_codes") or []
    if isinstance(raw_codes, str):
        raw_codes = [raw_codes]
    codes: List[str] = []
    for code in raw_codes:
        mapped = _coerce_code(code)
        if mapped:
            codes.append(mapped)
        else:
            notes.append(f"问题码 {code} 不在白名单，已忽略")
    out["issue_codes"] = list(dict.fromkeys(codes))
    primary = _coerce_code(out.get("primary_issue_code"))
    if primary not in out["issue_codes"]:
        primary = out["issue_codes"][0] if out["issue_codes"] else None
    out["primary_issue_code"] = primary

    differences = []
    for item in out.get("differences") or []:
        if not isinstance(item, dict):
            continue
        mapped = _coerce_code(item.get("type"))
        if not mapped:
            notes.append(f"差异类型 {item.get('type')} 不在白名单，已忽略")
            continue
        item = dict(item)
        item["type"] = mapped
        severity = str(item.get("severity") or "ERROR").strip().upper()
        severity = SEVERITY_ALIASES.get(severity, severity)
        item["severity"] = severity if severity in {"ERROR", "WARNING"} else "ERROR"
        if item.get("explanation") is not None:
            item["explanation"] = str(item["explanation"])[:120]
        coordinate = item.get("coordinate") or {}
        item["coordinate"] = {str(key): str(value) for key, value in coordinate.items() if value is not None}
        differences.append(item)
    out["differences"] = differences

    dims = out.get("dimensions") if isinstance(out.get("dimensions"), dict) else {}
    present = [key for key in REQUIRED_DIMENSIONS if isinstance(dims.get(key), dict)]
    if len(present) < 4 and not out["differences"] and not out["issue_codes"]:
        # 维度、差异、问题码都没有，等于没有给出判断，按非法响应处理而不是凭空补成合格
        return data, notes + ["判分返回缺少维度与差异，无法作为有效结论"]
    fixed: Dict[str, Any] = {}
    valid_status = {status.value for status in DimensionStatus}
    for key in REQUIRED_DIMENSIONS:
        entry = dims.get(key)
        if isinstance(entry, dict):
            status = str(entry.get("status") or "").strip().upper().replace("-", "_")
            status = STATUS_ALIASES.get(status, status)
            fixed[key] = {
                "status": status if status in valid_status else "UNKNOWN",
                "reason": str(entry.get("reason") or ""),
            }
        else:
            fixed[key] = {"status": "UNKNOWN", "reason": ""}
            notes.append(f"维度 {key} 缺失，按 UNKNOWN 处理")
    extra = [key for key in dims if key not in REQUIRED_DIMENSIONS]
    if extra:
        notes.append(f"忽略多余维度：{', '.join(str(item) for item in extra[:5])}")
    out["dimensions"] = fixed

    try:
        confidence = float(out.get("confidence"))
    except (TypeError, ValueError):
        confidence = 0.8
        notes.append("confidence 缺失或非法，按 0.8 处理")
    out["confidence"] = min(1.0, max(0.0, confidence))
    out["summary"] = str(out.get("summary") or "")
    out["needs_human_review"] = bool(out.get("needs_human_review"))
    return out, notes


def _rate_status(rate: Optional[float], floor: float) -> DimensionStatus:
    if rate is None:
        return DimensionStatus.UNKNOWN
    if rate >= 1.0:
        return DimensionStatus.MATCH
    if rate >= floor:
        return DimensionStatus.PARTIAL
    return DimensionStatus.MISMATCH


def apply_reconciliation(contract, artifact: LlmEvaluationArtifact, report: Optional[dict]) -> LlmEvaluationArtifact:
    """用程序逐行比对结果覆盖结论。

    LLM 只保留"期间/粒度/单位/口径"这类语义判断；数字对错以程序为准。
    """
    from evaluator.reconcile import verdict_from_report

    verdict = verdict_from_report(report or {})
    if verdict is None:
        return artifact
    evaluation = artifact.evaluation
    notes = list(artifact.validation_notes)
    if any(code in VETO_CODES for code in evaluation.issue_codes):
        notes.append("程序复核未覆盖语义层判定的期间/粒度/单位/口径问题，保留 LLM 结论")
        return artifact.model_copy(update={"validation_notes": notes})

    row_rate = report.get("row_hit_rate")
    value_rate = report.get("value_hit_rate")
    dims = dict(evaluation.dimensions)
    dims["row_coverage"] = DimensionEvaluation(
        status=_rate_status(row_rate, 0.5),
        reason=f"程序核对：{report.get('matched_rows', 0)}/{report.get('sql_rows', 0)} 行命中",
    )
    if value_rate is not None:
        dims["numeric_accuracy"] = DimensionEvaluation(
            status=_rate_status(value_rate, 0.5),
            reason=f"程序核对：{report.get('hit_values', 0)}/{report.get('checked_values', 0)} 个数值在契约容差内",
        )

    codes = list(evaluation.issue_codes)
    if verdict == "QUALIFIED":
        codes = []
        primary = None
    elif verdict == "PARTIAL":
        support = []
        if report.get("missing_rows"):
            support.append("MISSING_ROW")
        if report.get("wrong_values"):
            support.append("WRONG_VALUE")
        if report.get("missing_values"):
            support.append("MISSING_FIELD")
        codes = support or codes or ["WRONG_VALUE"]
        primary = _prioritized(codes)
    else:
        if report.get("wrong_values") or (value_rate is not None and value_rate < 0.5):
            codes = list(dict.fromkeys(["WRONG_VALUE", *codes]))
        elif report.get("missing_rows"):
            codes = list(dict.fromkeys(["MISSING_ROW", *codes]))
        primary = _prioritized(codes)

    summary = evaluation.summary
    detail = (
        f"程序复核：行命中 {report.get('matched_rows', 0)}/{report.get('sql_rows', 0)}"
        f"，数值命中 {report.get('hit_values', 0)}/{report.get('checked_values', 0)}"
    )
    if verdict != evaluation.overall_verdict.value:
        notes.append(f"程序复核把结论从 {evaluation.overall_verdict.value} 调整为 {verdict}")
    updated = evaluation.model_copy(
        update={
            "overall_verdict": EvaluationVerdict(verdict),
            "issue_codes": codes,
            "primary_issue_code": primary,
            "dimensions": dims,
            "summary": f"{summary}（{detail}）" if summary else detail,
            # 程序给出了确定结论时，不再挂"待人工复核"
            "needs_human_review": evaluation.needs_human_review if verdict == "UNQUALIFIED" else False,
        }
    )
    return artifact.model_copy(update={"evaluation": updated, "validation_notes": notes})


def _resolve_measure(contract, field: str):
    if not field or contract is None:
        return None
    key = canonical_metric(field, contract.measures)
    if key and key in contract.measures:
        return contract.measures[key]
    return contract.measures.get(field)


def validate_numeric_differences(contract, artifact: LlmEvaluationArtifact) -> LlmEvaluationArtifact:
    """用契约容差复核 LLM 已定位的 WRONG_VALUE 差异，不重新扫描整张表。"""
    evaluation = artifact.evaluation
    notes = list(artifact.validation_notes)
    kept: List[EvaluationDifference] = []
    dims = {key: value.model_copy() for key, value in evaluation.dimensions.items()}
    needs_human_review = evaluation.needs_human_review
    confirmed_wrong = False
    removed_wrong = 0

    for item in evaluation.differences:
        if item.type != "WRONG_VALUE":
            kept.append(item)
            continue
        spec = _resolve_measure(contract, item.field)
        if spec is None:
            notes.append(f"指标“{item.field or '未命名'}”无法映射契约 measure，保留差异并转人工复核")
            needs_human_review = True
            kept.append(item)
            continue
        agent_value, _agent_unit, agent_error = parse_number(item.agent_value, spec, "agent")
        sql_value, _sql_unit, sql_error = parse_number(item.sql_value, spec, "sql")
        if agent_value is None or sql_value is None:
            reason = agent_error or sql_error or "数值无法解析"
            notes.append(f"指标“{item.field}”双方数值无法标准化（{reason}），保留差异并转人工复核")
            needs_human_review = True
            kept.append(item)
            continue
        close, delta, _rule = values_close(sql_value, agent_value, spec)
        if close:
            notes.append(f"指标“{item.field}”复核在契约容差内（差值 {delta:g}），已移除 WRONG_VALUE 差异")
            removed_wrong += 1
            continue
        confirmed_wrong = True
        kept.append(
            item.model_copy(
                update={
                    "normalized_agent_value": agent_value,
                    "normalized_sql_value": sql_value,
                    "delta": delta,
                }
            )
        )

    if confirmed_wrong and "numeric_accuracy" in dims:
        if dims["numeric_accuracy"].status != DimensionStatus.MISMATCH:
            notes.append("复核确认存在超出容差的错值，numeric_accuracy 已改为 MISMATCH")
            dims["numeric_accuracy"] = DimensionEvaluation(
                status=DimensionStatus.MISMATCH,
                reason=dims["numeric_accuracy"].reason or "程序复核确认数值超出契约容差",
            )

    issue_codes = list(evaluation.issue_codes)
    if removed_wrong and not confirmed_wrong:
        issue_codes = [code for code in issue_codes if code != "WRONG_VALUE"]
        if "numeric_accuracy" in dims and dims["numeric_accuracy"].status == DimensionStatus.MISMATCH:
            notes.append("复核后不再存在超出容差的错值，numeric_accuracy 已改为 MATCH")
            dims["numeric_accuracy"] = DimensionEvaluation(status=DimensionStatus.MATCH, reason="程序复核在契约容差内")

    updated = evaluation.model_copy(
        update={
            "differences": kept,
            "dimensions": dims,
            "issue_codes": issue_codes,
            "needs_human_review": needs_human_review,
        }
    )
    updated, decision_notes = enforce_decision(updated)
    notes.extend(decision_notes)
    return artifact.model_copy(update={"evaluation": updated, "validation_notes": notes})
