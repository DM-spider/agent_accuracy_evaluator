"""Question-level consistency, auto judgment, and manual override."""
from __future__ import annotations

from typing import Any, Dict, Optional

MATCH = {"MATCH", "MATCH_WITH_TOLERANCE", "VALUE_MATCH_REVIEW"}
WRONG = {"WRONG_VALUE", "VALUE_DIFF_REVIEW"}
MISSING = {"MISSING", "FIELD_UNRECOGNIZED"}
COMMENTARY = {"UNEXPECTED", "NO_BENCHMARK"}
UNPARSEABLE = {"UNPARSEABLE", "VALUE_UNPARSEABLE"}
CALIBER = {"CALIBER_MISMATCH", "CALIBER_REVIEW"}
GAP = MISSING | COMMENTARY

QUALIFIED = "QUALIFIED"
PARTIAL = "PARTIAL"
UNQUALIFIED = "UNQUALIFIED"
UNEVALUABLE = "UNEVALUABLE"
SCORED_VERDICTS = {QUALIFIED, PARTIAL, UNQUALIFIED}
OVERRIDE_VERDICTS = {QUALIFIED, PARTIAL, UNQUALIFIED, UNEVALUABLE}
DEFAULT_THRESHOLD = 1.0
REASON_LABELS = {
    "WRONG_VALUE": "存在错值",
    "MISSING": "同一粒度下漏行或漏列",
    "GRAIN_MISMATCH": "SQL 为明细，回答只给出汇总，粒度不一致",
    "EXTRACT_FAIL": "抽不出可与 SQL 对齐的数值",
    "CALIBER_MISMATCH": "期间、组织或聚合口径不一致",
}


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


def item_status(item: Any) -> str:
    status = item.get("status") if isinstance(item, dict) else getattr(item, "status", "")
    return str(getattr(status, "value", status) or "")


def _item_coords(item: Any) -> tuple:
    raw = item.get("coordinates") if isinstance(item, dict) else getattr(item, "coordinates", None)
    raw = raw or {}
    return tuple(sorted((str(k), str(v)) for k, v in raw.items() if str(v).strip()))


def consistency_from_items(items: Any) -> Optional[float]:
    matched = wrong = missing = 0
    for item in items or []:
        status = item_status(item)
        if status in MATCH:
            matched += 1
        elif status in WRONG:
            wrong += 1
        elif status in MISSING:
            missing += 1
    return _ratio(matched, matched + wrong + missing)


def classify_comparison(items: Any, contract: Any = None) -> Dict[str, Any]:
    matched = wrong = missing = commentary = unparseable = caliber = 0
    sql_rows = set()
    for item in items or []:
        status = item_status(item)
        coords = _item_coords(item)
        if status in MATCH:
            matched += 1
            if coords:
                sql_rows.add(coords)
        elif status in WRONG:
            wrong += 1
            if coords:
                sql_rows.add(coords)
        elif status in MISSING:
            missing += 1
            if coords:
                sql_rows.add(coords)
        elif status in CALIBER:
            caliber += 1
            if coords:
                sql_rows.add(coords)
        elif status in COMMENTARY:
            commentary += 1
        elif status in UNPARSEABLE:
            unparseable += 1

    row_key = list(getattr(contract, "row_key", None) or []) if contract is not None else []
    result_type = str(getattr(contract, "result_type", "") or "")
    is_detail = bool(row_key) or result_type == "detail" or len(sql_rows) >= 2
    grain_mismatch = is_detail and len(sql_rows) >= 2 and matched == 0 and wrong == 0 and commentary > 0 and missing > 0
    policy = getattr(contract, "coverage_policy", None) if contract is not None else None
    minimum = float(getattr(policy, "minimum", 1.0) or 1.0)
    required = matched + missing + caliber
    coverage = _ratio(matched, required)

    if matched + wrong + missing + caliber == 0:
        verdict, reason = UNEVALUABLE, "EXTRACT_FAIL"
    elif grain_mismatch:
        verdict, reason = UNQUALIFIED, "GRAIN_MISMATCH"
    elif wrong:
        verdict, reason = UNQUALIFIED, "WRONG_VALUE"
    elif caliber and not matched:
        verdict, reason = UNQUALIFIED, "CALIBER_MISMATCH"
    elif caliber:
        verdict, reason = PARTIAL, "CALIBER_MISMATCH"
    elif matched == 0 and missing > 0:
        verdict, reason = (UNQUALIFIED, "GRAIN_MISMATCH") if commentary else (UNEVALUABLE, "EXTRACT_FAIL")
    elif missing and coverage is not None and coverage + 1e-12 >= minimum:
        verdict, reason = QUALIFIED, None
    elif missing:
        verdict, reason = PARTIAL, "MISSING"
    elif matched:
        verdict, reason = QUALIFIED, None
    else:
        verdict, reason = UNEVALUABLE, "EXTRACT_FAIL"

    return {
        "verdict": verdict,
        "reason": reason,
        "matched": matched,
        "wrong": wrong,
        "missing": missing,
        "commentary": commentary,
        "unparseable": unparseable,
        "caliber": caliber,
        "grain_mismatch": grain_mismatch,
        "coverage": coverage,
    }


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


def case_judgment(items: Any, threshold: Any = None, manual_verdict: Any = None, contract: Any = None) -> Dict[str, Any]:
    classified = classify_comparison(items, contract)
    auto = classified["verdict"]
    manual = normalize_manual_verdict(manual_verdict)
    final = manual if manual in OVERRIDE_VERDICTS else auto
    return {
        "consistency": consistency_from_items(items),
        "threshold": None,
        "auto_verdict": auto,
        "manual_verdict": manual,
        "final_verdict": final,
        "evaluable": final in SCORED_VERDICTS,
        "qualified": final == QUALIFIED,
        "reason": classified["reason"],
        "reason_label": REASON_LABELS.get(classified["reason"] or ""),
        "grain_mismatch": classified["grain_mismatch"],
        "coverage": classified["coverage"],
    }


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
