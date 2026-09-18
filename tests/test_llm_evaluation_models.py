# -*- coding: utf-8 -*-
import pytest
from pydantic import ValidationError

from evaluator.llm_evaluation import enforce_decision, unevaluable_result, validate_decision
from evaluator.models import (
    REQUIRED_DIMENSIONS,
    DimensionEvaluation,
    EvaluationDifference,
    LlmEvaluationResult,
)


def dimensions(status="MATCH", **overrides):
    out = {key: DimensionEvaluation(status=status) for key in REQUIRED_DIMENSIONS}
    for key, value in overrides.items():
        out[key] = value if isinstance(value, DimensionEvaluation) else DimensionEvaluation(status=value)
    return out


def make_result(verdict="QUALIFIED", codes=(), dims=None, diffs=(), confidence=0.9):
    return LlmEvaluationResult(
        overall_verdict=verdict,
        confidence=confidence,
        summary="summary",
        primary_issue_code=codes[0] if codes else None,
        issue_codes=list(codes),
        dimensions=dims or dimensions(),
        differences=list(diffs),
    )


def test_complete_seven_dimension_result_validates():
    result = make_result()
    assert tuple(result.dimensions) == REQUIRED_DIMENSIONS
    assert result.overall_verdict.value == "QUALIFIED"


def test_missing_any_dimension_is_rejected():
    for missing in REQUIRED_DIMENSIONS:
        dims = dimensions()
        dims.pop(missing)
        with pytest.raises(ValidationError):
            make_result(dims=dims)


def test_extra_dimension_is_rejected():
    dims = dimensions()
    dims["extra"] = DimensionEvaluation(status="MATCH")
    with pytest.raises(ValidationError):
        make_result(dims=dims)


@pytest.mark.parametrize("confidence", [-0.1, 1.1])
def test_confidence_out_of_range_is_rejected(confidence):
    with pytest.raises(ValidationError):
        make_result(confidence=confidence)


def test_unknown_verdict_status_and_issue_code_are_rejected():
    with pytest.raises(ValidationError):
        make_result(verdict="PASS")
    with pytest.raises(ValidationError):
        make_result(dims=dimensions(period="OK"))
    with pytest.raises(ValidationError):
        make_result(codes=("NOT_A_CODE",))
    with pytest.raises(ValidationError):
        EvaluationDifference(type="NOT_A_CODE")


def test_partial_requires_missing_support():
    dims = dimensions(field_coverage="PARTIAL")
    result = make_result(verdict="PARTIAL", codes=("WRONG_VALUE",), dims=dims)
    assert any("PARTIAL" in note for note in validate_decision(result))


def test_partial_with_missing_row_is_consistent():
    dims = dimensions(row_coverage="PARTIAL")
    result = make_result(verdict="PARTIAL", codes=("MISSING_ROW",), dims=dims)
    assert validate_decision(result) == []


def test_qualified_rejects_error_differences():
    diff = EvaluationDifference(type="WRONG_VALUE", field="产销差率", agent_value="1", sql_value="2")
    result = make_result(diffs=(diff,))
    assert any("QUALIFIED" in note for note in validate_decision(result))
    corrected, notes = enforce_decision(result)
    assert corrected.overall_verdict.value != "QUALIFIED"
    assert notes


def test_primary_issue_follows_fixed_priority():
    dims = dimensions()
    dims["period"] = DimensionEvaluation(status="MISMATCH")
    dims["numeric_accuracy"] = DimensionEvaluation(status="MISMATCH")
    result = make_result(verdict="UNQUALIFIED", codes=("WRONG_VALUE", "PERIOD_MISMATCH"), dims=dims)
    corrected, notes = enforce_decision(result)
    assert corrected.primary_issue_code == "PERIOD_MISMATCH"
    assert notes


def test_unevaluable_result_has_all_unknown_dimensions():
    result = unevaluable_result("INSUFFICIENT_EVIDENCE", "证据不足")
    assert result.overall_verdict.value == "UNEVALUABLE"
    assert result.needs_human_review is True
    assert {value.status.value for value in result.dimensions.values()} == {"UNKNOWN"}
