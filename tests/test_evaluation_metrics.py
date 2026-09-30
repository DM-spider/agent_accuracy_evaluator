# -*- coding: utf-8 -*-
import json

from evaluator.evaluation_metrics import run_evaluation_metrics
from evaluator.models import REQUIRED_DIMENSIONS, CaseContract, CoveragePolicy, MeasureSpec


def contract(case_id):
    return CaseContract(
        case_id=case_id, question="q", result_type="stat", numeric_evaluable=True,
        row_key=[], measures={"水量": MeasureSpec(label="水量", sql_column="value")},
        coverage_policy=CoveragePolicy(minimum=1.0),
    )


class Repo:
    runs_dir = "."

    def __init__(self):
        self.cases = [
            {"case_id": "A", "status": "PASS", "completion_status": "completed"},
            {"case_id": "B", "status": "REVIEW", "completion_status": "completed"},
            {"case_id": "C", "status": "NOT_SCORED", "completion_status": "failed"},
        ]
        self.threshold = 0.6

    def list_cases(self, _run_id):
        return self.cases

    def get_run(self, _run_id):
        return {"consistency_threshold": self.threshold}


def llm_payload(verdict="QUALIFIED", codes=(), dims=None, differences=None):
    dimensions = {key: {"status": "MATCH", "reason": ""} for key in REQUIRED_DIMENSIONS}
    for key, value in (dims or {}).items():
        dimensions[key] = {"status": value, "reason": ""} if isinstance(value, str) else value
    return {
        "evaluation": {
            "schema_version": "v1",
            "overall_verdict": verdict,
            "confidence": 0.9,
            "summary": "summary",
            "primary_issue_code": codes[0] if codes else None,
            "issue_codes": list(codes),
            "dimensions": dimensions,
            "differences": list(differences or []),
            "needs_human_review": False,
        },
        "provider": "test",
        "model": "model",
        "prompt_version": "v1",
        "input_hash": "h",
        "created_at": "t",
        "latency_ms": 0,
        "token_usage": {},
        "validation_notes": [],
        "error": None,
    }


def _write_case(tmp_path, case_id, payload=None, agent_text="水量为1"):
    case_dir = tmp_path / "r" / "cases" / case_id
    case_dir.mkdir(parents=True)
    if agent_text is not None:
        (case_dir / "agent_answer.json").write_text(
            json.dumps({"text": agent_text}, ensure_ascii=False), encoding="utf-8")
    if payload is not None:
        (case_dir / "llm_evaluation.json").write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_pass_rate_denominator_is_fixed_planned_count(tmp_path):
    """通过率分母 = 开跑即固定的计划题数（contracts_snapshot），不随已完成题数增长。"""
    repo = Repo()
    repo.cases = [
        {"case_id": "A", "status": "PASS", "completion_status": "completed"},
        {"case_id": "B", "status": "FAIL", "completion_status": "completed"},
    ]
    repo.runs_dir = tmp_path
    _write_case(tmp_path, "A", llm_payload("QUALIFIED"))
    _write_case(tmp_path, "B", llm_payload("UNQUALIFIED", ["WRONG_VALUE"], {"numeric_accuracy": "MISMATCH"}))
    snapshot = tmp_path / "r" / "contracts_snapshot.json"
    snapshot.write_text(
        json.dumps([contract(cid).model_dump(mode="json") for cid in ["A", "B", "C", "D", "E"]], ensure_ascii=False),
        encoding="utf-8")
    metrics = run_evaluation_metrics(repo, "r", [contract("A"), contract("B")])
    assert metrics["total_questions"] == 5
    assert metrics["planned_questions"] == 5
    assert metrics["final"]["generated_questions"] == 2
    assert metrics["final"]["pass_rate"] == 0.4


def test_run_metrics_uses_llm_verdicts(tmp_path):
    repo = Repo()
    repo.runs_dir = tmp_path
    _write_case(tmp_path, "A", llm_payload("QUALIFIED"))
    _write_case(tmp_path, "B", llm_payload("UNQUALIFIED", ["WRONG_VALUE"], {"numeric_accuracy": "MISMATCH"}))
    _write_case(tmp_path, "C", agent_text=None)
    metrics = run_evaluation_metrics(repo, "r", [contract("A"), contract("B"), contract("C")])
    assert metrics["final"]["generated_questions"] == 2
    assert metrics["numeric"]["accuracy"] == 0.5
    assert metrics["numeric"]["qualified_questions"] == 1
    assert metrics["numeric"]["evaluable_questions"] == 2
    assert metrics["assessability"]["rate"] == round(2 / 3, 4)
    assert metrics["case_judgments"]["A"]["final_verdict"] == "QUALIFIED"
    assert metrics["case_judgments"]["B"]["final_verdict"] == "UNQUALIFIED"
    assert metrics["case_judgments"]["C"]["final_verdict"] == "UNEVALUABLE"
    assert metrics["case_judgments"]["C"]["has_evaluation"] is False
    assert "category_pass_rate" in metrics


