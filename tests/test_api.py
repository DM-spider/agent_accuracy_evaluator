# -*- coding: utf-8 -*-
from fastapi.testclient import TestClient


def _bootstrap(tmp_path, mutate=None):
    import evaluator.api as api_mod
    from evaluator.settings import load_settings

    api_mod._STATE.clear()
    settings = load_settings()
    settings["paths"] = {"runtime_dir": str(tmp_path), "golden_dir": ""}
    if mutate:
        mutate(settings)
    api_mod.bootstrap(settings)
    return api_mod


def test_health_reports_golden_and_contracts(tmp_path):
    _bootstrap(tmp_path)
    from evaluator.app import app

    health = TestClient(app).get("/api/health")
    assert health.status_code == 200
    payload = health.json()
    assert payload["golden"] == "ok"
    assert payload["contracts"] == 141
    assert "evaluator_llm" in payload


def test_catalog_lists_numeric_cases(tmp_path):
    _bootstrap(tmp_path)
    from evaluator.app import app

    payload = TestClient(app).get("/api/catalog").json()
    assert payload["total"] == 141
    assert payload["numeric"] == 141
    assert payload["verified"] == 16
    assert sum(1 for c in payload["cases"] if c["business_verified"]) == 16
    assert payload["cases"][0]["case_id"]


def test_create_run_requires_database(tmp_path):
    def clear_database(settings):
        settings["database"] = {}

    _bootstrap(tmp_path, clear_database)
    from evaluator.app import app

    response = TestClient(app).post("/api/runs", json={})
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "database_not_configured"


def test_create_run_requires_confirmation_before_anchor(tmp_path):
    _bootstrap(tmp_path)
    from evaluator.app import app

    response = TestClient(app).post("/api/runs", json={"anchor_time": "2026-08-17"})
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "live_anchor_managed"
