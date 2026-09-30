# -*- coding: utf-8 -*-
"""离线测试假件：回放式智能体客户端、桩 SQL 执行器、确定性评估器（均不联网）。"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from evaluator.models import (
    REQUIRED_DIMENSIONS,
    AgentAnswer,
    CaseContract,
    DimensionEvaluation,
    EvaluationDifference,
    LlmEvaluationResult,
    RunContext,
    SqlSnapshot,
)
from evaluator.sql_executor import SqlExecutor

FIXTURE_AGENT_URL = "http://fixture-agent/ask"

KIND_TO_ISSUE_CODE = {
    "WRONG_VALUE": "WRONG_VALUE",
    "MISSING": "MISSING_ROW",
    "MISSING_ROW": "MISSING_ROW",
    "MISSING_FIELD": "MISSING_FIELD",
}

CODE_TO_DIMENSION = {
    "MISSING_FIELD": "field_coverage",
    "MISSING_ROW": "row_coverage",
    "WRONG_VALUE": "numeric_accuracy",
}


def build_agent_exchange(case_id: str, question: str, answer: str, *, anchor_time: str, timezone_name: str, model: str = "fixture-agent"):
    request = {"question": question, "case_id": case_id, "anchor_time": anchor_time, "timezone": timezone_name}
    response = {"ok": True, "model": model, "case_id": case_id, "answer": answer}
    return request, response


class FixtureAgentClient:
    """回放预置回答，报文形状与真实智能体 HTTP 调用一致。"""

    def __init__(self, answers: Dict[str, str], error: Optional[str] = None, *, url: str = FIXTURE_AGENT_URL, model: str = "fixture-agent"):
        self.answers = answers
        self.error = error
        self.url = url
        self.model = model

    def ask(self, case: CaseContract, run_context: RunContext) -> AgentAnswer:
        if self.error:
            request, _ = build_agent_exchange(
                case.case_id, case.question, "", anchor_time=run_context.anchor_time.isoformat(),
                timezone_name=run_context.timezone, model=self.model,
            )
            return AgentAnswer(
                case_id=case.case_id, question=case.question, error=self.error,
                http_url=self.url, http_status=500, request_body=request,
                response_body={"ok": False, "error": self.error},
            )
        text = self.answers.get(case.case_id, "")
        request, response = build_agent_exchange(
            case.case_id, case.question, text, anchor_time=run_context.anchor_time.isoformat(),
            timezone_name=run_context.timezone, model=self.model,
        )
        return AgentAnswer(
            case_id=case.case_id, question=case.question, text=text, model=self.model,
            http_url=self.url, http_status=200, request_body=request, response_body=response,
        )

    def close(self) -> None:
        pass


class StubSqlExecutor(SqlExecutor):
    """返回固定行的 SQL 桩；error 非空时返回错误快照。"""

    def __init__(self, rows: Optional[List[Dict[str, Any]]] = None, error: Optional[str] = None):
        super().__init__(connect=lambda: None)
        self._rows = rows or [{"产销差率": 5.51}]
        self._error = error

    def query(self, sql_template, params=None) -> SqlSnapshot:
        if self._error:
            return SqlSnapshot(params=params, error=self._error)
        columns = list(self._rows[0].keys()) if self._rows else []
        return SqlSnapshot(params=params, rows=self._rows, columns=columns, row_count=len(self._rows))


def expected_table(case: Dict[str, Any]) -> Tuple[List[str], List[Dict[str, Any]]]:
    """把标准集 expected 转成页面可展示的表格。"""
    exp = case.get("expected") or {}
    columns: List[str] = []
    rows: List[Dict[str, Any]] = []
    fields = exp.get("fields") or []
    detail_rows = list((exp.get("detail") or {}).get("rows") or [])
    if fields and not detail_rows:
        columns = ["指标", "数值", "单位"]
        for field in fields:
            rows.append({
                "指标": field.get("label") or field.get("key"),
                "数值": field.get("value"),
                "单位": field.get("unit") or "",
            })
        return columns, rows
    if detail_rows:
        columns = list(detail_rows[0].keys())
        rows = detail_rows
        if fields:
            extra = {field.get("label") or field.get("key"): field.get("value") for field in fields}
            rows = [extra] + rows
            for key in extra:
                if key not in columns:
                    columns.insert(0, key)
        return columns, rows
    return [], []


def markdown_table(columns: List[str], rows: List[Dict[str, Any]]) -> str:
    lines = ["| " + " | ".join(columns) + " |", "| " + " | ".join("---" for _ in columns) + " |"]
    for row in rows:
        lines.append("| " + " | ".join("" if row.get(c) is None else str(row.get(c)) for c in columns) + " |")
    return "\n".join(lines)


class StubLlmEvaluator:
    """确定性评估器：按预置 outcome 给出结论，用于离线持久化/指标测试。"""

    provider = "stub"

    def __init__(self, outcomes: Optional[Dict[str, Dict[str, str]]] = None, *, default_verdict: str = "QUALIFIED",
                 default_issue_code: Optional[str] = None, model: str = "stub-llm", prompt_version: str = "test"):
        self.outcomes = outcomes or {}
        self.default_verdict = default_verdict
        self.default_issue_code = default_issue_code
        self.model = model
        self.prompt_version = prompt_version

    def evaluate(self, evidence, contract=None):
        from evaluator.llm_evaluation import artifact_from_evaluation

        payload = evidence.payload
        case_id = payload.get("case_id")
        outcome = self.outcomes.get(case_id) or {}
        verdict = str(outcome.get("verdict") or self.default_verdict).upper()
        code = outcome.get("issue_code") or self.default_issue_code
        if verdict == "QUALIFIED":
            code = None
        if verdict in {"UNQUALIFIED", "PARTIAL"} and not code:
            code = "WRONG_VALUE"
        dimensions = {key: DimensionEvaluation(status="MATCH") for key in REQUIRED_DIMENSIONS}
        if code and code in CODE_TO_DIMENSION:
            status = "PARTIAL" if code in {"MISSING_FIELD", "MISSING_ROW"} else "MISMATCH"
            dimensions[CODE_TO_DIMENSION[code]] = DimensionEvaluation(status=status, reason=outcome.get("summary") or "")
        differences = []
        if code:
            differences.append(EvaluationDifference(
                type=code, severity="WARNING" if code in {"MISSING_FIELD", "MISSING_ROW"} else "ERROR",
                field=outcome.get("field") or "", agent_value=outcome.get("agent_value"),
                sql_value=outcome.get("sql_value"), evidence=outcome.get("summary") or "测试评估",
                explanation=outcome.get("summary") or "测试数据中的预设差异",
            ))
        result = LlmEvaluationResult(
            overall_verdict=verdict,
            confidence=0.99 if verdict in {"QUALIFIED", "UNQUALIFIED"} else 0.8,
            summary=outcome.get("summary") or ("测试评估：与 SQL 基准一致" if verdict == "QUALIFIED" else "测试评估：存在预设差异"),
            primary_issue_code=code,
            issue_codes=[code] if code else [],
            dimensions=dimensions,
            differences=differences,
            needs_human_review=False,
        )
        return artifact_from_evaluation(
            result, provider=self.provider, model=self.model, prompt_version=self.prompt_version,
            input_hash=evidence.input_hash,
        )
