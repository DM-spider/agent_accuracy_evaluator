# -*- coding: utf-8 -*-
"""LLM 结构化评估契约：固定维度、问题码、决策校验与数值复核。

本模块只约束 LLM 输出的一致性，不重新构造规则比较结论。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

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
]

PARTIAL_SUPPORT_CODES = {"MISSING_FIELD", "MISSING_ROW"}
HARD_MISMATCH_DIMENSIONS = {"period", "scope", "grain", "unit_caliber", "numeric_accuracy"}
LLM_FAILURE_CODES = {"LLM_CALL_FAILED", "LLM_RESPONSE_INVALID", "LLM_INPUT_TOO_LARGE"}


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
        if codes:
            notes.append("QUALIFIED 不允许包含问题码")
    if result.overall_verdict == EvaluationVerdict.PARTIAL:
        if not set(codes) & PARTIAL_SUPPORT_CODES:
            notes.append("PARTIAL 只能由 MISSING_FIELD 或 MISSING_ROW 支撑")
        if error_codes:
            notes.append("PARTIAL 不允许存在 ERROR 级差异")
        if hard_mismatch:
            notes.append("存在 MISMATCH 维度时不能是 PARTIAL")
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
        verdict = EvaluationVerdict.UNQUALIFIED if (error_diffs or hard_mismatch or codes) else EvaluationVerdict.UNEVALUABLE
    elif verdict == EvaluationVerdict.PARTIAL:
        if error_diffs or hard_mismatch:
            verdict = EvaluationVerdict.UNQUALIFIED
        elif not codes & PARTIAL_SUPPORT_CODES:
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
