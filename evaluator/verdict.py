"""Question-level judgment from the single LLM evaluation, plus manual override."""
from __future__ import annotations

from typing import Any, Dict, Optional

from evaluator.models import DimensionStatus, REQUIRED_DIMENSIONS

QUALIFIED = "QUALIFIED"
PARTIAL = "PARTIAL"
UNQUALIFIED = "UNQUALIFIED"
UNEVALUABLE = "UNEVALUABLE"
SCORED_VERDICTS = {QUALIFIED, PARTIAL, UNQUALIFIED}
OVERRIDE_VERDICTS = {QUALIFIED, PARTIAL, UNQUALIFIED, UNEVALUABLE}
DEFAULT_THRESHOLD = 1.0

ISSUE_LABELS = {
    "PERIOD_MISMATCH": "期间不一致",
    "SCOPE_MISMATCH": "范围不一致",
    "GRAIN_MISMATCH": "粒度不一致",
    "MISSING_FIELD": "漏指标",
    "MISSING_ROW": "漏行",
    "WRONG_VALUE": "错值",
    "UNIT_MISMATCH": "单位不一致",
    "CALIBER_MISMATCH": "口径不一致",
    "CONTRADICTORY_TEXT": "回答自相矛盾",
    "INSUFFICIENT_EVIDENCE": "证据不足",
    "LLM_CALL_FAILED": "评估调用失败",
    "LLM_RESPONSE_INVALID": "评估返回非法",
    "LLM_INPUT_TOO_LARGE": "证据超出上限",
}
NO_EVALUATION_CODE = "NO_LLM_EVALUATION"
NO_EVALUATION_LABEL = "尚无 LLM 评估"


def _ratio(numerator: int, denominator: int) -> Optional[float]:
    return round(numerator / denominator, 4) if denominator else None


def parse_threshold(value: Any) -> float:
    if value is None or value == "":
        return DEFAULT_THRESHOLD
    if isinstance(value, str):
        value = value.strip().replace("%", "")
    number = float(value)
    if 1 < number <= 100:
        number /= 100.0
    if not 0 <= number <= 1:
        raise ValueError("覆盖要求需在 0% 到 100% 之间")
    return round(number, 4)


def agent_produced_result(case: Dict[str, Any], detail: Optional[Dict[str, Any]] = None) -> bool:
    payload = detail or case
    text = str(((payload.get("agent_answer") or {}).get("text") or "")).strip()
    if text:
        return True
    completion = case.get("completion_status") or (payload.get("result") or {}).get("completion_status")
    return completion == "completed"


def _evaluation_data(evaluation: Any) -> Optional[Dict[str, Any]]:
    if evaluation is None:
        return None
    if isinstance(evaluation, dict):
        data = evaluation.get("evaluation") if isinstance(evaluation.get("evaluation"), dict) else evaluation
        return data if isinstance(data, dict) else None
    if hasattr(evaluation, "model_dump"):
        dumped = evaluation.model_dump(mode="json")
        return dumped.get("evaluation") if isinstance(dumped.get("evaluation"), dict) else dumped
    return None


