# -*- coding: utf-8 -*-
"""OpenAI-compatible 评估客户端。

配置全部来自 settings[\"evaluator_llm\"]。任何失败都转为 LlmEvaluationArtifact，
不让单题失败中断批次；不把 api_key、请求头或完整请求体写入日志。
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

import httpx

from evaluator.llm_evaluation import (
    artifact_from_evaluation,
    enforce_decision,
    unevaluable_artifact,
    unevaluable_result,
)
from evaluator.llm_evidence import EvaluationEvidence
from evaluator.llm_prompt import build_messages
from evaluator.models import DimensionEvaluation, EvaluationVerdict, LlmEvaluationArtifact, LlmEvaluationResult

PROVIDER = "openai-compatible"


def _now() -> str:
    return datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds")


def _strip_code_fence(text: str) -> str:
    """只做一次受控清理：剥离整段包裹的 Markdown 代码块。"""
    stripped = (text or "").strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if len(lines) < 2:
        return stripped
    body = lines[1:]
    if body and body[-1].strip().startswith("```"):
        body = body[:-1]
    return "\n".join(body).strip()


class _Retryable(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class LlmEvaluator:
    def __init__(
        self,
        cfg: Optional[Dict[str, Any]] = None,
        *,
        transport: Optional[httpx.BaseTransport] = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        cfg = cfg or {}
        self.enabled = bool(cfg.get("enabled", True))
        self.base_url = str(cfg.get("base_url") or "").rstrip("/")
        self.model = str(cfg.get("model") or "")
        self.api_key = str(cfg.get("api_key") or "")
        self.timeout_seconds = float(cfg.get("timeout_seconds") or 90)
        self.retries = max(0, int(cfg.get("retries") or 0))
        self.temperature = float(cfg.get("temperature") or 0)
        self.max_output_tokens = int(cfg.get("max_output_tokens") or 8000)
        self.max_input_chars = int(cfg.get("max_input_chars") or 500000)
        self.min_confidence = float(cfg.get("min_confidence") or 0.0)
        self.prompt_version = str(cfg.get("prompt_version") or "v1")
        self.provider = PROVIDER
        self.sleep = sleep
        self._client = httpx.Client(transport=transport, timeout=self.timeout_seconds) if transport else None

    @property
    def completions_url(self) -> str:
        return f"{self.base_url}/chat/completions"

    @property
    def configured(self) -> bool:
        return bool(self.enabled and self.base_url and self.model and self.api_key)

    def close(self) -> None:
        if self._client is not None:
            self._client.close()

    def _sanitize(self, text: Optional[str]) -> Optional[str]:
        if not text:
            return text
        cleaned = str(text)
        if self.api_key:
            cleaned = cleaned.replace(self.api_key, "***")
        return cleaned

    def _request(self, messages: List[Dict[str, str]]) -> Dict[str, Any]:
        body = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_output_tokens,
            "response_format": {"type": "json_object"},
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        try:
            if self._client is not None:
                response = self._client.post(self.completions_url, json=body, headers=headers)
            else:
                with httpx.Client(timeout=self.timeout_seconds) as client:
                    response = client.post(self.completions_url, json=body, headers=headers)
        except httpx.TimeoutException as exc:
            raise _Retryable("timeout") from exc
        except httpx.HTTPError as exc:
            raise _Retryable(type(exc).__name__) from exc
        status = response.status_code
        if status in {401, 403}:
            raise PermissionError(f"http_{status}")
        if status == 429 or status >= 500:
            raise _Retryable(f"http_{status}")
        if status != 200:
            raise PermissionError(f"http_{status}")
        return response.json()

    def _extract_content(self, payload: Dict[str, Any]) -> str:
        choices = payload.get("choices") or []
        if not choices:
            raise ValueError("response has no choices")
        message = choices[0].get("message") or {}
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            raise ValueError("response content is empty")
        return content

    def _parse(self, content: str) -> LlmEvaluationResult:
        cleaned = _strip_code_fence(content)
        data = json.loads(cleaned)
        return LlmEvaluationResult.model_validate(data)

    def _usage(self, payload: Dict[str, Any]) -> Dict[str, int]:
        usage = payload.get("usage") or {}
        return {
            key: int(usage[key])
            for key in ("prompt_tokens", "completion_tokens", "total_tokens")
            if isinstance(usage.get(key), int)
        }

    def _min_confidence_result(self, result: LlmEvaluationResult) -> LlmEvaluationResult:
        dimensions = {
            key: DimensionEvaluation(status=value.status, reason=value.reason)
            for key, value in result.dimensions.items()
        }
        return LlmEvaluationResult(
            overall_verdict=EvaluationVerdict.UNEVALUABLE,
            confidence=result.confidence,
            summary="置信度低于阈值，需要人工复核",
            primary_issue_code=result.primary_issue_code or "INSUFFICIENT_EVIDENCE",
            issue_codes=result.issue_codes or ["INSUFFICIENT_EVIDENCE"],
            dimensions=dimensions,
            differences=result.differences,
            needs_human_review=True,
        )

    def evaluate(self, evidence: EvaluationEvidence, contract: Any = None) -> LlmEvaluationArtifact:
        if evidence.too_large:
            return unevaluable_artifact(
                "LLM_INPUT_TOO_LARGE",
                "证据包超过配置上限，未调用 LLM",
                provider=self.provider,
                model=self.model,
                prompt_version=self.prompt_version,
                input_hash=evidence.input_hash,
            )
        if not self.enabled:
            return unevaluable_artifact(
                "LLM_CALL_FAILED", "评估 LLM 已禁用", provider=self.provider,
                model=self.model, prompt_version=self.prompt_version, input_hash=evidence.input_hash,
            )
        if not self.configured:
            return unevaluable_artifact(
                "LLM_CALL_FAILED", "评估 LLM 配置不完整", provider=self.provider,
                model=self.model, prompt_version=self.prompt_version, input_hash=evidence.input_hash,
            )
        messages = build_messages(evidence)
        attempts = self.retries + 1
        last_error = ""
        for attempt in range(attempts):
            started = time.perf_counter()
            try:
                payload = self._request(messages)
            except _Retryable as exc:
                last_error = exc.reason
                if attempt < attempts - 1:
                    self.sleep(float(attempt + 1))
                    continue
                break
            except PermissionError as exc:
                last_error = str(exc)
                break
            except Exception as exc:  # 响应解析前的未知错误也按失败处理
                last_error = type(exc).__name__
                break
            latency_ms = int((time.perf_counter() - started) * 1000)
            usage = self._usage(payload)
            try:
                content = self._extract_content(payload)
                result = self._parse(content)
            except Exception as exc:
                last_error = f"invalid_response:{type(exc).__name__}"
                if attempt < attempts - 1:
                    self.sleep(float(attempt + 1))
                    continue
                return LlmEvaluationArtifact(
                    evaluation=unevaluable_result("LLM_RESPONSE_INVALID", "评估 LLM 返回非法结构"),
                    provider=self.provider,
                    model=self.model,
                    prompt_version=self.prompt_version,
                    input_hash=evidence.input_hash,
                    created_at=_now(),
                    latency_ms=latency_ms,
                    token_usage=usage,
                    validation_notes=[self._sanitize(last_error) or "invalid_response"],
                    error=self._sanitize(last_error),
                    raw_response=locals().get("content"),
                )
            result, notes = enforce_decision(result)
            if result.confidence < self.min_confidence:
                notes.append(f"置信度 {result.confidence:.2f} 低于阈值 {self.min_confidence:.2f}")
                result = self._min_confidence_result(result)
            return artifact_from_evaluation(
                result,
                provider=self.provider,
                model=self.model,
                prompt_version=self.prompt_version,
                input_hash=evidence.input_hash,
                latency_ms=latency_ms,
                token_usage=usage,
                validation_notes=[self._sanitize(note) or "" for note in notes],
            )
        return unevaluable_artifact(
            "LLM_CALL_FAILED",
            f"评估 LLM 调用失败：{self._sanitize(last_error) or 'unknown'}",
            provider=self.provider,
            model=self.model,
            prompt_version=self.prompt_version,
            input_hash=evidence.input_hash,
            error=f"{self._sanitize(last_error) or 'unknown'}; retries={attempts - 1}",
        )
