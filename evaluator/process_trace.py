# -*- coding: utf-8 -*-
"""把平台 SSE 的中间过程归一化成可展示的执行记录。

平台在 ``response.output_item.done`` 里给出完整条目：reasoning（推理摘要）、
message（过程叙述）、function_call（工具调用及完整命令）、function_call_output
（工具返回）。这些内容原先在解析时被丢弃，本模块负责：

1. ``TraceRecorder``：有界地收集条目，顺带做截断与凭证脱敏；
2. ``build_process``：把条目归一化成时间轴（步骤 / 推理 / 叙述），并统计失败、
   超时、空返回等过程信号，供「执行记录」页签渲染。

本模块只做展示与统计，不参与评分，也不改变既有判定口径。
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Sequence

from evaluator.process_review import review_process

# 单题上限：条目数与单条文本都要有界，避免一次长任务把内存和详情接口撑爆。
MAX_ITEMS = 400
MAX_ITEM_TEXT = 8000
MAX_STEPS = 200
MAX_THOUGHTS = 80
THOUGHT_CHARS = 400
EXCERPT_CHARS = 220
COMMAND_CHARS = 400

# 三种实测形态：直接 CLI、heredoc 里的 invoke("code", {...})、以及非取数 shell。
CLI_INVOKE_RE = re.compile(r"leakage-agent-cli\s+invoke\s+([A-Za-z0-9_.\-]+)((?:\s+--param\s+\S+)*)")
PY_INVOKE_RE = re.compile(r"invoke\(\s*[\"']([A-Za-z0-9_.\-]+)[\"']\s*,\s*(\{.*?\})\s*\)", re.S)
PARAM_RE = re.compile(r"--param\s+([A-Za-z0-9_\-]+)=(\S+)")
ROW_COUNT_RE = re.compile(r"(?:totalRows|total|rows|TOTAL|total_rows)[\"']?\s*[=:]\s*(\d+)", re.I)
NO_DATA_RE = re.compile(r"(无数据|未发布|no\s*data|空结果|结果为空)", re.I)

TERMINAL_KINDS = {
    "response.created", "response.completed", "response.failed",
    "response.cancelled", "response.incomplete", "error",
}


def _redact(text: str, secret: str) -> str:
    value = str(text or "")
    if secret:
        value = value.replace(secret, "[REDACTED]")
    return value


def _truncate(value: Any, limit: int) -> Any:
    if isinstance(value, str):
        return value if len(value) <= limit else value[:limit]
    return value


class TraceRecorder:
    """有界收集 output_item 条目，供落盘与展示。"""

    def __init__(self, secret: str = ""):
        self.secret = secret or ""
        self._items: List[Dict[str, Any]] = []
        self.truncated = False

    def add(self, kind: str, elapsed_ms: float, payload: Optional[Dict[str, Any]] = None) -> None:
        if len(self._items) >= MAX_ITEMS:
            self.truncated = True
            return
        self._items.append({
            "seq": len(self._items) + 1,
            "t_ms": round(float(elapsed_ms or 0), 1),
            "kind": str(kind or "item"),
            "payload": self._sanitize(payload or {}),
        })

    def _sanitize(self, value: Any, depth: int = 0) -> Any:
        """递归脱敏与截断，但保留 dict/list 结构——下游要按 summary/content 取值。"""
        if depth > 6:
            return "[TRUNCATED]"
        if isinstance(value, str):
            return _truncate(_redact(value, self.secret), MAX_ITEM_TEXT)
        if isinstance(value, dict):
            return {str(key): self._sanitize(item, depth + 1) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [self._sanitize(item, depth + 1) for item in value[:40]]
        if isinstance(value, (int, float, bool)) or value is None:
            return value
        return _truncate(_redact(str(value), self.secret), MAX_ITEM_TEXT)

    def items(self) -> List[Dict[str, Any]]:
        return list(self._items)


def _loads(text: Any) -> Any:
    if isinstance(text, (dict, list)):
        return text
    try:
        return json.loads(str(text or ""))
    except (TypeError, ValueError):
        return None


def _first_line(text: str) -> str:
    for line in str(text or "").splitlines():
        if line.strip():
            return line.strip()
    return ""


def parse_cli_invocations(command: str) -> List[Dict[str, Any]]:
    """从 shell 命令里抽出 leakage-agent-cli 的取数调用。"""
    text = str(command or "")
    calls: List[Dict[str, Any]] = []
    seen = set()
    for code, params_blob in CLI_INVOKE_RE.findall(text):
        params = {key: value for key, value in PARAM_RE.findall(params_blob or "")}
        call = {"code": code, "params": params}
        signature = json.dumps(call, ensure_ascii=False, sort_keys=True)
        if signature not in seen:
            seen.add(signature)
            calls.append(call)
    for code, raw_params in PY_INVOKE_RE.findall(text):
        params = _loads(raw_params)
        call = {"code": code, "params": params if isinstance(params, dict) else {}}
        signature = json.dumps(call, ensure_ascii=False, sort_keys=True, default=str)
        if signature not in seen:
            seen.add(signature)
            calls.append(call)
    return calls


def _summary_text(item: Dict[str, Any]) -> str:
    parts = []
    for entry in item.get("summary") or []:
        if isinstance(entry, dict) and entry.get("text"):
            parts.append(str(entry["text"]))
    return "\n".join(parts).strip()


def _message_text(item: Dict[str, Any]) -> str:
    parts = []
    for entry in item.get("content") or []:
        if isinstance(entry, dict) and entry.get("type") == "output_text" and entry.get("text"):
            parts.append(str(entry["text"]))
    return "\n".join(parts).strip()


def _output_text(item: Dict[str, Any]) -> tuple:
    data = _loads(item.get("output"))
    if isinstance(data, dict):
        text = ""
        content = data.get("content")
        if isinstance(content, list) and content and isinstance(content[0], dict):
            text = str(content[0].get("text") or "")
        if not text:
            text = str(data.get("aggregated") or "")
        details = data.get("details") if isinstance(data.get("details"), dict) else {}
        return text, details
    return str(item.get("output") or ""), {}


def _row_counts(text: str) -> List[int]:
    return [int(match.group(1)) for match in ROW_COUNT_RE.finditer(text or "")]


def _row_count(text: str) -> Optional[int]:
    """一次调用可能连续试多个参数：取所有行数的最大值，避免第一个 0 行掩盖后面的成功。"""
    counts = _row_counts(text)
    return max(counts) if counts else None


def _outcome(details: Dict[str, Any], text: str) -> str:
    """ok / empty / error / timeout —— 空结果与失败必须分开统计。"""
    if details.get("status") == "failed" or details.get("exitSignal") or details.get("timedOut"):
        reason = str(details.get("exitReason") or "")
        return "timeout" if ("timeout" in reason.lower() or details.get("timedOut")) else "error"
    if not details and not text:
        return "unknown"
    payload = _loads(text)
    if isinstance(payload, dict):
        code = str(payload.get("respCode") or "").upper()
        if code and code != "SUCCESS":
            return "error"
    counts = _row_counts(text)
    if counts:
        return "empty" if all(count == 0 for count in counts) else "ok"
    if NO_DATA_RE.search(text or ""):
        return "empty"
    return "ok"


def _excerpt(text: str) -> str:
    flat = re.sub(r"\s+", " ", str(text or "")).strip()
    return flat[:EXCERPT_CHARS]


def _call_step(item: Dict[str, Any], t_ms: float) -> Dict[str, Any]:
    name = str(item.get("name") or "tool")
    arguments = _loads(item.get("arguments"))
    if not isinstance(arguments, dict):
        arguments = {}
    command = arguments.get("command")
    calls = parse_cli_invocations(command) if isinstance(command, str) else []
    step: Dict[str, Any] = {
        "type": "poll" if name == "process" else "tool",
        "tool": name,
        "call_id": item.get("call_id") or "",
        "t_ms": t_ms,
        "status": "pending",
        "codes": [call["code"] for call in calls],
        "calls": calls,
        "params": calls[0]["params"] if calls else {},
        "timeout": arguments.get("timeout"),
        "action": arguments.get("action"),
    }
    if isinstance(command, str) and command.strip():
        step["command"] = _truncate(command.strip(), COMMAND_CHARS)
        step["command_line"] = _first_line(command)[:COMMAND_CHARS]
    if not calls and isinstance(command, str) and command.strip() and name != "process":
        # 非取数的 shell（ls/cat/自检脚本），只计数不参与命令命中率
        step["type"] = "shell"
    return step


def _apply_output(step: Dict[str, Any], item: Dict[str, Any], t_ms: float) -> None:
    text, details = _output_text(item)
    step["status"] = _outcome(details, text)
    step["finished_ms"] = t_ms
    if step.get("t_ms") is not None:
        step["latency_ms"] = round(float(t_ms) - float(step["t_ms"]), 1)
    if details.get("durationMs") is not None:
        step["server_ms"] = details.get("durationMs")
    step["rows"] = _row_count(text)
    step["exit_reason"] = details.get("exitReason")
    step["excerpt"] = _excerpt(text)


def build_process(answer: Any) -> Dict[str, Any]:
    """把一次问答的中间条目归一化成时间轴与过程统计。"""
    body = getattr(answer, "response_body", None) or {}
    timings = dict(getattr(answer, "timings", None) or {})
    if not timings.get("transport_total_ms"):
        timings["transport_total_ms"] = getattr(answer, "latency_ms", None)
    return build_process_from_entries(
        body.get("process") or [],
        case_id=str(getattr(answer, "case_id", "") or ""),
        timings=timings,
        completion_status=str(getattr(answer, "completion_status", "") or ""),
        error=str(getattr(answer, "error", "") or ""),
        answer_text=str(getattr(answer, "text", "") or ""),
        truncated=bool(body.get("process_truncated")),
    )


def build_process_from_entries(
    entries: Sequence[Dict[str, Any]],
    *,
    case_id: str = "",
    timings: Optional[Dict[str, Any]] = None,
    completion_status: str = "",
    error: str = "",
    answer_text: str = "",
    truncated: bool = False,
) -> Dict[str, Any]:
    """同一套归一化逻辑，直接吃原始条目——回填旧存档时用。"""
    steps: List[Dict[str, Any]] = []
    thoughts: List[Dict[str, Any]] = []
    notes: List[Dict[str, Any]] = []
    events: List[Dict[str, Any]] = []
    pending: Dict[str, Dict[str, Any]] = {}

    for entry in entries:
        kind = str(entry.get("kind") or "")
        payload = entry.get("payload") or {}
        t_ms = entry.get("t_ms")
        if kind in TERMINAL_KINDS:
            events.append({"t_ms": t_ms, "type": str(payload.get("type") or kind)})
            continue
        if kind == "reasoning":
            text = _summary_text(payload)
            if text:
                thoughts.append({"t_ms": t_ms, "text": text[:THOUGHT_CHARS]})
            continue
        if kind == "message":
            text = _message_text(payload)
            if text:
                notes.append({"t_ms": t_ms, "text": text[:THOUGHT_CHARS]})
            continue
        if kind == "function_call":
            step = _call_step(payload, t_ms)
            steps.append(step)
            call_id = str(payload.get("call_id") or "")
            if call_id:
                pending[call_id] = step
            continue
        if kind == "function_call_output":
            call_id = str(payload.get("call_id") or "")
            step = pending.get(call_id)
            if step is None:
                step = {
                    "type": "tool", "tool": "output", "call_id": call_id,
                    "t_ms": t_ms, "status": "pending", "codes": [], "calls": [], "params": {},
                }
                steps.append(step)
            _apply_output(step, payload, t_ms)
            continue

    counts = {"tool_calls": 0, "shell": 0, "poll": 0, "ok": 0, "empty": 0, "error": 0, "timeout": 0, "pending": 0}
    first_tool_ms = None
    for step in steps:
        counts["tool_calls"] += 1
        if step["type"] == "shell":
            counts["shell"] += 1
        elif step["type"] == "poll":
            counts["poll"] += 1
        status = step.get("status") or "pending"
        if status in counts:
            counts[status] += 1
        if first_tool_ms is None and step.get("t_ms") is not None:
            first_tool_ms = step["t_ms"]

    timings = timings or {}
    summary = {
        "steps": len(steps),
        "thoughts": len(thoughts),
        "notes": len(notes),
        "counts": counts,
        "first_tool_ms": first_tool_ms,
        "total_ms": timings.get("transport_total_ms"),
        "completion_status": completion_status,
        "error": error or None,
    }

    timeline: List[Dict[str, Any]] = []
    for step in steps:
        timeline.append({
            "kind": "step",
            "t_ms": step.get("t_ms"),
            "step": step,
        })
    for item in thoughts:
        timeline.append({"kind": "thought", "t_ms": item["t_ms"], "text": item["text"]})
    for item in notes:
        timeline.append({"kind": "note", "t_ms": item["t_ms"], "text": item["text"]})
    timeline.sort(key=lambda item: (item.get("t_ms") is None, item.get("t_ms") or 0))

    flags: List[str] = []
    if counts["timeout"]:
        flags.append("TOOL_TIMEOUT")
    if counts["error"]:
        flags.append("TOOL_ERROR")
    if counts["empty"] >= max(1, counts["tool_calls"] // 2):
        flags.append("EMPTY_RESULT_HIGH")
    if counts["tool_calls"] >= 20:
        flags.append("MANY_STEPS")
    if truncated:
        flags.append("TRACE_TRUNCATED")

    return {
        "schema_version": "v1",
        "case_id": case_id,
        "summary": summary,
        "counts": counts,
        "flags": flags,
        "review": review_process(
            summary=summary,
            counts=counts,
            steps=steps,
            completion_status=str(completion_status or ""),
            error=str(error or ""),
            answer_text=str(answer_text or ""),
        ),
        "timeline": timeline[: MAX_STEPS * 2],
        "steps": steps[:MAX_STEPS],
        "thoughts": thoughts[:MAX_THOUGHTS],
        "notes": notes[:MAX_THOUGHTS],
        "events": events[:MAX_THOUGHTS],
        "truncated": truncated,
        "trace": list(entries),
    }
