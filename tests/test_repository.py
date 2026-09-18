# -*- coding: utf-8 -*-
from evaluator.models import (
    REQUIRED_DIMENSIONS,
    CaseMetrics,
    CaseResult,
    CaseStatus,
    DimensionEvaluation,
    EvaluationVerdict,
    LlmEvaluationArtifact,
    LlmEvaluationResult,
    RunStatus,
    RunSummary,
)
from evaluator.repository import Repository


def test_sqlite_roundtrip(tmp_path):
    repo = Repository(db_path=tmp_path / "e.db", runs_dir=tmp_path / "runs")
    summary = RunSummary(run_id="r1", status=RunStatus.RUNNING, total_cases=2, started_at="t")
    repo.create_run(summary)
    repo.save_case(
        "r1",
        CaseResult(case_id="CX01", status=CaseStatus.PASS, metrics=CaseMetrics(match_count=3)),
        agent_text="ok",
        sql_payload={"rows": []},
    )
    found = repo.get_run("r1")
    assert found["run_id"] == "r1"
    cases = repo.list_cases("r1")
    assert cases[0]["case_id"] == "CX01"
    detail = repo.load_case_detail("r1", "CX01")
    assert detail["agent_answer"]["text"] == "ok"
    repo.save_verdict("r1", "CX01", "QUALIFIED", "人工看过")
    detail2 = repo.load_case_detail("r1", "CX01")
    assert detail2["review"]["note"] == "人工看过"
    assert detail2["manual_verdict"] == "QUALIFIED"
    assert repo.list_runs()


def _llm_artifact(input_hash="h1", verdict="QUALIFIED", created_at="2026-09-18T10:00:00+08:00"):
    result = LlmEvaluationResult(
        overall_verdict=verdict,
        confidence=0.9,
        summary="结论",
        primary_issue_code=None,
        issue_codes=[],
        dimensions={key: DimensionEvaluation(status="MATCH") for key in REQUIRED_DIMENSIONS},
    )
    return LlmEvaluationArtifact(
        evaluation=result,
        provider="openai-compatible",
        model="deepseek-flash",
        prompt_version="v1",
        input_hash=input_hash,
        created_at=created_at,
    )


def _run(repo, run_id="r1"):
    repo.create_run(RunSummary(run_id=run_id, status=RunStatus.RUNNING, total_cases=1, started_at="t"))
    repo.save_case(run_id, CaseResult(case_id="CX01", status=CaseStatus.PASS))


def test_save_load_and_reuse_llm_evaluation(tmp_path):
    repo = Repository(db_path=tmp_path / "e.db", runs_dir=tmp_path / "runs")
    _run(repo)
    path = repo.save_llm_evaluation("r1", "CX01", _llm_artifact())
    assert path.endswith("llm_evaluation.json")
    detail = repo.load_case_detail("r1", "CX01")
    assert detail["llm_evaluation"]["evaluation"]["overall_verdict"] == "QUALIFIED"
    assert detail["llm_evaluation"]["input_hash"] == "h1"
    reusable = repo.find_reusable_llm_evaluation(
        "r1", "CX01", input_hash="h1", model="deepseek-flash", prompt_version="v1"
    )
    assert reusable and reusable["model"] == "deepseek-flash"
    assert repo.find_reusable_llm_evaluation(
        "r1", "CX01", input_hash="h2", model="deepseek-flash", prompt_version="v1"
    ) is None


def test_overwriting_llm_evaluation_archives_previous_version(tmp_path):
    repo = Repository(db_path=tmp_path / "e.db", runs_dir=tmp_path / "runs")
    _run(repo)
    repo.save_llm_evaluation("r1", "CX01", _llm_artifact(input_hash="h1"))
    repo.save_llm_evaluation("r1", "CX01", _llm_artifact(input_hash="h2", verdict="UNQUALIFIED"))
    history = list((tmp_path / "runs" / "r1" / "llm_evaluations").glob("*.json"))
    assert len(history) == 1
    assert repo.load_llm_evaluation("r1", "CX01")["input_hash"] == "h2"


def test_llm_evaluation_files_and_index_have_no_api_key(tmp_path):
    repo = Repository(db_path=tmp_path / "e.db", runs_dir=tmp_path / "runs")
    _run(repo)
    artifact = _llm_artifact()
    artifact.raw_response = "authorization header redacted"
    repo.save_llm_evaluation("r1", "CX01", artifact)
    blob = "".join(p.read_text(encoding="utf-8") for p in (tmp_path / "runs" / "r1").rglob("*.json"))
    assert "api_key" not in blob
    with repo._connect() as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(llm_evaluations)")}
        assert "api_key" not in columns
        row = conn.execute("SELECT * FROM llm_evaluations WHERE run_id='r1'").fetchone()
        assert row["verdict"] == "QUALIFIED"