def test_dimension_metrics_and_issue_distribution(tmp_path):
    repo = Repo()
    repo.cases = [
        {"case_id": "A", "status": "PASS", "completion_status": "completed"},
        {"case_id": "B", "status": "FAIL", "completion_status": "completed"},
        {"case_id": "C", "status": "FAIL", "completion_status": "completed"},
    ]
    repo.runs_dir = tmp_path
    _write_case(tmp_path, "A", llm_payload("QUALIFIED"))
    _write_case(tmp_path, "B", llm_payload("UNQUALIFIED", ["PERIOD_MISMATCH", "WRONG_VALUE"], {"period": "MISMATCH", "numeric_accuracy": "MISMATCH"}))
    _write_case(tmp_path, "C", llm_payload("PARTIAL", ["MISSING_ROW"], {"row_coverage": "PARTIAL"}))
    metrics = run_evaluation_metrics(repo, "r", [contract(cid) for cid in ["A", "B", "C"]])

    period = metrics["dimension_metrics"]["period"]
    assert period["label"] == "期间一致率"
    assert period["matched"] == 2
    assert period["mismatched"] == 1
    assert period["evaluable"] == 3
    assert period["rate"] == round(2 / 3, 4)
    numeric = metrics["dimension_metrics"]["numeric_accuracy"]
    assert numeric["matched"] == 2 and numeric["mismatched"] == 1 and numeric["rate"] == round(2 / 3, 4)
    scope = metrics["dimension_metrics"]["scope"]
    assert scope["evaluable"] == 3 and scope["rate"] == 1.0

    assert metrics["primary_issue_distribution"] == {"PERIOD_MISMATCH": 1, "MISSING_ROW": 1}
    assert metrics["issue_distribution"] == {"PERIOD_MISMATCH": 1, "WRONG_VALUE": 1, "MISSING_ROW": 1}


def test_manual_qualified_makes_unevaluable_count(tmp_path):
    repo = Repo()
    repo.runs_dir = tmp_path
    repo.cases[2]["manual_verdict"] = "QUALIFIED"
    _write_case(tmp_path, "A", llm_payload("QUALIFIED"))
    _write_case(tmp_path, "B", llm_payload("UNQUALIFIED", ["WRONG_VALUE"], {"numeric_accuracy": "MISMATCH"}))
    metrics = run_evaluation_metrics(repo, "r", [contract("A"), contract("B"), contract("C")])
    assert metrics["assessability"]["rate"] == 1.0
    assert metrics["numeric"]["accuracy"] == round(2 / 3, 4)
    assert metrics["case_judgments"]["C"]["final_verdict"] == "QUALIFIED"
    assert metrics["case_judgments"]["C"]["auto_verdict"] == "UNEVALUABLE"


def test_claims_are_ignored_without_llm_evaluation(tmp_path):
    repo = Repo()
    repo.cases = [{"case_id": "A", "status": "PASS", "completion_status": "completed"}]
    repo.runs_dir = tmp_path
    case_dir = tmp_path / "r" / "cases" / "A"
    case_dir.mkdir(parents=True)
    (case_dir / "agent_answer.json").write_text('{"text":"水量为1"}', encoding="utf-8")
    (case_dir / "sql_snapshot.json").write_text('{"columns":["value"],"rows":[{"value":1}]}', encoding="utf-8")
    for source in ("agent", "sql"):
        (case_dir / f"{source}_claims.json").write_text(
            json.dumps([{"metric": "水量", "value": 1, "source": source}], ensure_ascii=False), encoding="utf-8")
    metrics = run_evaluation_metrics(repo, "r", [contract("A")])
    assert metrics["numeric"]["qualified_questions"] == 0
    assert metrics["case_judgments"]["A"]["final_verdict"] == "UNEVALUABLE"
    assert metrics["case_judgments"]["A"]["has_evaluation"] is False
