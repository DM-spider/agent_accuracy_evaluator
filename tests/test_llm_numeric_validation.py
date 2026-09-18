# -*- coding: utf-8 -*-
import pytest

from evaluator.llm_evaluation import validate_numeric_differences
from evaluator.models import (
    REQUIRED_DIMENSIONS,
    CaseContract,
    DimensionEvaluation,
    EvaluationDifference,
    LlmEvaluationResult,
    MeasureSpec,
)
from evaluator.llm_evaluation import artifact_from_evaluation


def contract(value_scale="percent", unit="%", kind="abs", eps=0.01):
    return CaseContract(
        case_id="CX01",
        question="q",
        result_type="stat",
        numeric_evaluable=True,
        measures={
            "产销差率": MeasureSpec(
                label="产销差率",
                unit=unit,
                value_scale=value_scale,
                sql_column="rate",
                tolerance={"kind": kind, "eps": eps},
            )
        },
    )


def dimensions(numeric="MISMATCH"):
    out = {key: DimensionEvaluation(status="MATCH") for key in REQUIRED_DIMENSIONS}
    out["numeric_accuracy"] = DimensionEvaluation(status=numeric)
    return out


def result_with(diff, numeric="MISMATCH", verdict="UNQUALIFIED", codes=("WRONG_VALUE",)):
    return LlmEvaluationResult(
        overall_verdict=verdict,
        confidence=0.9,
        summary="s",
        primary_issue_code=codes[0] if codes else None,
        issue_codes=list(codes),
        dimensions=dimensions(numeric),
        differences=[diff],
    )


def artifact(diff, **kwargs):
    return artifact_from_evaluation(result_with(diff, **kwargs), input_hash="h")


def wrong(**kwargs):
    data = {"type": "WRONG_VALUE", "field": "产销差率", "agent_value": "5.4%", "sql_value": "5.3%"}
    data.update(kwargs)
    return EvaluationDifference(**data)


def test_ratio01_and_percent_are_consistent():
    item = wrong(sql_value="0.053", agent_value="5.3%")
    out = validate_numeric_differences(contract(value_scale="ratio01", unit="%"), artifact(item))
    assert out.evaluation.differences == []
    assert any("容差" in note for note in out.validation_notes)


def test_wan_and_m3_are_consistent():
    c = CaseContract(
        case_id="CX01", question="q", result_type="stat", numeric_evaluable=True,
        measures={"水量": MeasureSpec(label="水量", unit="m³", value_scale="volume", sql_column="v")},
    )
    item = wrong(field="水量", agent_value="1 万 m³", sql_value="10000")
    out = validate_numeric_differences(c, artifact(item))
    assert out.evaluation.differences == []


def test_out_of_tolerance_difference_is_kept_with_normalized_values():
    out = validate_numeric_differences(contract(), artifact(wrong()))
    assert len(out.evaluation.differences) == 1
    kept = out.evaluation.differences[0]
    assert kept.normalized_agent_value == pytest.approx(5.4)
    assert kept.normalized_sql_value == pytest.approx(5.3)
    assert kept.delta == pytest.approx(0.1)
    assert out.evaluation.dimensions["numeric_accuracy"].status.value == "MISMATCH"


def test_within_tolerance_wrong_value_is_removed_and_verdict_corrected():
    item = wrong(agent_value="5.305%", sql_value="5.3%")
    out = validate_numeric_differences(contract(eps=0.01), artifact(item))
    assert out.evaluation.differences == []
    assert any("已移除" in note for note in out.validation_notes)
    assert out.evaluation.overall_verdict.value == "QUALIFIED"


def test_llm_claims_match_but_values_exceed_tolerance_marks_mismatch():
    item = wrong()
    out = validate_numeric_differences(contract(eps=0.01), artifact(item, numeric="MATCH", verdict="QUALIFIED"))
    assert out.evaluation.dimensions["numeric_accuracy"].status.value == "MISMATCH"
    assert out.evaluation.overall_verdict.value != "QUALIFIED"


def test_unmappable_field_is_not_guessed():
    item = wrong(field="未知指标")
    out = validate_numeric_differences(contract(), artifact(item))
    assert len(out.evaluation.differences) == 1
    assert out.evaluation.needs_human_review is True
    assert any("无法映射" in note for note in out.validation_notes)
