"""Water-company SSE adapter. A disconnected stream is never a completed turn."""
from __future__ import annotations

import json
import codecs
import re
import time
from collections import Counter
from datetime import datetime, timezone
from typing import Iterable

import httpx

from evaluator.models import AgentAnswer, CaseContract, RunContext

STREAM_URL = "https://aiemployee.sz-water.com.cn/cc-control/api/user/mid-platform/session/message/stream"
TIME_KEYS = {"period", "businessyearmonth", "yearmonth", "ym", "start_date", "end_date", "date_start", "date_end"}


def sse_frames(lines: Iterable[str]):
    data, event = [], ""
    for line in lines:
        if not line:
            if data:
                yield event, "\n".join(data)
            data, event = [], ""
        elif not line.startswith(":"):
            field, sep, value = line.partition(":")
            value = value[1:] if value.startswith(" ") else value
            if field == "data" and sep:
                data.append(value)
            elif field == "event":
                event = value
    # An unterminated frame is not evidence of completion.


def output_text(response: dict) -> str:
    answer = ""
    for item in response.get("output") or []:
        if item.get("role") != "assistant":
            continue
        parts = []
        for part in item.get("content") or []:
            if part.get("type") == "output_text" and isinstance(part.get("text"), str):
                parts.append(part["text"])
        if parts:
            answer = "\n".join(parts)
    return answer


def public_remote_error(payload: dict, response: dict, credential: str) -> dict:
    raw = payload.get("error") or response.get("error") or {}
    if isinstance(raw, str):
        raw = {"message": raw}
    if not isinstance(raw, dict):
        return {}
    out = {}
    for key in ("code", "type", "message"):
        value = raw.get(key)
        if not isinstance(value, (str, int)):
            continue
        text = str(value).replace(credential, "[REDACTED]") if credential else str(value)
        text = re.sub(r"(?i)Bearer\s+\S+", "Bearer [REDACTED]", text)
        text = re.sub(r"(?i)(password|token|api[_-]?key|cookie|session_id)\s*[:=]\s*[^\s,;]+", r"\1=[REDACTED]", text)
        out[key] = text[:1000]
    return out


