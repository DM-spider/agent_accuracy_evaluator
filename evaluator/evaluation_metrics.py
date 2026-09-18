"""Derived run metrics from the single LLM evaluation result.

不再导入旧规则比较；题目结论与七维诊断均来自持久化的 llm_evaluation.json。
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from evaluator.models import REQUIRED_DIMENSIONS, CaseContract
from evaluator.verdict import (
    PARTIAL,
    QUALIFIED,
    UNEVALUABLE,
    UNQUALIFIED,
    agent_produced_result,
    case_judgment,
    dimension_counts,
    headline_from_metrics,
)

DIMENSION_METRIC_LABELS = {
    "period": "期间一致率",
    "scope": "范围一致率",
    "grain": "粒度一致率",
    "field_coverage": "字段完整率",
    "row_coverage": "行覆盖一致率",
    "numeric_accuracy": "数值准确率",
    "unit_caliber": "单位口径一致率",
}


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
    for name in ("result.json", "agent_answer.json", "sql_snapshot.json", "llm_evaluation.json"):
        path = case_dir / name
        if path.exists():
            detail[name.removesuffix(".json")] = json.loads(path.read_text(encoding="utf-8"))
    return detail


def _planned_question_count(repo, run_id):
    """计划测评题数：开跑时一次性写入的 contracts_snapshot，不随完成数增长。"""
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
        produced = agent_produced_result(case, detail)
        generated += int(produced)
        manual = case.get("manual_verdict") or (detail.get("review") or {}).get("verdict")
        judgment = case_judgment(detail.get("llm_evaluation"), manual_verdict=manual)
        judgment["generated"] = produced
        judgment["scene_big"] = (contract_map.get(case["case_id"]).scene_big if contract_map.get(case["case_id"]) else "") or case.get("scene_big") or ""
        case_judgments[case["case_id"]] = judgment
        verdict_counts[judgment["final_verdict"]] = verdict_counts.get(judgment["final_verdict"], 0) + 1
        evaluable += int(judgment["evaluable"])
        qualified += int(judgment["qualified"])

    dimensions = dimension_counts(case_judgments.values())
    for key, bucket in dimensions.items():
        bucket["label"] = DIMENSION_METRIC_LABELS.get(key, key)

    primary_distribution = Counter()
    issue_distribution = Counter()
    for judgment in case_judgments.values():
        codes = list(dict.fromkeys(judgment.get("issue_codes") or []))
        for code in codes:
            issue_distribution[code] += 1
        if judgment["final_verdict"] in {PARTIAL, UNQUALIFIED}:
            code = judgment.get("primary_issue_code")
            if code:
                primary_distribution[code] += 1

    total = len(cases)
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
        "dimension_metrics": dimensions,
        "primary_issue_distribution": dict(primary_distribution),
        "issue_distribution": dict(issue_distribution),
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
