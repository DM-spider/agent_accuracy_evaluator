# -*- coding: utf-8 -*-
import json
import time

from fastapi.testclient import TestClient


def _bootstrap(tmp_path):
    import evaluator.api as api_mod
    from evaluator.settings import load_settings

    api_mod._STATE.clear()
    settings = load_settings()
    settings["paths"] = {"runtime_dir": str(tmp_path), "golden_dir": ""}
    api_mod.bootstrap(settings)
    return api_mod


def test_api_create_progress_overview_detail_review(tmp_path):
    _bootstrap(tmp_path)
    from evaluator.app import app

    client = TestClient(app)
    health = client.get("/api/health")
    assert health.status_code == 200
    assert health.json()["golden"] == "ok"
    assert "evaluator_llm" in health.json()
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
    metrics = overview.json()["evaluation_metrics"]
    assert metrics["total_questions"] == 2
    assert "dimension_metrics" in metrics
    assert "primary_issue_distribution" in metrics
    cases = client.get(f"/api/runs/{run_id}/cases")
    assert cases.status_code == 200
    assert len(cases.json()["cases"]) == 2
    detail = client.get(f"/api/runs/{run_id}/cases/CX01")
    assert detail.status_code == 200
    body = detail.json()
    assert "result" in body or "status" in body
    assert body["llm_evaluation"]["evaluation"]["overall_verdict"] == "QUALIFIED"
    assert body["judgment"]["final_verdict"] == "QUALIFIED"
    assert "table_comparison" not in body
    assert body["caliber_alignment"]["rows"]
    # GET 只读持久化结果：再次访问详情不新增 LLM 评估记录
    artifact_path = tmp_path / "runs" / run_id / "cases" / "CX01" / "llm_evaluation.json"
    before_blob = artifact_path.read_text(encoding="utf-8")
    for _ in range(3):
        assert client.get(f"/api/runs/{run_id}/cases/CX01").status_code == 200
    assert artifact_path.read_text(encoding="utf-8") == before_blob
    history_dir = tmp_path / "runs" / run_id / "llm_evaluations"
    assert not history_dir.exists() or not list(history_dir.glob("*.json"))
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


import pytest


def _bootstrap_with_llm(tmp_path, llm_cfg):
    import evaluator.api as api_mod
    from evaluator.settings import load_settings

    api_mod._STATE.clear()
    settings = load_settings()
    settings["paths"] = {"runtime_dir": str(tmp_path), "golden_dir": ""}
    settings["evaluator_llm"] = llm_cfg
    api_mod.bootstrap(settings)


@pytest.mark.parametrize(
    "llm_cfg,expected",
    [
        ({"enabled": True, "base_url": "https://api.example.com", "model": "m", "api_key": "sk-secret"}, "configured"),
        ({"enabled": False, "base_url": "https://api.example.com", "model": "m", "api_key": "sk-secret"}, "disabled"),
        ({"enabled": True, "base_url": "", "model": "", "api_key": ""}, "not_configured"),
        ({"enabled": True, "base_url": "https://api.example.com", "model": "m"}, "not_configured"),
    ],
)
def test_health_reports_evaluator_llm_state(tmp_path, llm_cfg, expected):
    _bootstrap_with_llm(tmp_path, llm_cfg)
    from evaluator.app import app

    response = TestClient(app).get("/api/health")
    assert response.status_code == 200
    assert response.json()["evaluator_llm"] == expected
    assert "sk-secret" not in response.text
