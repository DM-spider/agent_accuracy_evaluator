from evaluator.models import REQUIRED_DIMENSIONS
from evaluator.verdict import (
    NO_EVALUATION_CODE,
    QUALIFIED,
    UNEVALUABLE,
    UNQUALIFIED,
    case_judgment,
    dimension_counts,
    headline_from_metrics,
    parse_threshold,
)


def artifact(verdict="QUALIFIED", codes=(), dims=None, confidence=0.9, summary="结论"):
    dimensions = {key: {"status": "MATCH", "reason": ""} for key in REQUIRED_DIMENSIONS}
    for key, value in (dims or {}).items():
        dimensions[key] = value if isinstance(value, dict) else {"status": value, "reason": ""}
    return {
        "evaluation": {
            "schema_version": "v1",
            "overall_verdict": verdict,
            "confidence": confidence,
            "summary": summary,
            "primary_issue_code": codes[0] if codes else None,
            "issue_codes": list(codes),
            "dimensions": dimensions,
            "differences": [],
            "needs_human_review": False,
        },
        "provider": "test",
        "model": "model",
        "prompt_version": "v1",
        "input_hash": "h",
        "created_at": "t",
    }


def test_parse_threshold_percent_and_ratio():
    assert parse_threshold(None) == 1.0
    assert parse_threshold(0.6) == 0.6
    assert parse_threshold(60) == 0.6
    assert parse_threshold("60%") == 0.6
    assert parse_threshold(1) == 1.0


def test_auto_verdict_comes_from_llm_evaluation():
    judgment = case_judgment(artifact(UNQUALIFIED, ["WRONG_VALUE"], {"numeric_accuracy": "MISMATCH"}))
    assert judgment["auto_verdict"] == UNQUALIFIED
    assert judgment["final_verdict"] == UNQUALIFIED
    assert judgment["reason"] == "WRONG_VALUE"
    assert judgment["reason_label"] == "错值"
    assert judgment["evaluable"] is True
    assert judgment["has_evaluation"] is True
    assert judgment["dimensions"]["numeric_accuracy"]["status"] == "MISMATCH"


def test_manual_override_beats_llm_verdict():
    judgment = case_judgment(artifact(UNQUALIFIED, ["WRONG_VALUE"]), "合格")
    assert judgment["auto_verdict"] == UNQUALIFIED
    assert judgment["manual_verdict"] == QUALIFIED
    assert judgment["final_verdict"] == QUALIFIED
    assert judgment["qualified"] is True
    judgment = case_judgment(artifact(QUALIFIED), "无法评估")
    assert judgment["final_verdict"] == UNEVALUABLE
    assert judgment["evaluable"] is False


def test_missing_llm_evaluation_is_unevaluable_without_legacy_rules():
    judgment = case_judgment(None)
    assert judgment["auto_verdict"] == UNEVALUABLE
    assert judgment["final_verdict"] == UNEVALUABLE
    assert judgment["reason"] == NO_EVALUATION_CODE
    assert judgment["has_evaluation"] is False
    assert judgment["evaluable"] is False


def test_dimension_counts_ignore_unknown_and_na():
    judgments = [
        {"dimensions": {"period": {"status": "MATCH"}, "scope": {"status": "UNKNOWN"}}},
        {"dimensions": {"period": {"status": "MISMATCH"}, "scope": {"status": "NA"}}},
        {"dimensions": {"period": {"status": "UNKNOWN"}}},
    ]
    metrics = dimension_counts(judgments)
    assert metrics["period"]["matched"] == 1
    assert metrics["period"]["mismatched"] == 1
    assert metrics["period"]["evaluable"] == 2
    assert metrics["period"]["rate"] == 0.5
    assert metrics["scope"]["evaluable"] == 0
    assert metrics["scope"]["rate"] is None


def test_headline_from_metrics_shape():
    metrics = {
        "final": {"pass_rate": 0.5, "generated_questions": 2},
        "numeric": {"accuracy": 0.5, "qualified_questions": 1, "partial_questions": 0},
        "assessability": {"rate": 0.6, "evaluable_questions": 3},
        "total_questions": 5,
    }
    headline = headline_from_metrics(metrics)
    assert headline["pass_rate"] == 0.5
    assert headline["accuracy"] == 0.5
    assert headline["assessability"] == 0.6
    assert headline["total_questions"] == 5