def normalize_manual_verdict(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip().upper()
    if text in {"", "NULL", "NONE", "AUTO", "RESET"}:
        return None
    aliases = {
        QUALIFIED: QUALIFIED, "合格": QUALIFIED,
        PARTIAL: PARTIAL, "部分作答": PARTIAL,
        UNQUALIFIED: UNQUALIFIED, "不合格": UNQUALIFIED,
        UNEVALUABLE: UNEVALUABLE, "无法评估": UNEVALUABLE,
    }
    if text not in aliases:
        raise ValueError("平反结论必须是合格、部分作答、不合格或无法评估")
    return aliases[text]


EMPTY_JUDGMENT = {
    "auto_verdict": UNEVALUABLE,
    "primary_issue_code": None,
    "issue_codes": [],
    "confidence": None,
    "summary": "",
    "dimensions": {},
    "differences_count": 0,
    "has_evaluation": False,
    "auto_source": "none",
}


def case_judgment(evaluation: Any, manual_verdict: Any = None) -> Dict[str, Any]:
    """自动结论直接来自保存的 LLM 评估；人工平反优先。"""
    data = _evaluation_data(evaluation)
    manual = normalize_manual_verdict(manual_verdict)
    if not data:
        auto = UNEVALUABLE
        issue = None
        codes: list = []
        confidence = None
        summary = ""
        dimensions: Dict[str, Any] = {}
        differences_count = 0
        has_evaluation = False
    else:
        auto = str(data.get("overall_verdict") or UNEVALUABLE).upper()
        if auto not in OVERRIDE_VERDICTS:
            auto = UNEVALUABLE
        issue = data.get("primary_issue_code")
        codes = list(data.get("issue_codes") or [])
        confidence = data.get("confidence")
        summary = str(data.get("summary") or "")
        dimensions = data.get("dimensions") or {}
        differences_count = len(data.get("differences") or [])
        has_evaluation = True
    final = manual if manual in OVERRIDE_VERDICTS else auto
    if not has_evaluation and final == auto:
        reason = NO_EVALUATION_CODE
        reason_label = NO_EVALUATION_LABEL
    else:
        reason = issue
        reason_label = ISSUE_LABELS.get(str(issue or ""), "")
    return {
        "auto_verdict": auto,
        "manual_verdict": manual,
        "final_verdict": final,
        "evaluable": final in SCORED_VERDICTS,
        "qualified": final == QUALIFIED,
        "reason": reason,
        "reason_label": reason_label,
        "primary_issue_code": issue,
        "issue_codes": codes,
        "confidence": confidence,
        "summary": summary,
        "dimensions": dimensions,
        "differences_count": differences_count,
        "has_evaluation": has_evaluation,
        "auto_source": "llm" if has_evaluation else "none",
        "consistency": None,
        "threshold": None,
        "grain_mismatch": ((dimensions.get("grain") or {}).get("status") == DimensionStatus.MISMATCH.value),
        "coverage": None,
    }


def dimension_counts(judgments) -> Dict[str, Dict[str, Any]]:
    """七维一致率：按题计数，UNKNOWN 和 NA 不进入分母。"""
    metrics: Dict[str, Dict[str, Any]] = {}
    for key in REQUIRED_DIMENSIONS:
        metrics[key] = {"matched": 0, "partial": 0, "mismatched": 0, "evaluable": 0, "rate": None}
    for judgment in judgments:
        dimensions = judgment.get("dimensions") or {}
        for key in REQUIRED_DIMENSIONS:
            status = (dimensions.get(key) or {}).get("status")
            bucket = metrics[key]
            if status == DimensionStatus.MATCH.value:
                bucket["matched"] += 1
            elif status == DimensionStatus.PARTIAL.value:
                bucket["partial"] += 1
            elif status == DimensionStatus.MISMATCH.value:
                bucket["mismatched"] += 1
    for bucket in metrics.values():
        evaluable = bucket["matched"] + bucket["partial"] + bucket["mismatched"]
        bucket["evaluable"] = evaluable
        bucket["rate"] = _ratio(bucket["matched"], evaluable)
    return metrics


def headline_from_metrics(metrics: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "pass_rate": (metrics.get("final") or {}).get("pass_rate"),
        "accuracy": (metrics.get("numeric") or {}).get("accuracy"),
        "assessability": (metrics.get("assessability") or {}).get("rate"),
        "threshold": metrics.get("threshold"),
        "generated_questions": (metrics.get("final") or {}).get("generated_questions"),
        "qualified_questions": (metrics.get("numeric") or {}).get("qualified_questions"),
        "partial_questions": (metrics.get("numeric") or {}).get("partial_questions"),
        "evaluable_questions": (metrics.get("assessability") or {}).get("evaluable_questions"),
        "total_questions": metrics.get("total_questions"),
    }
