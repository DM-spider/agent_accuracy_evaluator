# -*- coding: utf-8 -*-
"""单题与批次评分。准确率与覆盖率分开。"""
from __future__ import annotations

from collections import defaultdict
from typing import Dict, List, Optional, Sequence

from evaluator.models import (
    CaseContract,
    CaseMetrics,
    CaseResult,
    CaseStatus,
    ClaimStatus,
    ComparisonItem,
    ErrorType,
    RunSummary,
    RunStatus,
    SceneStats,
)
from evaluator.verdict import (
    PARTIAL,
    QUALIFIED,
    UNEVALUABLE,
    UNQUALIFIED,
    classify_comparison,
)


MATCH_STATUSES = {ClaimStatus.MATCH, ClaimStatus.MATCH_WITH_TOLERANCE}


def _ratio(num: int, den: int) -> Optional[float]:
    if den <= 0:
        return None
    return round(num / den, 4)


def score_case(
    contract: CaseContract,
    items: List[ComparisonItem],
    *,
    not_scored_reason: Optional[str] = None,
    agent_error: Optional[str] = None,
    extract_failed: bool = False,
) -> CaseResult:
    error_types: List[str] = []
    if not contract.numeric_evaluable:
        return CaseResult(
            case_id=contract.case_id,
            question=contract.question,
            scene_big=contract.scene_big,
            result_type=contract.result_type,
            numeric_evaluable=False,
            status=CaseStatus.NOT_SCORED,
            not_scored_reason=not_scored_reason or "NON_NUMERIC",
            error_types=[ErrorType.NON_NUMERIC.value],
        )
    if not_scored_reason:
        mapped = {
            "SQL_FAIL": ErrorType.SQL_FAIL.value,
            "AGENT_FAIL": ErrorType.AGENT_FAIL.value,
            "SQL_NOT_REALTIME_READY": ErrorType.SQL_NOT_REALTIME_READY.value,
            "WATERMARK_CHANGED": ErrorType.WATERMARK_CHANGED.value,
            "NON_NUMERIC": ErrorType.NON_NUMERIC.value,
        }
        return CaseResult(
            case_id=contract.case_id,
            question=contract.question,
            scene_big=contract.scene_big,
            result_type=contract.result_type,
            numeric_evaluable=True,
            status=CaseStatus.NOT_SCORED,
            not_scored_reason=not_scored_reason,
            primary_failure=not_scored_reason,
            error_types=[mapped.get(not_scored_reason, not_scored_reason)],
        )

    match_n = sum(1 for i in items if i.status in MATCH_STATUSES)
    wrong_n = sum(1 for i in items if i.status is ClaimStatus.WRONG_VALUE)
    missing_n = sum(1 for i in items if i.status is ClaimStatus.MISSING)
    unexpected_n = sum(1 for i in items if i.status is ClaimStatus.UNEXPECTED)
    unparseable_n = sum(1 for i in items if i.status is ClaimStatus.UNPARSEABLE)
    required = [i for i in items if i.status is not ClaimStatus.UNEXPECTED and i.status is not ClaimStatus.UNPARSEABLE]
    returned_required = [i for i in required if i.status is not ClaimStatus.MISSING]
    sql_keys = {(tuple(sorted(i.coordinates.items())), i.metric) for i in required}
    agent_keys = {
        (tuple(sorted(i.coordinates.items())), i.metric)
        for i in items
        if i.status is not ClaimStatus.MISSING
    }
    sql_rows = {tuple(sorted(i.coordinates.items())) for i in required}
    agent_rows = {tuple(sorted(i.coordinates.items())) for i in items if i.status is not ClaimStatus.MISSING}

    accuracy_den = match_n + wrong_n
    coverage_den = len(required)
    row_den = len(sql_rows)
    metrics = CaseMetrics(
        match_count=match_n,
        wrong_count=wrong_n,
        missing_count=missing_n,
        unexpected_count=unexpected_n,
        unparseable_count=unparseable_n,
        accuracy=_ratio(match_n, accuracy_den),
        coverage=_ratio(len(returned_required), coverage_den),
        row_coverage=_ratio(len(sql_rows & agent_rows), row_den) if row_den else None,
        required_claims=coverage_den,
        returned_required=len(returned_required),
        auto_resolved=unparseable_n == 0 and not extract_failed,
    )

    classified = classify_comparison(items, contract)
    if wrong_n:
        error_types.append(ErrorType.WRONG_VALUE.value)
    if any(i.status is ClaimStatus.CALIBER_MISMATCH for i in items):
        error_types.append(ErrorType.CALIBER_MISMATCH.value)
    if classified["reason"] == "GRAIN_MISMATCH":
        error_types.append(ErrorType.GRAIN_MISMATCH.value)
    elif missing_n:
        error_types.append(ErrorType.MISSING.value)
    if unparseable_n:
        error_types.append(ErrorType.EXTRACT_FAIL.value)
    if extract_failed and not items:
        error_types.append(ErrorType.EXTRACT_FAIL.value)

    primary = None
    if extract_failed and not items:
        status = CaseStatus.REVIEW
        primary = "EXTRACT_FAIL"
    elif classified["verdict"] == UNEVALUABLE:
        status = CaseStatus.REVIEW
        primary = classified["reason"] or "EXTRACT_FAIL"
    elif classified["verdict"] == UNQUALIFIED:
        status = CaseStatus.FAIL
        primary = classified["reason"] or "WRONG_VALUE"
    elif classified["verdict"] == PARTIAL:
        status = CaseStatus.PARTIAL
        primary = "MISSING"
    elif classified["verdict"] == QUALIFIED:
        status = CaseStatus.PASS
    else:
        status = CaseStatus.REVIEW
        primary = classified["reason"] or "EXTRACT_FAIL"

    return CaseResult(
        case_id=contract.case_id,
        question=contract.question,
        scene_big=contract.scene_big,
        result_type=contract.result_type,
        numeric_evaluable=True,
        status=status,
        primary_failure=primary,
        error_types=error_types,
        metrics=metrics,
        comparison_items=items,
    )


