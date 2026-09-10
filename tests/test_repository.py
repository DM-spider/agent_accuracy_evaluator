# -*- coding: utf-8 -*-
from evaluator.models import CaseMetrics, CaseResult, CaseStatus, RunStatus, RunSummary
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
