# -*- coding: utf-8 -*-
"""过程评测：把过程信号翻译成问题与建议。"""
from evaluator.process_review import review_from_process, review_process


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


def test_caliber_blocker_is_not_channel_failure():
    # 「这张表没有漏损水量字段」是口径问题，不是取数通道故障；不能记成过程风险。
    review = review_process(
        summary={"steps": 2},
        counts={"tool_calls": 2, "shell": 2, "poll": 0, "ok": 2, "empty": 0, "error": 0, "timeout": 0, "pending": 0},
        steps=[{"status": "ok", "command": 't.call("invoke", "water_supply_pipe_maintenance_work", {})'}],
        completion_status="completed",
        answer_text="严格口径无法取数：工单明细接口没有漏损水量字段，不得改用其它字段代算。已用替代口径取得数据。",
    )
    assert "AGENT_DECLARED_UNAVAILABLE" not in _codes(review)
    assert review["verdict"] == "PROCESS_HEALTHY"


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


def test_shell_commands_running_queries_are_not_exploration():
    command = "python - <<'PY'\nfrom cli_client import CliTransport\nt.call(\"invoke\", \"water_supply_pipe_maintenance_work\", {})\nPY"
    review = review_process(
        summary={"steps": 6},
        counts={"tool_calls": 6, "shell": 6, "poll": 0, "ok": 6, "empty": 0, "error": 0, "timeout": 0, "pending": 0},
        steps=[{"status": "ok", "command": command} for _ in range(6)],
        completion_status="completed",
    )
    assert "EXPLORATION_HEAVY" not in _codes(review)
    assert review["stats"]["data_calls"] == 6


def test_empty_calls_carry_concrete_evidence():
    command = 't.call("invoke", "water_loss_total", {})'
    review = review_process(
        summary={"steps": 3},
        counts={"tool_calls": 3, "shell": 3, "poll": 0, "ok": 1, "empty": 2, "error": 0, "timeout": 0, "pending": 0},
        steps=[
            {"status": "ok", "t_ms": 1000, "command": command},
            {"status": "empty", "t_ms": 2000, "command": command},
            {"status": "empty", "t_ms": 3000, "command": command},
        ],
        completion_status="completed",
    )
    finding = next(item for item in review["findings"] if item["code"] == "EMPTY_UNRESOLVED")
    assert len(finding["evidence"]) == 2
    assert "第 2 次调用" in finding["evidence"][0]
    assert "2.0 秒" in finding["evidence"][0]
    assert "water_loss_total" in finding["evidence"][0]
    assert "都发生在「water_loss_total」" in finding["detail"]


def test_empty_results_followed_by_success_are_observations():
    steps = [
        {"status": "ok", "command": 't.call("invoke", "x", {})'},
        {"status": "empty", "command": 't.call("invoke", "x", {"a": 1})'},
        {"status": "empty", "command": 't.call("invoke", "x", {"a": 2})'},
        {"status": "ok", "command": 't.call("invoke", "x", {"a": 3})'},
    ]
    review = review_process(
        summary={"steps": 4},
        counts={"tool_calls": 4, "shell": 4, "poll": 0, "ok": 2, "empty": 2, "error": 0, "timeout": 0, "pending": 0},
        steps=steps,
        completion_status="completed",
    )
    assert not [code for code in _codes(review) if code.startswith("EMPTY")]
    assert review["observations"]
    assert review["verdict"] == "PROCESS_HEALTHY"


def test_empty_results_without_recovery_are_a_finding():
    steps = [
        {"status": "ok", "command": 't.call("invoke", "x", {})'},
        {"status": "empty", "command": 't.call("invoke", "x", {"a": 1})'},
        {"status": "empty", "command": 't.call("invoke", "x", {"a": 2})'},
    ]
    review = review_process(
        summary={"steps": 3},
        counts={"tool_calls": 3, "shell": 3, "poll": 0, "ok": 1, "empty": 2, "error": 0, "timeout": 0, "pending": 0},
        steps=steps,
        completion_status="completed",
    )
    assert "EMPTY_UNRESOLVED" in _codes(review)
    assert review["verdict"] == "PROCESS_ATTENTION"


def test_review_from_process_rebuilds_from_stored_payload():
    process = {
        "summary": {"tool_calls": 2, "completion_status": "completed"},
        "counts": {"tool_calls": 3, "shell": 3, "poll": 0, "ok": 1, "empty": 2, "error": 0, "timeout": 0, "pending": 0},
        "steps": [
            {"status": "ok", "command": 't.call("invoke", "water_loss_total", {})'},
            {"status": "empty", "command": 't.call("invoke", "water_loss_total", {})'},
            {"status": "empty", "command": 't.call("invoke", "water_loss_total", {})'},
        ],
    }
    review = review_from_process(process)
    finding = next(item for item in review["findings"] if item["code"] == "EMPTY_UNRESOLVED")
    assert finding["evidence"]
    assert review["stats"]["data_calls"] == 3


def test_legacy_read_timeout_without_headers_is_no_response():
    process = {
        "summary": {"completion_status": "unknown", "error": "STREAM_ERROR:ReadTimeout"},
        "counts": {"tool_calls": 0},
        "steps": [],
    }
    review = review_from_process(process, timings={"headers_ms": None})
    assert "PLATFORM_NO_RESPONSE" in _codes(review)
    assert review["verdict"] == "PROCESS_RISK"


def test_platform_no_response_is_reported_specifically():
    review = review_process(
        summary={"steps": 0},
        counts={"tool_calls": 0, "shell": 0, "poll": 0, "ok": 0, "empty": 0, "error": 0, "timeout": 0, "pending": 0},
        steps=[],
        completion_status="unknown",
        error="STREAM_ERROR:NoResponseHeaders",
    )
    codes = _codes(review)
    assert "PLATFORM_NO_RESPONSE" in codes
    assert "EXECUTION_INCOMPLETE" not in codes
    assert "EXECUTION_ERROR" not in codes
    assert review["verdict"] == "PROCESS_RISK"
    assert any("cURL" in item for item in review["suggestions"])


def test_exploration_heavy_when_shell_outnumbers_data_calls():
    review = review_process(
        summary={"steps": 6},
        counts={"tool_calls": 6, "shell": 6, "poll": 0, "ok": 6, "empty": 0, "error": 0, "timeout": 0, "pending": 0},
        steps=[],
        completion_status="completed",
    )
    assert "EXPLORATION_HEAVY" in _codes(review)
    assert review["verdict"] == "PROCESS_ATTENTION"