def safe_time_hints(item: dict) -> list:
    """Extract validated dates from tool arguments without retaining the payload."""
    if not isinstance(item, dict) or item.get("type") != "function_call":
        return []
    raw = item.get("arguments")
    try:
        payload = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, json.JSONDecodeError):
        return []
    hints = []

    def walk(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if str(key).lower() in TIME_KEYS:
                    text = str(child)
                    if re.fullmatch(r"20\d{2}(?:0[1-9]|1[0-2])|20\d{2}[-/](?:0[1-9]|1[0-2])(?:[-/]\d{2})?", text):
                        hints.append({"field": str(key).lower(), "value": text})
                elif isinstance(child, (dict, list)):
                    walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(payload)
    return hints[:20]


class StreamAgentClient:
    sequential = True

    def __init__(self, *, session_id: str, task_id: str, template_id: str, org_id: str,
                 login_session: str, task_name: str = "leakage-skill", url: str = STREAM_URL,
                 read_timeout: float = 60, total_timeout: float = 300, trust_env: bool = False, transport=None):
        self.url = url
        self.session_id = session_id
        self.task_id = task_id
        self.template_id = template_id
        self.org_id = org_id
        self.login_session = login_session
        self.task_name = task_name
        self.read_timeout = read_timeout
        self.total_timeout = total_timeout
        self.transport = transport
        self.trust_env = trust_env
        self.halted = False
        self.uncertain = False

    def close(self):
        pass

    def ask(self, case: CaseContract, run_context: RunContext) -> AgentAnswer:
        answer = AgentAnswer(case_id=case.case_id, question=case.question, http_url=self.url,
                             http_status=0, completion_status="unknown")
        if self.halted:
            answer.error = "SESSION_BLOCKED: previous turn did not complete"
            answer.completion_status = "not_sent"
            return answer
        if not all((self.session_id, self.task_id, self.template_id, self.org_id, self.login_session)):
            answer.error = "PLATFORM_NOT_CONFIGURED"
            answer.completion_status = "not_sent"
            return answer
        body = dict(sessionId=self.session_id, taskId=self.task_id, templateId=self.template_id,
                    taskName=self.task_name, message=case.question)
        answer.request_body = body
        headers = {"accept": "text/event-stream", "Cookie": f"SESSION_ID={self.login_session}",
                   "x-session-id": self.login_session, "x-org-id": self.org_id,
                   "origin": "https://aiemployee.sz-water.com.cn",
                   "referer": "https://aiemployee.sz-water.com.cn/workspace/"}
        started = time.perf_counter()
        elapsed = lambda: round((time.perf_counter() - started) * 1000, 2)
        timings = dict(started_at=datetime.now(timezone.utc).isoformat(), headers_ms=None,
                       first_body_ms=None, first_answer_ms=None, completed_ms=None,
                       server_duration_ms=None)
        counts, events, text_parts, time_hints = Counter(), [], {}, []
        response_id = None
        total_bytes = 0
        remote_error = {}
        try:
            timeout = httpx.Timeout(self.read_timeout, connect=15)
            with httpx.Client(timeout=timeout, transport=self.transport, follow_redirects=False, trust_env=self.trust_env) as client:
                with client.stream("POST", self.url, json=body, headers=headers) as response:
                    timings["headers_ms"] = elapsed()
                    answer.http_status = response.status_code
                    response.raise_for_status()
                    if "text/event-stream" not in response.headers.get("content-type", "").lower():
                        raise ValueError("NOT_SSE_RESPONSE")

                    def bounded_lines():
                        nonlocal total_bytes
                        decoder, buffer = codecs.getincrementaldecoder("utf-8")(), ""
                        for chunk in response.iter_bytes():
                            if timings["first_body_ms"] is None:
                                timings["first_body_ms"] = elapsed()
                            total_bytes += len(chunk)
                            if total_bytes > 4_000_000:
                                raise ValueError("STREAM_SIZE_LIMIT")
                            if elapsed() > self.total_timeout * 1000:
                                raise TimeoutError("STREAM_TOTAL_TIMEOUT")
                            buffer += decoder.decode(chunk)
                            while "\n" in buffer:
                                line, buffer = buffer.split("\n", 1)
                                yield line.rstrip("\r")
                        decoder.decode(b"", final=True)

                    for event_name, raw in sse_frames(bounded_lines()):
                        if raw == "[DONE]":
                            # The observed protocol has response.* events; DONE alone is unverified.
                            continue
                        payload = json.loads(raw)
                        if not isinstance(payload, dict):
                            raise ValueError("INVALID_EVENT")
                        kind = str(payload.get("type") or event_name)
                        counts[kind] += 1
                        if len(events) < 2000:
                            events.append({"type": kind, "elapsed_ms": elapsed()})
                        incoming_session = payload.get("session_id") or payload.get("sessionId")
                        if incoming_session and incoming_session != self.session_id:
                            raise ValueError("SESSION_MISMATCH")
                        obj = payload.get("response") or {}
                        if not isinstance(obj, dict):
                            raise ValueError("INVALID_RESPONSE_OBJECT")
                        incoming_id = obj.get("id") or payload.get("response_id")
                        if incoming_id:
                            if response_id and incoming_id != response_id:
                                raise ValueError("RESPONSE_MISMATCH")
                            response_id = incoming_id
                        if kind == "response.output_text.delta":
                            delta = payload.get("delta")
                            if not isinstance(delta, str):
                                raise ValueError("INVALID_TEXT_DELTA")
                            key = (payload.get("output_index", 0), payload.get("content_index", 0))
                            text_parts[key] = text_parts.get(key, "") + delta
                            if delta and timings["first_answer_ms"] is None:
                                timings["first_answer_ms"] = elapsed()
                        elif kind in {"response.output_item.added", "response.output_item.done"}:
                            for hint in safe_time_hints(payload.get("item") or {}):
                                if hint not in time_hints:
                                    time_hints.append(hint)
                        elif kind in {"response.completed", "response.done"}:
                            if obj.get("status") != "completed":
                                raise ValueError("UNCONFIRMED_TERMINAL_STATUS")
                            final_text = output_text(obj)
                            last_index = max((k[0] for k in text_parts), default=0)
                            answer.text = final_text or "\n".join(text_parts[k] for k in sorted(text_parts) if k[0] == last_index)
                            answer.completion_status = "completed"
                            timings["completed_ms"] = elapsed()
                            if not answer.text.strip():
                                answer.error = "EMPTY_COMPLETED_ANSWER"
                            break
                        elif kind in {"response.failed", "response.incomplete", "response.cancelled", "error"}:
                            remote_error = public_remote_error(payload, obj, self.login_session)
                            answer.error = f"REMOTE_{kind.upper()}"
                            answer.completion_status = "failed" if kind != "error" else "unknown"
                            break
                    else:
                        answer.error = "STREAM_EOF_WITHOUT_COMPLETION"
        except Exception as exc:
            # Exception strings / tool payloads can contain credentials or internal reasoning.
            answer.error = f"STREAM_ERROR:{type(exc).__name__}"
            if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout)):
                answer.completion_status = "not_sent"
            if isinstance(exc, ValueError) and str(exc).isupper():
                answer.error += ":" + str(exc)
        finally:
            if not answer.text:
                answer.text = "\n".join(text_parts[k] for k in sorted(text_parts))
            answer.latency_ms = int(elapsed())
            timings["transport_total_ms"] = elapsed()
            timings["finished_at"] = datetime.now(timezone.utc).isoformat()
            answer.timings = timings
            if answer.completion_status != "completed" or answer.error:
                self.halted = True
            self.uncertain = answer.completion_status == "unknown"
            answer.response_body = {"answer": answer.text, "completion_status": answer.completion_status,
                                    "response_id": response_id, "event_counts": dict(counts),
                                    "events": events, "timings": timings, "time_hints": time_hints,
                                    "error": answer.error}
            if remote_error:
                answer.response_body["remote_error"] = remote_error
            # Defense in depth if a response echoes the login cookie.
            if self.login_session:
                answer.text = answer.text.replace(self.login_session, "[REDACTED]")
                answer.response_body["answer"] = answer.text
        return answer
