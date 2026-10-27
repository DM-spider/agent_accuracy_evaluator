# -*- coding: utf-8 -*-
"""判分模型输出不规矩时，平台应该收敛而不是整题作废。"""
from evaluator.llm_evaluation import apply_reconciliation, coerce_payload, enforce_decision
from evaluator.models import (
    REQUIRED_DIMENSIONS,
    DimensionEvaluation,
    EvaluationDifference,
    LlmEvaluationResult,
    MeasureSpec,
    CaseContract,
)
from evaluator.llm_evaluation import artifact_from_evaluation


def _payload(**overrides):
    base = {
        "overall_verdict": "UNQUALIFIED",
        "confidence": 0.9,
        "summary": "ok",
        "issue_codes": [],
        "primary_issue_code": None,
        "dimensions": {key: {"status": "MATCH", "reason": ""} for key in REQUIRED_DIMENSIONS},
        "differences": [],
        "needs_human_review": False,
    }
    base.update(overrides)
    return base


def test_unknown_issue_codes_are_mapped_not_rejected():
    data, notes = coerce_payload(
        _payload(issue_codes=["VALUE_MISMATCH", "ROW_COVERAGE_MISMATCH"], primary_issue_code="VALUE_MISMATCH")
    )
    assert data["issue_codes"] == ["WRONG_VALUE", "MISSING_ROW"]
    assert data["primary_issue_code"] in {"WRONG_VALUE", "MISSING_ROW"}
    result = LlmEvaluationResult.model_validate(data)
    assert result.overall_verdict.value == "UNQUALIFIED"
    assert notes == []


def test_unknown_codes_are_dropped_with_a_note():
    data, notes = coerce_payload(_payload(issue_codes=["TOTALLY_MADE_UP"], primary_issue_code="TOTALLY_MADE_UP"))
    assert data["issue_codes"] == []
    assert data["primary_issue_code"] is None
    assert any("TOTALLY_MADE_UP" in note for note in notes)


def test_difference_type_is_mapped_and_explanation_truncated():
    data, _notes = coerce_payload(
        _payload(
            issue_codes=["WRONG_VALUE"],
            primary_issue_code="WRONG_VALUE",
            differences=[{"type": "ROW_COVERAGE_MISMATCH", "severity": "MAJOR", "field": "组织", "explanation": "很长" * 100}],
        )
    )
    assert data["differences"][0]["type"] == "MISSING_ROW"
    assert data["differences"][0]["severity"] == "ERROR"
    assert len(data["differences"][0]["explanation"]) == 120


def test_missing_dimensions_are_filled_when_other_evidence_exists():
    data, notes = coerce_payload(
        _payload(dimensions={}, issue_codes=["WRONG_VALUE"], primary_issue_code="WRONG_VALUE")
    )
    assert set(data["dimensions"]) == set(REQUIRED_DIMENSIONS)
    assert all(item["status"] == "UNKNOWN" for item in data["dimensions"].values())
    assert notes


def test_empty_judgment_is_not_padded_into_qualified():
    # 只剩一个 overall_verdict，没有维度、差异、问题码：应当按非法响应处理
    data, notes = coerce_payload({"overall_verdict": "QUALIFIED"})
    assert "dimensions" not in data
    assert any("无法作为有效结论" in note for note in notes)


def test_verdict_aliases_and_confidence_clamp():
    data, _ = coerce_payload(_payload(overall_verdict="FAIL", confidence=1.7))
    assert data["overall_verdict"] == "UNQUALIFIED"
    assert data["confidence"] == 1.0


def _contract():
    return CaseContract(
        case_id="T-001",
        question="q",
        result_type="detail",
        numeric_evaluable=True,
        row_key=["组织"],
        measures={"x": MeasureSpec(label="x", unit="m³", value_scale="volume")},
    )


def _artifact(verdict="UNEVALUABLE", codes=()):
    dims = {key: DimensionEvaluation(status="UNKNOWN") for key in REQUIRED_DIMENSIONS}
    result = LlmEvaluationResult(
        overall_verdict=verdict,
        confidence=0.5,
        summary="判分返回非法结构",
        primary_issue_code=codes[0] if codes else None,
        issue_codes=list(codes),
        dimensions=dims,
    )
    return artifact_from_evaluation(result).model_copy(update={"created_at": "2026-10-27T00:00:00"})


def test_reconciliation_rescues_unevaluable_verdict():
    report = {
        "available": True,
        "sql_rows": 5,
        "matched_rows": 5,
        "checked_values": 15,
        "hit_values": 13,
        "row_hit_rate": 1.0,
        "value_hit_rate": 0.8667,
        "missing_rows": [],
        "wrong_values": [{"key": "202605", "field": "供水量"}],
        "missing_values": [],
    }
    artifact = apply_reconciliation(_contract(), _artifact(), report)
    assert artifact.evaluation.overall_verdict.value == "PARTIAL"
    assert artifact.evaluation.dimensions["row_coverage"].status.value == "MATCH"
    assert artifact.evaluation.dimensions["numeric_accuracy"].status.value == "PARTIAL"
    assert "程序复核" in artifact.evaluation.summary


def test_reconciliation_is_blocked_by_caliber_veto():
    report = {
        "available": True,
        "sql_rows": 5,
        "matched_rows": 5,
        "checked_values": 5,
        "hit_values": 5,
        "row_hit_rate": 1.0,
        "value_hit_rate": 1.0,
        "missing_rows": [],
        "wrong_values": [],
        "missing_values": [],
    }
    artifact = apply_reconciliation(_contract(), _artifact("UNQUALIFIED", ("CALIBER_MISMATCH",)), report)
    assert artifact.evaluation.overall_verdict.value == "UNQUALIFIED"
    assert any("保留 LLM 结论" in note for note in artifact.validation_notes)


def test_reconciliation_clears_codes_on_full_hit():
    report = {
        "available": True,
        "sql_rows": 2,
        "matched_rows": 2,
        "checked_values": 2,
        "hit_values": 2,
        "row_hit_rate": 1.0,
        "value_hit_rate": 1.0,
        "missing_rows": [],
        "wrong_values": [],
        "missing_values": [],
    }
    artifact = apply_reconciliation(_contract(), _artifact("PARTIAL", ("WRONG_VALUE",)), report)
    assert artifact.evaluation.overall_verdict.value == "QUALIFIED"
    assert artifact.evaluation.issue_codes == []
    assert artifact.evaluation.primary_issue_code is None


def test_reconciliation_skips_when_not_comparable():
    artifact = apply_reconciliation(_contract(), _artifact(), {"available": False, "reason": "no_table"})
    assert artifact.evaluation.overall_verdict.value == "UNEVALUABLE"
