# -*- coding: utf-8 -*-
from datetime import datetime, timezone, timedelta

import pytest

from evaluator.models import (
    CaseContract,
    CaseMetrics,
    CaseResult,
    CaseStatus,
    CoveragePolicy,
    DimensionEvaluation,
    LlmEvaluationArtifact,
    LlmEvaluationResult,
    MeasureSpec,
    RunContext,
    RunSummary,
    Tolerance,
)


def _ctx(**kwargs) -> RunContext:
    payload = dict(
        run_id="20260831_100000_a1b2",
        anchor_time=datetime(2026, 8, 31, 10, 0, tzinfo=timezone(timedelta(hours=8))),
        current_month="202608",
        previous_month="202607",
        current_month_start="2026-08-01",
        next_month_start="2026-09-01",
        current_month_end="2026-08-31",
        previous_month_start="2026-07-01",
        previous_month_end="2026-07-31",
        year_start="2026-01-01",
        today="2026-08-31",
        yesterday="2026-08-30",
        yoy_month="202508",
    )
    payload.update(kwargs)
    return RunContext(**payload)


def test_run_context_requires_anchor_and_period_fields():
    ctx = _ctx()
    assert ctx.timezone == "Asia/Shanghai"
    assert ctx.current_month == "202608"
    with pytest.raises(Exception):
        RunContext(run_id="x")  # type: ignore[call-arg]


def test_case_contract_marks_review_when_numeric_fields_missing():
    contract = CaseContract(
        case_id="CX01",
        question="集团本月产销差率是多少？",
        result_type="stat",
        numeric_evaluable=True,
    )
    assert "sql" in contract.review_required
    assert "measures" in contract.review_required


def test_case_contract_accepts_stat_measures():
    contract = CaseContract(
        case_id="CX01",
        question="集团本月产销差率是多少？供水量、售水量各多少？",
        result_type="stat",
        numeric_evaluable=True,
        realtime_ready=True,
        sql_template="SELECT 1 WHERE businessyearmonth=:period",
        parameter_resolver="single_month",
        measures={
            "产销差率": MeasureSpec(
                label="产销差率",
                aliases=["实际率"],
                unit="%",
                tolerance=Tolerance(kind="abs", eps=0.01),
                value_scale="percent",
            )
        },
        coverage_policy=CoveragePolicy(mode="all_fields", minimum=1.0),
    )
    assert contract.numeric_evaluable
    assert contract.measures["产销差率"].tolerance.eps == 0.01
    assert not contract.review_required


def test_case_result_and_run_summary_defaults():
    result = CaseResult(case_id="CX01", status=CaseStatus.PASS, metrics=CaseMetrics(accuracy=1.0))
    summary = RunSummary(run_id="r1", total_cases=73, scored_cases=55, not_scored_cases=18)
    assert result.status is CaseStatus.PASS
    assert result.metrics.accuracy == 1.0
    assert summary.not_scored_cases == 18


def test_llm_evaluation_result_roundtrip():
    from evaluator.llm_evaluation import artifact_from_evaluation
    from evaluator.models import REQUIRED_DIMENSIONS

    result = LlmEvaluationResult(
        overall_verdict="UNEVALUABLE",
        confidence=0.2,
        summary="证据不足",
        primary_issue_code="INSUFFICIENT_EVIDENCE",
        issue_codes=["INSUFFICIENT_EVIDENCE"],
        dimensions={key: DimensionEvaluation(status="UNKNOWN") for key in REQUIRED_DIMENSIONS},
        differences=[],
        needs_human_review=True,
    )
    artifact = artifact_from_evaluation(result, model="m", input_hash="h")
    assert isinstance(artifact, LlmEvaluationArtifact)
    assert artifact.evaluation.needs_human_review is True
    assert artifact.created_at
