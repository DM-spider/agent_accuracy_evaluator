# -*- coding: utf-8 -*-
from evaluator.models import CaseContract, CaseStatus, ClaimStatus, ComparisonItem, CoveragePolicy, MeasureSpec, Tolerance
from evaluator.scorer import score_case, score_run


def test_accuracy_100_coverage_low_is_not_pass():
    contract = CaseContract(
        case_id="LS4",
        question="高漏损DMA",
        result_type="detail",
        numeric_evaluable=True,
        measures={"rate": MeasureSpec(label="rate", unit="%", tolerance=Tolerance())},
        coverage_policy=CoveragePolicy(mode="all_rows", minimum=1.0),
    )
    items = [
        ComparisonItem(metric="rate", coordinates={"dma": str(i)}, expected_value=1, actual_value=1 if i < 10 else None, status=ClaimStatus.MATCH if i < 10 else ClaimStatus.MISSING)
        for i in range(13)
    ]
    result = score_case(contract, items)
    assert result.metrics.accuracy == 1.0
    assert result.metrics.coverage == round(10 / 13, 4)
    assert result.status is CaseStatus.PARTIAL


def test_extra_summary_does_not_fail_complete_detail():
    contract = CaseContract(
        case_id="CX-013",
        question="列出各单位产销差率",
        result_type="detail",
        numeric_evaluable=True,
        row_key=["组织"],
        measures={"产销差率": MeasureSpec(label="产销差率")},
        coverage_policy=CoveragePolicy(mode="all_rows", minimum=1.0),
    )
    items = [
        ComparisonItem(metric="产销差率", coordinates={"组织": "盐田分公司"}, expected_value=1, actual_value=1, status=ClaimStatus.MATCH),
        ComparisonItem(metric="产销差率", coordinates={"组织": "罗湖分公司"}, expected_value=1, actual_value=1, status=ClaimStatus.MATCH),
        ComparisonItem(metric="平均产销差率", coordinates={}, actual_value=1, status=ClaimStatus.UNEXPECTED),
    ]
    result = score_case(contract, items)
    assert result.status is CaseStatus.PASS
    assert result.metrics.accuracy == 1.0


def test_detail_only_summary_is_grain_mismatch_fail():
    contract = CaseContract(
        case_id="CX-013",
        question="列出各单位产销差率",
        result_type="detail",
        numeric_evaluable=True,
        row_key=["组织"],
        measures={"产销差率": MeasureSpec(label="产销差率")},
        coverage_policy=CoveragePolicy(mode="all_rows", minimum=1.0),
    )
    items = [
        ComparisonItem(metric="产销差率", coordinates={"组织": "盐田分公司"}, expected_value=1, status=ClaimStatus.MISSING),
        ComparisonItem(metric="产销差率", coordinates={"组织": "罗湖分公司"}, expected_value=1, status=ClaimStatus.MISSING),
        ComparisonItem(metric="平均产销差率", coordinates={}, actual_value=8.2, status=ClaimStatus.UNEXPECTED),
    ]
    result = score_case(contract, items)
    assert result.status is CaseStatus.FAIL
    assert result.primary_failure == "GRAIN_MISMATCH"
    assert "GRAIN_MISMATCH" in result.error_types


def test_non_numeric_excluded_from_pass_rate():
    numeric = CaseContract(case_id="CX01", question="q", result_type="stat", numeric_evaluable=True, measures={"a": MeasureSpec(label="a")})
    text = CaseContract(case_id="BJ3", question="q", result_type="text", numeric_evaluable=False)
    r1 = score_case(numeric, [ComparisonItem(metric="a", expected_value=1, actual_value=1, status=ClaimStatus.MATCH)])
    r2 = score_case(text, [])
    summary = score_run("r", [r1, r2])
    assert summary.scored_cases == 1
    assert summary.not_scored_cases == 1
    assert summary.case_pass_rate == 1.0
    assert r2.status is CaseStatus.NOT_SCORED
