# -*- coding: utf-8 -*-
"""过程评测：把过程信号翻译成问题与建议。"""
from evaluator.process_review import review_process


def _codes(review):
    return [item["code"] for item in review["findings"]]


def test_timeout_empty_and_repeats_are_reported():
    review = review_process(
        summary={"steps": 3, "thoughts": 1, "notes": 0},
        counts={"tool_calls": 3, "shell": 0, "poll": 0, "ok": 1, "empty": 2, "error": 0, "timeout": 1, "pending": 0},
        steps=[
            {"calls": [{"code": "year_missed_rate", "params": {"pageNo": 0}}]},
            {"calls": [{"code": "year_missed_rate", "params": {"pageNo": 0}}]},
        ],
        completion_status="completed",
    )
    codes = _codes(review)
    assert "TOOL_TIMEOUT" in codes
    assert "EMPTY_RESULT_HIGH" in codes
    assert "REPEATED_CALLS" in codes
    assert review["verdict"] == "PROCESS_RISK"
    assert review["suggestions"]


def test_healthy_run_has_no_findings():
    review = review_process(
        summary={"steps": 1, "thoughts": 1, "notes": 0},
        counts={"tool_calls": 1, "shell": 0, "poll": 0, "ok": 1, "empty": 0, "error": 0, "timeout": 0, "pending": 0},
        steps=[{"calls": [{"code": "CX-001", "params": {"period": 202610}}]}],
        completion_status="completed",
    )
    assert review["verdict"] == "PROCESS_HEALTHY"
    assert review["findings"] == []


def test_declared_unavailable_is_flagged():
    review = review_process(
        summary={"steps": 2},
        counts={"tool_calls": 2, "shell": 2, "poll": 0, "ok": 1, "empty": 0, "error": 0, "timeout": 0, "pending": 0},
        steps=[],
        completion_status="completed",
        answer_text="当前会话取不到实时数值，ask.py 返回 NEED_LLM，按规则不估算。",
    )
    assert "AGENT_DECLARED_UNAVAILABLE" in _codes(review)
    assert review["verdict"] == "PROCESS_RISK"


def test_incomplete_and_error_are_reported():
    review = review_process(
        summary={"steps": 1},
        counts={"tool_calls": 1, "shell": 0, "poll": 0, "ok": 0, "empty": 0, "error": 0, "timeout": 0, "pending": 1},
        steps=[],
        completion_status="unknown",
        error="STREAM_ERROR:ReadTimeout",
    )
    codes = _codes(review)
    assert "EXECUTION_INCOMPLETE" in codes
    assert "EXECUTION_ERROR" in codes
    assert "PENDING_STEPS" in codes
    assert review["verdict"] == "PROCESS_RISK"


def test_no_process_data_reports_unknown():
    review = review_process(summary={}, counts={}, steps=[], completion_status="completed")
    assert review["verdict"] == "PROCESS_UNKNOWN"


def test_exploration_heavy_when_shell_outnumbers_data_calls():
    review = review_process(
        summary={"steps": 6},
        counts={"tool_calls": 6, "shell": 6, "poll": 0, "ok": 6, "empty": 0, "error": 0, "timeout": 0, "pending": 0},
        steps=[],
        completion_status="completed",
    )
    assert "EXPLORATION_HEAVY" in _codes(review)
    assert review["verdict"] == "PROCESS_ATTENTION"
