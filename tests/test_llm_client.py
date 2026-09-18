# -*- coding: utf-8 -*-
import json

import httpx

from evaluator.llm_client import LlmEvaluator
from evaluator.llm_evidence import build_evaluation_evidence
from evaluator.models import REQUIRED_DIMENSIONS, CaseContract, MeasureSpec, SqlSnapshot

API_KEY = "sk-secret-key"


def evidence(text="产销差率 5.3%", max_input_chars=500000):
    contract = CaseContract(
        case_id="CX-001",
        question="本月产销差率是多少？",
        result_type="stat",
        numeric_evaluable=True,
        measures={"产销差率": MeasureSpec(label="产销差率", unit="%", sql_column="rate")},
    )
    snapshot = SqlSnapshot(columns=["rate"], rows=[{"rate": 5.3}], row_count=1)
    return build_evaluation_evidence(
        contract, answer_text=text, sql_snapshot=snapshot, max_input_chars=max_input_chars
    )


def content(verdict="QUALIFIED", codes=(), confidence=0.95, dims="MATCH", diffs=()):
    dimensions = {key: {"status": dims, "reason": ""} for key in REQUIRED_DIMENSIONS}
    return json.dumps(
        {
            "overall_verdict": verdict,
            "confidence": confidence,
            "summary": "结论",
            "primary_issue_code": codes[0] if codes else None,
            "issue_codes": list(codes),
            "dimensions": dimensions,
            "differences": list(diffs),
            "needs_human_review": False,
        },
        ensure_ascii=False,
    )


def evaluator(handler, **overrides):
    cfg = {
        "enabled": True,
        "base_url": "https://api.example.com/v1",
        "model": "deepseek-flash",
        "api_key": API_KEY,
        "timeout_seconds": 5,
        "retries": 0,
        "temperature": 0,
        "max_output_tokens": 8000,
        "max_input_chars": 500000,
        "min_confidence": 0.0,
        "prompt_version": "v1",
    }
    cfg.update(overrides)
    calls = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return handler(request)

    client = LlmEvaluator(cfg, transport=httpx.MockTransport(wrapped), sleep=lambda _s: None)
    return client, calls


def response_with(text, status=200, usage=True):
    body = {"choices": [{"message": {"content": text}}]}
    if usage:
        body["usage"] = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
    return httpx.Response(status, json=body)


def test_successful_evaluation_records_metadata():
    def handler(request):
        payload = json.loads(request.content)
        assert payload["model"] == "deepseek-flash"
        assert payload["temperature"] == 0
        assert payload["response_format"] == {"type": "json_object"}
        assert request.headers["Authorization"] == f"Bearer {API_KEY}"
        assert request.url.path.endswith("/chat/completions")
        assert payload["messages"][0]["role"] == "system"
        return response_with(content())

    client, calls = evaluator(handler)
    ev = evidence()
    artifact = client.evaluate(ev)
    assert artifact.evaluation.overall_verdict.value == "QUALIFIED"
    assert artifact.model == "deepseek-flash"
    assert artifact.prompt_version == "v1"
    assert artifact.input_hash == ev.input_hash
    assert artifact.token_usage == {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
    assert artifact.latency_ms >= 0
    assert len(calls) == 1


def test_markdown_fenced_json_is_cleaned_once():
    client, calls = evaluator(lambda request: response_with("```json\n" + content() + "\n```"))
    artifact = client.evaluate(evidence())
    assert artifact.evaluation.overall_verdict.value == "QUALIFIED"
    assert len(calls) == 1


def test_invalid_json_retries_then_reports_invalid():
    client, calls = evaluator(lambda request: response_with("not json at all"), retries=2)
    artifact = client.evaluate(evidence())
    assert artifact.evaluation.overall_verdict.value == "UNEVALUABLE"
    assert artifact.evaluation.primary_issue_code == "LLM_RESPONSE_INVALID"
    assert len(calls) == 3
    assert "invalid_response" in (artifact.error or "")


def test_timeout_and_5xx_are_retried():
    seen = {"n": 0}

    def handler(request):
        seen["n"] += 1
        if seen["n"] == 1:
            raise httpx.ReadTimeout("boom")
        if seen["n"] == 2:
            return httpx.Response(500, json={"error": "server"})
        return response_with(content())

    client, calls = evaluator(handler, retries=3)
    artifact = client.evaluate(evidence())
    assert artifact.evaluation.overall_verdict.value == "QUALIFIED"
    assert len(calls) == 3


def test_401_is_not_retried_and_hides_key():
    client, calls = evaluator(lambda request: httpx.Response(401, json={"error": "unauthorized"}), retries=3)
    artifact = client.evaluate(evidence())
    assert len(calls) == 1
    assert artifact.evaluation.primary_issue_code == "LLM_CALL_FAILED"
    dumped = artifact.model_dump_json()
    assert API_KEY not in dumped
    assert "http_401" in dumped


def test_low_confidence_becomes_unevaluable():
    client, _ = evaluator(lambda request: response_with(content(confidence=0.3)), min_confidence=0.7)
    artifact = client.evaluate(evidence())
    assert artifact.evaluation.overall_verdict.value == "UNEVALUABLE"
    assert artifact.evaluation.needs_human_review is True
    assert any("置信度" in note for note in artifact.validation_notes)


def test_oversized_evidence_skips_http():
    def handler(request):
        raise AssertionError("should not call the model")

    client, calls = evaluator(handler)
    artifact = client.evaluate(evidence(max_input_chars=50))
    assert artifact.evaluation.primary_issue_code == "LLM_INPUT_TOO_LARGE"
    assert len(calls) == 0


def test_disabled_or_unconfigured_returns_unevaluable():
    client, calls = evaluator(lambda request: response_with(content()), enabled=False)
    artifact = client.evaluate(evidence())
    assert artifact.evaluation.primary_issue_code == "LLM_CALL_FAILED"
    assert len(calls) == 0
    client2 = LlmEvaluator({"enabled": True, "model": "m"}, transport=httpx.MockTransport(lambda r: response_with(content())))
    assert client2.evaluate(evidence()).evaluation.primary_issue_code == "LLM_CALL_FAILED"