def score_run(
    run_id: str,
    results: Sequence[CaseResult],
    *,
    agent_name: str = "",
    anchor_time: str = "",
    timezone_name: str = "Asia/Shanghai",
    watermark_stable: bool = True,
    status: RunStatus = RunStatus.COMPLETED,
    mode: str = "live",
    consistency_threshold: float = 0.6,
) -> RunSummary:
    scored = [r for r in results if r.status not in {CaseStatus.NOT_SCORED, CaseStatus.REVIEW}]
    not_scored = [r for r in results if r.status is CaseStatus.NOT_SCORED]
    by_status = defaultdict(int)
    by_error = defaultdict(int)
    for result in results:
        by_status[result.status.value] += 1
        for err in result.error_types:
            by_error[err] += 1

    match_n = sum(r.metrics.match_count for r in scored)
    wrong_n = sum(r.metrics.wrong_count for r in scored)
    unexpected_n = sum(r.metrics.unexpected_count for r in scored)
    returned = sum(r.metrics.returned_required for r in scored)
    required = sum(r.metrics.required_claims for r in scored)
    row_num = 0
    row_den = 0
    for result in scored:
        if result.metrics.row_coverage is None or result.metrics.required_claims == 0:
            continue
        # 用覆盖分子还原：row_coverage * sql_rows ≈ 已返回行
        sql_rows = {tuple(sorted(i.coordinates.items())) for i in result.comparison_items if i.status is not ClaimStatus.UNEXPECTED}
        agent_rows = {tuple(sorted(i.coordinates.items())) for i in result.comparison_items if i.status is not ClaimStatus.MISSING}
        row_den += len(sql_rows)
        row_num += len(sql_rows & agent_rows)

    auto = sum(1 for r in scored if r.numeric_evaluable and r.metrics.auto_resolved)
    numeric_n = sum(1 for r in results if r.numeric_evaluable)

    scene_bucket: Dict[str, List[CaseResult]] = defaultdict(list)
    for result in results:
        scene_bucket[result.scene_big or "未分类"].append(result)
    by_scene = []
    for scene, group in sorted(scene_bucket.items()):
        scene_scored = [r for r in group if r.status not in {CaseStatus.NOT_SCORED, CaseStatus.REVIEW}]
        passed = sum(1 for r in scene_scored if r.status is CaseStatus.PASS)
        m = sum(r.metrics.match_count for r in scene_scored)
        d = m + sum(r.metrics.wrong_count + r.metrics.unexpected_count for r in scene_scored)
        by_scene.append(
            SceneStats(
                scene=scene,
                scored=len(scene_scored),
                passed=passed,
                pass_rate=_ratio(passed, len(scene_scored)),
                accuracy=_ratio(m, d),
            )
        )

    pass_n = sum(1 for r in scored if r.status is CaseStatus.PASS)
    return RunSummary(
        run_id=run_id,
        status=status,
        anchor_time=anchor_time,
        timezone=timezone_name,
        agent_name=agent_name,
        total_cases=len(results),
        scored_cases=len(scored),
        not_scored_cases=len(not_scored),
        pass_cases=pass_n,
        partial_cases=sum(1 for r in scored if r.status is CaseStatus.PARTIAL),
        fail_cases=sum(1 for r in scored if r.status is CaseStatus.FAIL),
        review_cases=sum(1 for r in results if r.status is CaseStatus.REVIEW),
        case_pass_rate=_ratio(pass_n, len(scored)),
        numeric_accuracy=_ratio(match_n, match_n + wrong_n + unexpected_n),
        numeric_coverage=_ratio(returned, required),
        row_coverage=_ratio(row_num, row_den),
        unexpected_rate=_ratio(unexpected_n, match_n + wrong_n + unexpected_n + sum(r.metrics.missing_count for r in scored)),
        auto_parse_rate=_ratio(auto, numeric_n),
        watermark_stable=watermark_stable,
        by_scene=by_scene,
        by_status=dict(by_status),
        by_error_type=dict(by_error),
        progress_done=len(results),
        progress_total=len(results),
        mode=mode,
        consistency_threshold=consistency_threshold,
        timing_summary={
            "completed_agent_count": sum(r.completion_status == "completed" for r in results),
            "agent_completed_avg_ms": _average([r.agent_timings.get("completed_ms") for r in results]),
            "first_answer_avg_ms": _average([r.agent_timings.get("first_answer_ms") for r in results]),
            "sql_measured_avg_ms": _average([r.sql_latency_ms for r in results if r.sql_latency_ms > 0]),
        },
    )


def _average(values):
    measured = [v for v in values if v is not None]
    return round(sum(measured) / len(measured), 2) if measured else None
