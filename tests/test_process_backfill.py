# -*- coding: utf-8 -*-
"""旧存档回填：从 trace 条目重建步骤状态与过程结论。"""
import json

from backfill_process import rebuild_payload
from evaluator.models import AgentAnswer
from evaluator.process_trace import build_process, build_process_from_entries


def _call(code, call_id="c1", t_ms=100):
    command = f"cd /root && leakage-agent-cli invoke {code} --param pageNo=0"
    return {
        "kind": "function_call",
        "t_ms": t_ms,
        "payload": {
            "type": "function_call",
            "name": "exec",
            "call_id": call_id,
            "arguments": json.dumps({"command": command}, ensure_ascii=False),
        },
    }


def _output(text, call_id="c1", t_ms=600):
    payload = {
        "type": "function_call_output",
        "call_id": call_id,
        "output": json.dumps(
            {"content": [{"type": "text", "text": text}], "details": {"status": "completed"}},
            ensure_ascii=False,
        ),
    }
    return {"kind": "function_call_output", "t_ms": t_ms, "payload": payload}


def _entries():
    return [
        _call("water_supply_pipe_maintenance_work"),
        _output("ws(bz_bm) -> totalRows: 0 | futian -> totalRows: 4634"),
    ]


def _answer(entries):
    return AgentAnswer(
        case_id="T-001",
        question="q",
        text="answer",
        latency_ms=1000,
        timings={"transport_total_ms": 1234.5},
        completion_status="completed",
        response_body={"process": entries},
    )


def test_from_entries_matches_build_process():
    entries = _entries()
    direct = build_process(_answer(entries))
    rebuilt = build_process_from_entries(
        entries,
        case_id="T-001",
        timings={"transport_total_ms": 1234.5},
        completion_status="completed",
        answer_text="answer",
    )
    assert rebuilt["summary"] == direct["summary"]
    assert rebuilt["counts"] == direct["counts"]
    assert rebuilt["review"] == direct["review"]


def test_rebuild_uses_max_rows_and_rejudges_process():
    # 旧存档把这一条记成「空结果」，实际同一输出里后面取到了 4634 行。
    process = {
        "summary": {"total_ms": 1234.5, "completion_status": "completed", "error": None},
        "counts": {"tool_calls": 1, "shell": 1, "ok": 0, "empty": 1, "error": 0, "timeout": 0, "pending": 0},
        "review": {"verdict": "PROCESS_ATTENTION", "findings": [{"code": "EMPTY_RESULT_HIGH"}]},
        "timeline": [],
    }
    result = {"completion_status": "completed", "agent_timings": {"transport_total_ms": 1234.5}}
    payload = rebuild_payload(process, _entries(), case_id="T-001", result=result, answer_text="answer")

    step = [item["step"] for item in payload["timeline"] if item["kind"] == "step"][0]
    assert step["status"] == "ok"
    assert step["rows"] == 4634
    assert payload["counts"]["empty"] == 0
    assert payload["review"]["verdict"] == "PROCESS_HEALTHY"


def test_rebuild_keeps_all_zero_probe_empty():
    entries = [_call("year_missed_rate"), _output("a -> totalRows: 0 | b -> totalRows: 0")]
    process = {"summary": {}, "counts": {}, "review": {}, "timeline": []}
    payload = rebuild_payload(
        process,
        entries,
        case_id="T-002",
        result={"completion_status": "completed"},
        answer_text="",
    )
    step = [item["step"] for item in payload["timeline"] if item["kind"] == "step"][0]
    assert step["status"] == "empty"
    assert payload["counts"]["empty"] == 1
