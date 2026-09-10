# -*- coding: utf-8 -*-
from datetime import datetime, timezone, timedelta

import pytest

from evaluator.models import (
    CaseContract,
    CaseResult,
    CaseStatus,
    ClaimStatus,
    ComparisonItem,
    CoveragePolicy,
    MeasureSpec,
    NumericClaim,
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
        last_7_start="2026-08-25",
        last_7_end="2026-08-31",
        last_30_start="2026-08-02",
        last_30_end="2026-08-31",
        year_start="2026-01-01",
        today="2026-08-31",
        yesterday="2026-08-30",
        week_start="2026-08-31",
        jan_may_period="202605",
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


def test_numeric_claim_and_comparison_item():
    claim = NumericClaim(
        case_id="CX04",
        source="agent",
        coordinates={"period": "202607", "organization": "南山分公司"},
        metric="actual_rate",
        raw_value="11.84%",
        value=11.84,
        unit="%",
        evidence="南山分公司实际产销差率为11.84%",
        extractor="markdown_table",
    )
    item = ComparisonItem(
        coordinates=claim.coordinates,
        metric=claim.metric,
        expected_value=11.84,
        actual_value=11.40,
        delta=-0.44,
        tolerance=0.01,
        status=ClaimStatus.WRONG_VALUE,
        evidence_claim_id="claim_123",
        evidence=claim.evidence,
    )
    assert claim.source == "agent"
    assert item.status is ClaimStatus.WRONG_VALUE


def test_case_result_and_run_summary_defaults():
    result = CaseResult(case_id="CX01", status=CaseStatus.PASS)
    summary = RunSummary(run_id="r1", total_cases=73, scored_cases=55, not_scored_cases=18)
    assert result.status is CaseStatus.PASS
    assert summary.not_scored_cases == 18
    with pytest.raises(Exception):
        NumericClaim(case_id="X", source="llm", metric="rate")
