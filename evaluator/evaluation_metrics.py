"""Derived run metrics for generation, accuracy, and assessability."""
from __future__ import annotations

import json
from pathlib import Path

from evaluator.models import CaseContract
from evaluator.table_comparison import table_comparison
from evaluator.verdict import (
    PARTIAL,
    QUALIFIED,
    UNEVALUABLE,
    UNQUALIFIED,
    agent_produced_result,
    case_judgment,
    headline_from_metrics,
)


def _ratio(numerator, denominator):
    return round(numerator / denominator, 4) if denominator else None


def _contracts_for_run(repo, run_id, contracts):
    path = Path(repo.runs_dir) / run_id / "contracts_snapshot.json"
    if path.exists():
        payload = json.loads(path.read_text(encoding="utf-8"))
        return {item["case_id"]: CaseContract.model_validate(item) for item in payload}
    return {contract.case_id: contract for contract in contracts}


def _case_detail(repo, run_id, case):
    detail = dict(case)
    case_dir = Path(repo.runs_dir) / run_id / "cases" / case["case_id"]
    recheck_dir = case_dir / "sql_rechecks"
    detail["sql_rechecks"] = [json.loads(path.read_text(encoding="utf-8")) for path in sorted(recheck_dir.glob("*.json"))]
    for name in ("result.json", "agent_answer.json", "sql_snapshot.json", "agent_claims.json", "sql_claims.json"):
        path = case_dir / name
        if path.exists():
            detail[name.removesuffix(".json")] = json.loads(path.read_text(encoding="utf-8"))
    return detail


def _planned_question_count(repo, run_id):
    """计划测评题数：开跑时一次性写入的 contracts_snapshot，不随完成数增长。

    list_cases 只包含已落库（已完成）的题；live 模式下已完成题必有返回文本，
    若用它的数量当分母，通过率的分子会恒等于分母、全程显示 100%。
    """
    path = Path(repo.runs_dir) / run_id / "contracts_snapshot.json"
    if not path.exists():
        return None
    try:
        return len(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return None


def run_evaluation_metrics(repo, run_id, contracts):
    cases = repo.list_cases(run_id)
    contract_map = _contracts_for_run(repo, run_id, contracts)
    generated = qualified = evaluable = 0
    verdict_counts = {QUALIFIED: 0, PARTIAL: 0, UNQUALIFIED: 0, UNEVALUABLE: 0}
    case_judgments = {}

    for case in cases:
        detail = _case_detail(repo, run_id, case)
        contract = contract_map.get(case["case_id"])
        produced = agent_produced_result(case, detail)
        generated += int(produced)
        items = []
        if contract and contract.numeric_evaluable:
            items = table_comparison(contract, detail).get("items") or []
        manual = case.get("manual_verdict") or (detail.get("review") or {}).get("verdict")
        judgment = case_judgment(items, manual_verdict=manual, contract=contract)
        judgment["generated"] = produced
        case_judgments[case["case_id"]] = judgment
        verdict_counts[judgment["final_verdict"]] = verdict_counts.get(judgment["final_verdict"], 0) + 1
        evaluable += int(judgment["evaluable"])
        qualified += int(judgment["qualified"])

    total = len(cases)
    # 分母取开跑即固定的计划题数；快照缺失（旧数据）时回退已完成题数
    planned = _planned_question_count(repo, run_id)
    denominator = planned if planned else total
    metrics = {
        "total_questions": denominator,
        "planned_questions": denominator,
        "threshold": None,
        "final": {
            "generated_questions": generated,
            "pass_rate": _ratio(generated, denominator),
            "statuses": {
                QUALIFIED: verdict_counts[QUALIFIED],
                PARTIAL: verdict_counts[PARTIAL],
                UNQUALIFIED: verdict_counts[UNQUALIFIED],
                UNEVALUABLE: verdict_counts[UNEVALUABLE],
            },
        },
        "numeric": {
            "qualified_questions": qualified,
            "partial_questions": verdict_counts[PARTIAL],
            "evaluable_questions": evaluable,
            "accuracy": _ratio(qualified, evaluable),
        },
        "assessability": {
            "evaluable_questions": evaluable,
            "rate": _ratio(evaluable, total),
            "unevaluable_questions": verdict_counts[UNEVALUABLE],
        },
        "case_judgments": case_judgments,
    }
    metrics["category_pass_rate"] = _category_pass_rate(cases, case_judgments)
    metrics["headline"] = headline_from_metrics(metrics)
    return metrics


V3_CATEGORIES = {
    "CX": "产销差专题",
    "LS": "漏损率专题",
    "DMA": "小区DMA专题",
    "JL": "探漏与维抢修专题",
    "BJ": "异常报警与设施专题",
}


def _category_key(case_id):
    prefix = str(case_id or "").split("-")[0]
    return prefix if prefix in V3_CATEGORIES else "other"


def _category_pass_rate(cases, case_judgments):
    buckets = {}
    for case in cases:
        key = _category_key(case.get("case_id"))
        bucket = buckets.setdefault(key, {"total": 0, "qualified": 0, "label": V3_CATEGORIES.get(key, "其他")})
        bucket["total"] += 1
        if (case_judgments.get(case["case_id"]) or {}).get("qualified"):
            bucket["qualified"] += 1
    for bucket in buckets.values():
        bucket["pass_rate"] = _ratio(bucket["qualified"], bucket["total"])
    return buckets
