# -*- coding: utf-8 -*-
"""单题与批次评分。

正常数值题的唯一自动结论来自 LLM 评估；本模块只负责：
1. 执行失败题（SQL_FAIL / AGENT_FAIL / WATERMARK_CHANGED / NON_NUMERIC）的构造；
2. 把 LlmEvaluationArtifact 映射成 CaseResult；
3. 批次级运行摘要。
"""
from __future__ import annotations

from collections import defaultdict
from typing import Dict, List, Optional, Sequence

from evaluator.models import (
    CaseContract,
    CaseMetrics,
    CaseResult,
    CaseStatus,
    ErrorType,
    EvaluationVerdict,
    LlmEvaluationArtifact,
    RunStatus,
    RunSummary,
    SceneStats,
)

VERDICT_TO_STATUS = {
    EvaluationVerdict.QUALIFIED: CaseStatus.PASS,
    EvaluationVerdict.PARTIAL: CaseStatus.PARTIAL,
    EvaluationVerdict.UNQUALIFIED: CaseStatus.FAIL,
    EvaluationVerdict.UNEVALUABLE: CaseStatus.REVIEW,
}

NOT_SCORED_ERROR_TYPES = {
    "SQL_FAIL": ErrorType.SQL_FAIL.value,
    "AGENT_FAIL": ErrorType.AGENT_FAIL.value,
    "SQL_NOT_REALTIME_READY": ErrorType.SQL_NOT_REALTIME_READY.value,
    "WATERMARK_CHANGED": ErrorType.WATERMARK_CHANGED.value,
    "NON_NUMERIC": ErrorType.NON_NUMERIC.value,
}


def _ratio(num: int, den: int) -> Optional[float]:
    if den <= 0:
        return None
    return round(num / den, 4)


def score_case(
    contract: CaseContract,
    *,
    not_scored_reason: Optional[str] = None,
) -> CaseResult:
    """构造非数值题与执行失败题的结果。"""
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
    reason = not_scored_reason or "EXECUTION_FAILED"
    return CaseResult(
        case_id=contract.case_id,
        question=contract.question,
        scene_big=contract.scene_big,
        result_type=contract.result_type,
        numeric_evaluable=True,
        status=CaseStatus.NOT_SCORED,
        not_scored_reason=reason,
        primary_failure=reason,
        error_types=[NOT_SCORED_ERROR_TYPES.get(reason, reason)],
    )


def result_from_llm_evaluation(contract: CaseContract, artifact: LlmEvaluationArtifact) -> CaseResult:
    """CaseResult.status 完全由 LLM overall_verdict 映射，不叠加规则结论。"""
    evaluation = artifact.evaluation
    status = VERDICT_TO_STATUS[evaluation.overall_verdict]
    if evaluation.overall_verdict == EvaluationVerdict.QUALIFIED:
        accuracy: Optional[float] = 1.0
    elif evaluation.overall_verdict in {EvaluationVerdict.PARTIAL, EvaluationVerdict.UNQUALIFIED}:
        accuracy = 0.0
    else:
        accuracy = None
    return CaseResult(
        case_id=contract.case_id,
        question=contract.question,
        scene_big=contract.scene_big,
        result_type=contract.result_type,
        numeric_evaluable=True,
        status=status,
        primary_failure=evaluation.primary_issue_code,
        error_types=list(dict.fromkeys(evaluation.issue_codes)),
        metrics=CaseMetrics(
            accuracy=accuracy,
            auto_resolved=status is not CaseStatus.REVIEW,
        ),
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
    pass_n = sum(1 for r in scored if r.status is CaseStatus.PASS)

    scene_bucket: Dict[str, List[CaseResult]] = defaultdict(list)
    for result in results:
        scene_bucket[result.scene_big or "未分类"].append(result)
    by_scene = []
    for scene, group in sorted(scene_bucket.items()):
        scene_scored = [r for r in group if r.status not in {CaseStatus.NOT_SCORED, CaseStatus.REVIEW}]
        passed = sum(1 for r in scene_scored if r.status is CaseStatus.PASS)
        by_scene.append(
            SceneStats(
                scene=scene,
                scored=len(scene_scored),
                passed=passed,
                pass_rate=_ratio(passed, len(scene_scored)),
                accuracy=None,
            )
        )

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
        numeric_accuracy=None,
        numeric_coverage=None,
        row_coverage=None,
        unexpected_rate=None,
        auto_parse_rate=None,
        watermark_stable=watermark_stable,
        by_scene=by_scene,
        by_status=dict(by_status),
        by_error_type=dict(by_error),
        progress_done=len(results),
        progress_total=len(results),
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
