# -*- coding: utf-8 -*-
import time

from fastapi.testclient import TestClient

from evaluator.golden_loader import load_agent_answers
from evaluator.mock_demo import (
    MIXED_ANSWERS_FILE,
    MIXED_CASE_IDS,
    PLANTED_ERRORS,
    load_script_answers,
    parse_mock_script,
    script_path,
)


def test_mixed_answers_cover_twenty_cases_with_errors():
    payload = load_agent_answers(MIXED_ANSWERS_FILE)
    ids = [c["case_id"] for c in payload["cases"]]
    assert ids == MIXED_CASE_IDS
    assert len(ids) == 20
    errors = [c for c in payload["cases"] if c.get("expected_outcome") == "ERROR"]
    correct = [c for c in payload["cases"] if c.get("expected_outcome") == "PASS"]
    assert len(errors) == 10
    assert len(correct) == 10
    cx03 = next(c for c in payload["cases"] if c["case_id"] == "CX03")
    assert "19.0800" in cx03["final_answer_text"]
    ls4 = next(c for c in payload["cases"] if c["case_id"] == "LS4")
    data_rows = [ln for ln in ls4["final_answer_text"].splitlines() if ln.startswith("|") and "---" not in ln]
    assert len(data_rows) == 6  # header + 5 data
    assert set(PLANTED_ERRORS) == {c["case_id"] for c in errors}


def test_mock_script_is_markdown_with_request_and_response():
    path = script_path()
    assert path.exists()
    text = path.read_text(encoding="utf-8")
    parsed = parse_mock_script(text)
    assert list(parsed) == MIXED_CASE_IDS
    answers = load_script_answers()
    assert "19.0800" in answers["CX03"]
    assert "根据评测基准时间" in answers["CX01"] or "按评测基准时间" in answers["CX01"]
    assert "```json" in parsed["CX01"]["block"]
    assert "POST http://mock-agent/ask" in parsed["CX01"]["block"]
    assert "### 智能体返回" in parsed["CX01"]["block"]


def test_api_mock_mixed_defaults_to_twenty_and_detects_errors(tmp_path, monkeypatch):
    monkeypatch.setenv("EVAL_RUNTIME_DIR", str(tmp_path))
    import evaluator.api as api_mod
    api_mod._STATE.clear()
    from evaluator.app import app
    from evaluator.api import bootstrap

    bootstrap()
    client = TestClient(app)
    pack = client.get("/api/mock-demo")
    assert pack.status_code == 200
    assert pack.json()["case_ids"] == MIXED_CASE_IDS
    created = client.post("/api/runs", json={"mode": "mock_mixed"})
    assert created.status_code == 200
    run_id = created.json()["run_id"]
    for _ in range(80):
        prog = client.get(f"/api/runs/{run_id}/progress").json()
        if prog.get("status") == "COMPLETED" or prog.get("done") == 20:
            break
        time.sleep(0.2)
    cases = client.get(f"/api/runs/{run_id}/cases").json()["cases"]
    assert len(cases) == 20
    by = {c["case_id"]: c for c in cases}
    assert by["CX01"]["status"] in {"PASS", "PARTIAL"}
    assert by["CX04"]["status"] == "FAIL"
    assert by["CX03"]["status"] == "FAIL"
    statuses = {c["status"] for c in cases}
    assert "FAIL" in statuses
    assert "PASS" in statuses or "PARTIAL" in statuses
    detail = client.get(f"/api/runs/{run_id}/cases/CX01").json()
    snap = detail.get("sql_snapshot") or {}
    assert snap.get("source") == "golden_expected"
    assert snap.get("rows")
    raw = (detail.get("agent_answer") or {}).get("raw") or {}
    assert raw.get("request", {}).get("case_id") == "CX01"
    assert "question" in (raw.get("request") or {})
    assert "sql" not in (raw.get("request") or {})
    assert (raw.get("response") or {}).get("answer")
    script = client.get("/api/mock-demo/script")
    assert script.status_code == 200
    assert "模拟智能体 20 题" in script.text
