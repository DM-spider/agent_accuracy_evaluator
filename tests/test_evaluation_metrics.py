import json

from evaluator.evaluation_metrics import run_evaluation_metrics
from evaluator.models import CaseContract, CoveragePolicy, MeasureSpec


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


def _write_case(tmp_path, case_id, agent_value, sql_value=1):
    case_dir = tmp_path / "r" / "cases" / case_id
    case_dir.mkdir(parents=True)
    (case_dir / "agent_answer.json").write_text(
        '{"text":"| 指标 | 数值 |\\n|---|---|\\n| 水量 | ' + str(agent_value) + ' |"}', encoding="utf-8")
    (case_dir / "sql_snapshot.json").write_text(
        '{"columns":["value"],"rows":[{"value":' + str(sql_value) + '}]}', encoding="utf-8")


def test_pass_rate_denominator_is_fixed_planned_count(tmp_path):
    """通过率分母 = 开跑即固定的计划题数（contracts_snapshot），不随已完成题数增长。

    live 执行中 list_cases 只有已落库题且必有返回文本，若用它当分母，
    分子恒等于分母、通过率全程显示 100%。
    """
    repo = Repo()
    repo.cases = [
        {"case_id": "A", "status": "PASS", "completion_status": "completed"},
        {"case_id": "B", "status": "FAIL", "completion_status": "completed"},
    ]
    repo.runs_dir = tmp_path
    _write_case(tmp_path, "A", 1)
    _write_case(tmp_path, "B", 2)
    snapshot = tmp_path / "r" / "contracts_snapshot.json"
    snapshot.write_text(
        json.dumps([contract(cid).model_dump(mode="json") for cid in ["A", "B", "C", "D", "E"]], ensure_ascii=False),
        encoding="utf-8")
    metrics = run_evaluation_metrics(repo, "r", [contract("A"), contract("B")])
    assert metrics["total_questions"] == 5
    assert metrics["planned_questions"] == 5
    assert metrics["final"]["generated_questions"] == 2
    assert metrics["final"]["pass_rate"] == 0.4


def test_run_metrics_uses_generation_and_consistency(tmp_path):
    repo = Repo()
    repo.runs_dir = tmp_path
    _write_case(tmp_path, "A", 1)
    _write_case(tmp_path, "B", 2)
    metrics = run_evaluation_metrics(repo, "r", [contract("A"), contract("B"), contract("C")])
    assert metrics["final"]["pass_rate"] == round(2 / 3, 4)
    assert metrics["final"]["generated_questions"] == 2
    assert metrics["numeric"]["accuracy"] == 0.5
    assert metrics["numeric"]["qualified_questions"] == 1
    assert metrics["assessability"]["rate"] == round(2 / 3, 4)
    assert metrics["case_judgments"]["A"]["final_verdict"] == "QUALIFIED"
    assert metrics["case_judgments"]["B"]["final_verdict"] == "UNQUALIFIED"
    assert metrics["case_judgments"]["C"]["final_verdict"] == "UNEVALUABLE"
    assert "category_pass_rate" in metrics


def test_v3_category_pass_rate(tmp_path):
    repo = Repo()
    repo.cases = [
        {"case_id": "CX-001", "status": "PASS", "completion_status": "completed"},
        {"case_id": "LS-001", "status": "FAIL", "completion_status": "completed"},
        {"case_id": "CX01", "status": "PASS", "completion_status": "completed"},
    ]
    repo.runs_dir = tmp_path
    _write_case(tmp_path, "CX-001", 1)
    _write_case(tmp_path, "LS-001", 2)
    _write_case(tmp_path, "CX01", 1)
    metrics = run_evaluation_metrics(
        repo, "r",
        [contract("CX-001"), contract("LS-001"), contract("CX01")],
    )
    assert metrics["category_pass_rate"]["CX"]["total"] == 1
    assert metrics["category_pass_rate"]["CX"]["qualified"] == 1
    assert metrics["category_pass_rate"]["LS"]["qualified"] == 0
    assert metrics["category_pass_rate"]["other"]["total"] == 1


def test_manual_qualified_makes_unevaluable_count(tmp_path):
    repo = Repo()
    repo.runs_dir = tmp_path
    repo.cases[2]["manual_verdict"] = "QUALIFIED"
    _write_case(tmp_path, "A", 1)
    _write_case(tmp_path, "B", 2)
    metrics = run_evaluation_metrics(repo, "r", [contract("A"), contract("B"), contract("C")])
    assert metrics["assessability"]["rate"] == 1.0
    assert metrics["numeric"]["accuracy"] == round(2 / 3, 4)
    assert metrics["case_judgments"]["C"]["final_verdict"] == "QUALIFIED"
    assert metrics["final"]["pass_rate"] == round(2 / 3, 4)


def test_run_metrics_reuses_saved_claims_for_normalized_sql_snapshot(tmp_path):
    repo = Repo()
    repo.cases = [{"case_id": "A", "status": "PASS", "completion_status": "completed"}]
    repo.runs_dir = tmp_path
    case_dir = tmp_path / "r" / "cases" / "A"
    case_dir.mkdir(parents=True)
    (case_dir / "agent_answer.json").write_text('{"text":"水量为1"}', encoding="utf-8")
    (case_dir / "sql_snapshot.json").write_text(
        '{"columns":["指标","数值"],"rows":[{"指标":"水量","数值":1}]}', encoding="utf-8")
    claim = {"case_id": "A", "coordinates": {}, "metric": "水量", "raw_value": "1", "value": 1,
             "unit": "", "evidence": "水量=1", "extractor": "saved"}
    for source in ("agent", "sql"):
        payload = dict(claim, claim_id=f"A:{source}:0", source=source)
        (case_dir / f"{source}_claims.json").write_text(
            json.dumps([payload], ensure_ascii=False), encoding="utf-8")
    metrics = run_evaluation_metrics(repo, "r", [contract("A")])
    assert metrics["numeric"]["qualified_questions"] == 1
    assert metrics["numeric"]["accuracy"] == 1.0
    assert metrics["case_judgments"]["A"]["final_verdict"] == "QUALIFIED"
