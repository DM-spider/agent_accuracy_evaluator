# -*- coding: utf-8 -*-
import os
import time

from fastapi.testclient import TestClient


def test_api_create_progress_overview_detail_review(tmp_path, monkeypatch):
    monkeypatch.setenv("EVAL_RUNTIME_DIR", str(tmp_path))
    import evaluator.paths as paths
    monkeypatch.setattr(paths, "RUNTIME_DIR", tmp_path)
    monkeypatch.setattr(paths, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(paths, "DB_PATH", tmp_path / "evaluation.db")
    import evaluator.api as api_mod
    api_mod._STATE.clear()
    from evaluator.app import app
    from evaluator.api import bootstrap

    bootstrap()
    client = TestClient(app)
    health = client.get("/api/health")
    assert health.status_code == 200
    assert health.json()["golden"] == "ok"
    created = client.post("/api/runs", json={"mode": "mock_perfect", "case_ids": ["CX01", "CX04"], "numeric_only": True, "consistency_threshold": 0.6})
    assert created.status_code == 200
    run_id = created.json()["run_id"]
    for _ in range(40):
        prog = client.get(f"/api/runs/{run_id}/progress").json()
        if prog.get("status") == "COMPLETED" or prog.get("done") == 2:
            break
        time.sleep(0.2)
    overview = client.get(f"/api/runs/{run_id}")
    assert overview.status_code == 200
    assert "evaluation_metrics" in overview.json()
    assert overview.json()["evaluation_metrics"]["total_questions"] == 2
    cases = client.get(f"/api/runs/{run_id}/cases")
    assert cases.status_code == 200
    assert len(cases.json()["cases"]) == 2
    detail = client.get(f"/api/runs/{run_id}/cases/CX01")
    assert detail.status_code == 200
    body = detail.json()
    assert "result" in body or "status" in body
    overview = client.get(f"/api/runs/{run_id}").json()
    before = overview["evaluation_metrics"]["numeric"]["accuracy"]
    flipped = client.post(f"/api/runs/{run_id}/cases/CX01/verdict", json={"verdict": "unqualified"})
    assert flipped.status_code == 200
    after = client.get(f"/api/runs/{run_id}").json()["evaluation_metrics"]
    assert after["case_judgments"]["CX01"]["final_verdict"] == "UNQUALIFIED"
    assert after["numeric"]["accuracy"] != before or after["numeric"]["qualified_questions"] == 0
    page = client.get(f"/runs/{run_id}")
    assert page.status_code == 200
    assert "评估指标" in page.text
