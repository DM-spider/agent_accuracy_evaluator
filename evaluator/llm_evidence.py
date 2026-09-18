# -*- coding: utf-8 -*-
"""构建发送给评估 LLM 的轻量证据包。

只发送评估必要字段：契约、期间、智能体原文、SQL 基准行和别名子集。
不发送数据库连接信息、执行 SQL 文本、请求头或任何凭证。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from evaluator.aliases import metric_aliases, organization_aliases, resolved_dimension_columns
from evaluator.models import CaseContract, SqlSnapshot

DEFAULT_MAX_INPUT_CHARS = 500_000


@dataclass
class EvaluationEvidence:
    payload: Dict[str, Any]
    input_hash: str
    input_chars: int
    too_large: bool


def stable_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def evidence_input_hash(payload: Dict[str, Any]) -> str:
    return hashlib.sha256(stable_json(payload).encode("utf-8")).hexdigest()


def _measure_payload(contract: CaseContract) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for key, spec in contract.measures.items():
        out[key] = {
            "label": spec.label,
            "unit": spec.unit,
            "value_scale": spec.value_scale,
            "required": spec.required,
            "aggregation": spec.aggregation,
            "period_role": spec.period_role,
            "sql_column": spec.sql_column,
            "aliases": list(spec.aliases),
            "tolerance": spec.tolerance.model_dump(mode="json"),
        }
    return out


def _project_sql(contract: CaseContract, snapshot: SqlSnapshot) -> Dict[str, Any]:
    rows = [dict(row) for row in (snapshot.rows or [])]
    columns = list(snapshot.columns or (list(rows[0].keys()) if rows else []))
    wanted: List[str] = []
    aliases_by_dim = resolved_dimension_columns(contract)
    for dim, aliases in aliases_by_dim.items():
        for column in columns:
            if column in aliases and column not in wanted:
                wanted.append(column)
    for key, spec in contract.measures.items():
        for column in columns:
            if column in {spec.sql_column, key} or column in set(spec.aliases):
                if column not in wanted:
                    wanted.append(column)
    if not wanted:
        # 无法确定契约列时保留原列，绝不丢行。
        wanted = columns
    projected = [{column: row.get(column) for column in wanted} for row in rows]
    return {
        "columns": wanted,
        "rows": projected,
        "row_count": snapshot.row_count if snapshot.row_count else len(rows),
        "truncated": bool(snapshot.truncated),
        "source": snapshot.source,
    }


def _relevant_aliases(contract: CaseContract, text: str) -> Dict[str, Any]:
    haystack = text or ""
    organizations = {
        alias: canonical for alias, canonical in organization_aliases().items() if alias and alias in haystack
    }
    metrics = {
        alias: canonical
        for alias, canonical in metric_aliases().items()
        if canonical in contract.measures
    }
    for key, spec in contract.measures.items():
        for name in [key, spec.label, *spec.aliases]:
            if name:
                metrics.setdefault(name, key)
    return {"organizations": organizations, "metrics": metrics}


def build_evaluation_evidence(
    contract: CaseContract,
    *,
    answer_text: str = "",
    sql_snapshot: Optional[SqlSnapshot] = None,
    alignment: Optional[Dict[str, Any]] = None,
    max_input_chars: int = DEFAULT_MAX_INPUT_CHARS,
) -> EvaluationEvidence:
    alignment = alignment or {}
    snapshot = sql_snapshot or SqlSnapshot()
    payload: Dict[str, Any] = {
        "case_id": contract.case_id,
        "question": contract.question,
        "result_type": contract.result_type,
        "contract": {
            "row_key": list(contract.row_key),
            "dimensions": list(contract.dimensions),
            "measures": _measure_payload(contract),
            "coverage_policy": contract.coverage_policy.model_dump(mode="json"),
            "caliber": contract.caliber,
            "parameter_resolver": contract.parameter_resolver,
        },
        "requested_params": alignment.get("requested_params") or {},
        "reported_periods": alignment.get("reported_periods") or [],
        "sql_params": alignment.get("sql_params") or dict(snapshot.params or {}),
        "alignment_notes": list(alignment.get("issues") or []),
        "agent_answer": answer_text or "",
        "sql_result": _project_sql(contract, snapshot),
        "aliases": _relevant_aliases(contract, f"{contract.question}\n{answer_text}"),
    }
    encoded = stable_json(payload)
    return EvaluationEvidence(
        payload=payload,
        input_hash=hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
        input_chars=len(encoded),
        too_large=bool(max_input_chars) and len(encoded) > int(max_input_chars),
    )
