# -*- coding: utf-8 -*-
from evaluator.llm_evaluation import artifact_from_evaluation
from evaluator.models import (
    REQUIRED_DIMENSIONS,
    CaseContract,
    CaseStatus,
    DimensionEvaluation,
    LlmEvaluationResult,
    MeasureSpec,
)
from evaluator.scorer import result_from_llm_evaluation, score_case, score_run


def contract(case_id="CX01", numeric=True, result_type="stat"):
    return CaseContract(
        case_id=case_id, question="q", result_type=result_type, numeric_evaluable=numeric,
        measures={"产销差率": MeasureSpec(label="产销差率", unit="%")},
    )


def artifact(verdict, codes=(), dims=None):
    dimensions = {key: DimensionEvaluation(status="MATCH") for key in REQUIRED_DIMENSIONS}
    for key, value in (dims or {}).items():
        dimensions[key] = DimensionEvaluation(status=value)
    result = LlmEvaluationResult(
        overall_verdict=verdict,
        confidence=0.9,
        summary="s",
        primary_issue_code=codes[0] if codes else None,
        issue_codes=list(codes),
        dimensions=dimensions,
        differences=[],
        needs_human_review=verdict == "UNEVALUABLE",
    )
    return artifact_from_evaluation(result, model="m", input_hash="h")


def test_non_numeric_is_not_scored():
    result = score_case(contract("BJ3", numeric=False, result_type="text"), not_scored_reason="NON_NUMERIC")
    assert result.status is CaseStatus.NOT_SCORED
    assert result.not_scored_reason == "NON_NUMERIC"


def test_execution_failure_is_not_scored():
    for reason in ("SQL_FAIL", "AGENT_FAIL", "WATERMARK_CHANGED"):
        result = score_case(contract(), not_scored_reason=reason)
        assert result.status is CaseStatus.NOT_SCORED
        assert result.primary_failure == reason
        assert result.error_types == [reason]


def test_llm_verdict_maps_to_case_status():
    qualified = result_from_llm_evaluation(contract(), artifact("QUALIFIED"))
    assert qualified.status is CaseStatus.PASS
    partial = result_from_llm_evaluation(contract(), artifact("PARTIAL", ["MISSING_ROW"], {"row_coverage": "PARTIAL"}))
    assert partial.status is CaseStatus.PARTIAL
    failed = result_from_llm_evaluation(contract(), artifact("UNQUALIFIED", ["WRONG_VALUE"], {"numeric_accuracy": "MISMATCH"}))
    assert failed.status is CaseStatus.FAIL
    assert failed.primary_failure == "WRONG_VALUE"
    assert failed.error_types == ["WRONG_VALUE"]
    review = result_from_llm_evaluation(contract(), artifact("UNEVALUABLE", ["LLM_CALL_FAILED"]))
    assert review.status is CaseStatus.REVIEW
    assert review.metrics.auto_resolved is False


def test_score_run_excludes_not_scored_and_review():
    numeric = contract("CX01")
    passed = result_from_llm_evaluation(numeric, artifact("QUALIFIED"))
    failed = result_from_llm_evaluation(contract("CX02"), artifact("UNQUALIFIED", ["WRONG_VALUE"], {"numeric_accuracy": "MISMATCH"}))
    review = result_from_llm_evaluation(contract("CX03"), artifact("UNEVALUABLE", ["LLM_CALL_FAILED"]))
    skipped = score_case(contract("BJ3", numeric=False, result_type="text"), not_scored_reason="NON_NUMERIC")
    summary = score_run("r", [passed, failed, review, skipped])
    assert summary.scored_cases == 2
    assert summary.not_scored_cases == 1
    assert summary.review_cases == 1
    assert summary.case_pass_rate == 0.5
    assert summary.pass_cases == 1 and summary.fail_cases == 1
