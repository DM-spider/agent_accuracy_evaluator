# -*- coding: utf-8 -*-
import json

import httpx

from evaluator.agent_client import AgentClient
from evaluator.models import CaseContract, MeasureSpec
from evaluator.run_context import build_run_context


def _case():
    return CaseContract(
        case_id="CX01",
        question="集团本月产销差率是多少？",
        result_type="stat",
        numeric_evaluable=True,
        measures={"产销差率": MeasureSpec(label="产销差率", unit="%")},
    )


def _ctx():
    return build_run_context("2026-08-31T10:00:00+08:00")


def test_success_and_secret_redaction():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert "sql" not in body
        assert body["case_id"] == "CX01"
        assert "2026-08-31" in body["anchor_time"]
        return httpx.Response(200, json={"answer": "产销差率 5.51%", "model": "demo"})

    client = AgentClient(
        "http://agent/ask",
        transport=httpx.MockTransport(handler),
        sleep=lambda _s: None,
        request_template='{"question":"{question}","case_id":"{case_id}","anchor_time":"{anchor_time}"}',
    )
    ans = client.ask(_case(), _ctx())
    assert ans.text == "产销差率 5.51%"
    assert ans.error is None


def test_timeout_non200_path_error_and_retry():
    hits = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        hits["n"] += 1
        if hits["n"] < 3:
            return httpx.Response(500, json={"error": "boom"})
        return httpx.Response(200, json={"data": {"answer": "ok"}})

    client = AgentClient(
        "http://agent/ask",
        transport=httpx.MockTransport(handler),
        retries=2,
        answer_path="data.answer",
        sleep=lambda _s: None,
    )
    ans = client.ask(_case(), _ctx())
    assert ans.text == "ok"
    assert ans.retries == 2

    def bad_path(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"foo": 1})

    client2 = AgentClient("http://agent/ask", transport=httpx.MockTransport(bad_path), retries=0, sleep=lambda _s: None)
    ans2 = client2.ask(_case(), _ctx())
    assert ans2.error
