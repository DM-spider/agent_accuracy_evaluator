# -*- coding: utf-8 -*-
"""过程采集：命令解析、状态归一化与记录上限。"""
import json

from evaluator.models import AgentAnswer
from evaluator.process_trace import TraceRecorder, build_process, parse_cli_invocations


def _answer(entries, *, latency_ms=1000, timings=None):
    return AgentAnswer(
        case_id="T-001",
        question="q",
        text="",
        latency_ms=latency_ms,
        timings=timings or {"transport_total_ms": 1234.5},
        response_body={"process": entries},
    )


def _call(code, *, call_id="c1", t_ms=100, params="--param pageNo=0"):
    command = f"cd /root && leakage-agent-cli invoke {code} {params}"
    return {
        "kind": "function_call",
        "t_ms": t_ms,
        "payload": {
            "type": "function_call",
            "name": "exec",
            "call_id": call_id,
            "arguments": json.dumps({"command": command, "timeout": 120}, ensure_ascii=False),
        },
    }


def _output(text, *, call_id="c1", t_ms=600, details=None):
    payload = {"type": "function_call_output", "call_id": call_id,
               "output": json.dumps({"content": [{"type": "text", "text": text}],
                                     "details": details or {"status": "completed", "exitCode": 0}}, ensure_ascii=False)}
    return {"kind": "function_call_output", "t_ms": t_ms, "payload": payload}


def test_parse_direct_cli_and_heredoc_invoke():
    direct = "leakage-agent-cli invoke year_missed_rate --param pageNo=0 --param pageSize=100"
    assert parse_cli_invocations(direct) == [
        {"code": "year_missed_rate", "params": {"pageNo": "0", "pageSize": "100"}}
    ]
    heredoc = (
        "cd /root/.openclaw/workspace/x && python3 << 'EOF'\n"
        "def invoke(code, params):\n"
        "    pass\n"
        'd = invoke("leak_detection_efficiency_details", {"pageNumber": 1, "fgs": "宝安水务集团"})\n'
        "EOF"
    )
    assert parse_cli_invocations(heredoc) == [
        {"code": "leak_detection_efficiency_details", "params": {"pageNumber": 1, "fgs": "宝安水务集团"}}
    ]


def test_step_carries_code_params_and_status():
    entries = [
        _call("year_missed_rate"),
        _output("== 宝安 == totalRows= 0 rows= 0"),
    ]
    process = build_process(_answer(entries))
    steps = process["timeline"]
    steps = [item["step"] for item in steps if item["kind"] == "step"]
    assert len(steps) == 1
    step = steps[0]
    assert step["codes"] == ["year_missed_rate"]
    assert step["params"] == {"pageNo": "0"}
    assert step["status"] == "empty"
    assert step["rows"] == 0
    assert process["review"]["stats"]["tool_calls"] == 1
    assert "EMPTY_RESULT_HIGH" in [item["code"] for item in process["review"]["findings"]]


def test_timeout_is_distinguished_from_error():
    entries = [
        _call("water_supply_pipe_maintenance_work"),
        _output(
            "no new output\n\nProcess exited with signal SIGTERM.",
            details={"status": "failed", "exitSignal": "SIGTERM", "exitReason": "overall-timeout", "timedOut": True},
        ),
    ]
    process = build_process(_answer(entries))
    step = [item["step"] for item in process["timeline"] if item["kind"] == "step"][0]
    assert step["status"] == "timeout"
    assert "TOOL_TIMEOUT" in process["flags"]


def test_reasoning_and_narration_join_timeline():
    entries = [
        {"kind": "reasoning", "t_ms": 50, "payload": {"type": "reasoning", "summary": [{"type": "summary_text", "text": "先确认口径"}]}},
        {"kind": "message", "t_ms": 80, "payload": {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "改为按月查询"}]}},
        _call("year_missed_rate"),
        _output("totalRows= 2242 rows= 100"),
    ]
    process = build_process(_answer(entries))
    kinds = [item["kind"] for item in process["timeline"]]
    assert kinds == ["thought", "note", "step"]
    assert process["summary"]["thoughts"] == 1
    assert process["summary"]["notes"] == 1


def test_recorder_redacts_secret_and_caps_items():
    recorder = TraceRecorder("SESSION-ABC")
    recorder.add("message", 1, {"type": "message", "content": [{"type": "output_text", "text": "cookie=SESSION-ABC"}]})
    items = recorder.items()
    assert "SESSION-ABC" not in json.dumps(items, ensure_ascii=False)
    assert "[REDACTED]" in json.dumps(items, ensure_ascii=False)

    from evaluator.process_trace import MAX_ITEMS

    for index in range(MAX_ITEMS + 5):
        recorder.add("message", index, {"type": "message"})
    assert len(recorder.items()) == MAX_ITEMS
    assert recorder.truncated is True
