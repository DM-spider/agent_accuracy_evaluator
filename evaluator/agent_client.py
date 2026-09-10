# -*- coding: utf-8 -*-
"""智能体 HTTP 适配层。不把 SQL 或 SQL 结果发给智能体。"""
from __future__ import annotations

import json
import time
from typing import Any, Callable, Dict, Optional

import httpx

from evaluator.models import AgentAnswer, CaseContract, RunContext
from evaluator.mock_demo import MOCK_AGENT_URL, build_agent_exchange

SENSITIVE_KEYS = ("authorization", "token", "api-key", "x-api-key", "cookie", "x-session-id")


def redact_headers(headers: Dict[str, str]) -> Dict[str, str]:
    out = {}
    for key, value in headers.items():
        if any(s in key.lower() for s in SENSITIVE_KEYS):
            out[key] = "***"
        else:
            out[key] = value
    return out


def resolve_path(payload: Any, path: str) -> Any:
    current = payload
    if not path:
        return payload
    for part in path.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            raise KeyError(f"answer path not found: {path}")
    return current


class AgentClient:
    def __init__(
        self,
        url: str,
        *,
        method: str = "POST",
        timeout_seconds: float = 90,
        retries: int = 2,
        retry_backoff_seconds: float = 1.5,
        headers: Optional[Dict[str, str]] = None,
        answer_path: str = "answer",
        request_template: str = '{"question":"{question}"}',
        transport: Optional[httpx.BaseTransport] = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.url = url
        self.method = method.upper()
        self.timeout_seconds = timeout_seconds
        self.retries = retries
        self.retry_backoff_seconds = retry_backoff_seconds
        self.headers = headers or {}
        self.answer_path = answer_path
        self.request_template = request_template
        self.sleep = sleep
        self._client = httpx.Client(transport=transport, timeout=timeout_seconds) if transport else None

    def close(self) -> None:
        if self._client is not None:
            self._client.close()

    def _build_body(self, case: CaseContract, run_context: RunContext) -> Dict[str, Any]:
        mapping = {
            "question": case.question,
            "case_id": case.case_id,
            "anchor_time": run_context.anchor_time.isoformat(),
            "timezone": run_context.timezone,
            "agent_name": run_context.agent_name,
        }
        rendered = self.request_template
        for key, value in mapping.items():
            rendered = rendered.replace("{" + key + "}", json.dumps(value, ensure_ascii=False)[1:-1] if False else str(value))
        # 用 JSON 编码避免换行破坏
        safe = self.request_template
        for key, value in mapping.items():
            safe = safe.replace("{" + key + "}", json.dumps(str(value), ensure_ascii=False)[1:-1])
        try:
            body = json.loads(safe)
        except json.JSONDecodeError:
            body = {
                "question": case.question,
                "case_id": case.case_id,
                "anchor_time": run_context.anchor_time.isoformat(),
                "timezone": run_context.timezone,
            }
        body.pop("sql", None)
        body.pop("sql_template", None)
        body.pop("sql_result", None)
        return body

    def ask(self, case: CaseContract, run_context: RunContext) -> AgentAnswer:
        if not self.url:
            return AgentAnswer(
                case_id=case.case_id,
                question=case.question,
                error="agent url is not configured",
            )
        body = self._build_body(case, run_context)
        last_error = None
        retries_used = 0
        started = time.perf_counter()
        attempts = self.retries + 1
        for attempt in range(attempts):
            try:
                if self._client is not None:
                    response = self._client.request(self.method, self.url, json=body, headers=self.headers)
                else:
                    with httpx.Client(timeout=self.timeout_seconds) as client:
                        response = client.request(self.method, self.url, json=body, headers=self.headers)
                if response.status_code != 200:
                    last_error = f"HTTP {response.status_code}"
                    retries_used = attempt
                    if attempt < attempts - 1:
                        self.sleep(self.retry_backoff_seconds * (attempt + 1))
                        continue
                    break
                payload = response.json()
                answer = resolve_path(payload, self.answer_path)
                text = answer if isinstance(answer, str) else json.dumps(answer, ensure_ascii=False)
                response_body = payload if isinstance(payload, dict) else {"answer": text}
                return AgentAnswer(
                    case_id=case.case_id,
                    question=case.question,
                    text=text,
                    latency_ms=int((time.perf_counter() - started) * 1000),
                    retries=attempt,
                    model=str(response_body.get("model") or ""),
                    http_url=self.url,
                    http_status=response.status_code,
                    request_body=body,
                    response_body=response_body,
                )
            except httpx.TimeoutException:
                last_error = "timeout"
                retries_used = attempt
                if attempt < attempts - 1:
                    self.sleep(self.retry_backoff_seconds * (attempt + 1))
                    continue
            except Exception as exc:
                last_error = str(exc)
                retries_used = attempt
                if attempt < attempts - 1:
                    self.sleep(self.retry_backoff_seconds * (attempt + 1))
                    continue
        return AgentAnswer(
            case_id=case.case_id,
            question=case.question,
            error=last_error,
            latency_ms=int((time.perf_counter() - started) * 1000),
            retries=retries_used,
        )


class FixtureAgentClient:
    """回放问答稿/样本，报文形状与真实智能体 HTTP 调用一致。"""

    def __init__(
        self,
        answers: Dict[str, str],
        error: Optional[str] = None,
        *,
        url: str = MOCK_AGENT_URL,
        model: str = "mock-mixed-agent",
    ):
        self.answers = answers
        self.error = error
        self.url = url
        self.model = model

    def ask(self, case: CaseContract, run_context: RunContext) -> AgentAnswer:
        if self.error:
            request, _ = build_agent_exchange(
                case.case_id,
                case.question,
                "",
                anchor_time=run_context.anchor_time.isoformat(),
                timezone_name=run_context.timezone,
                model=self.model,
            )
            return AgentAnswer(
                case_id=case.case_id,
                question=case.question,
                error=self.error,
                http_url=self.url,
                http_status=500,
                request_body=request,
                response_body={"ok": False, "error": self.error},
            )
        text = self.answers.get(case.case_id, "")
        request, response = build_agent_exchange(
            case.case_id,
            case.question,
            text,
            anchor_time=run_context.anchor_time.isoformat(),
            timezone_name=run_context.timezone,
            model=self.model,
        )
        return AgentAnswer(
            case_id=case.case_id,
            question=case.question,
            text=text,
            model=self.model,
            http_url=self.url,
            http_status=200,
            request_body=request,
            response_body=response,
        )


def client_from_settings(settings: Dict[str, Any], token: str = "") -> AgentClient:
    agent = settings.get("agent") or {}
    platform = settings.get("platform") or {}
    if platform.get("enabled"):
        from evaluator.stream_client import StreamAgentClient
        from evaluator.settings import platform_login_session
        return StreamAgentClient(
            session_id=platform.get("session_id", ""), task_id=platform.get("task_id", ""),
            template_id=platform.get("template_id", ""), org_id=platform.get("org_id", ""),
            login_session=platform_login_session(settings),
            task_name=platform.get("task_name", "leakage-skill"),
            read_timeout=float(platform.get("read_timeout_seconds", 60)),
            total_timeout=float(platform.get("total_timeout_seconds", 300)),
            trust_env=bool(platform.get("trust_env", False)),
        )
    headers = {}
    if token:
        template = agent.get("header_template") or "Bearer {token}"
        headers[agent.get("header_name") or "Authorization"] = template.replace("{token}", token)
    return AgentClient(
        url=agent.get("url") or "",
        method=agent.get("method") or "POST",
        timeout_seconds=float(agent.get("timeout_seconds") or 90),
        retries=int(agent.get("retries") or 2),
        retry_backoff_seconds=float(agent.get("retry_backoff_seconds") or 1.5),
        headers=headers,
        answer_path=agent.get("answer_path") or "answer",
        request_template=agent.get("request_template") or '{"question":"{question}"}',
    )
