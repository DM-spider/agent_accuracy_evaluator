# -*- coding: utf-8 -*-
"""只重跑判分，不重新问智能体、不重查 SQL。

改的是判分规则/比对逻辑时用它：拿已保存的回答与 SQL 快照重新出结论。
默认只预览，加 --apply 才写回（旧版本会自动归档到 llm_evaluations/）。
"""
from __future__ import annotations

import argparse
import io
import json
import sys
from typing import Any, Dict, List, Optional

from evaluator.api import bootstrap
from evaluator.llm_evidence import build_evaluation_evidence
from evaluator.llm_evaluation import apply_reconciliation, validate_numeric_differences
from evaluator.models import CaseContract, CaseResult, CaseStatus, SqlSnapshot
from evaluator.reconcile import reconcile
from evaluator.scorer import result_from_llm_evaluation, score_run

PRESERVED = (
    "agent_latency_ms", "sql_latency_ms", "retries", "turn_index", "completion_status", "agent_timings",
)


def _load_contracts(repo, run_id: str, fallback: List[CaseContract]) -> Dict[str, CaseContract]:
    path = repo.run_dir(run_id) / "contracts_snapshot.json"
    if path.exists():
        payload = json.loads(path.read_text(encoding="utf-8"))
        return {item["case_id"]: CaseContract.model_validate(item) for item in payload}
    return {item.case_id: item for item in fallback}


def _rejudge_one(repo, contract: CaseContract, run_id: str, case_id: str, llm_evaluator):
    detail = repo.load_case_detail(run_id, case_id)
    if not detail:
        return None
    stored = detail.get("result") or {}
    if stored.get("status") == CaseStatus.NOT_SCORED.value:
        return None
    if detail.get("review") or detail.get("manual_verdict"):
        return None  # 人工平反过的题不再自动覆盖
    answer = (detail.get("agent_answer") or {}).get("text") or ""
    if not answer.strip():
        return None
    snapshot = SqlSnapshot.model_validate(detail.get("sql_snapshot") or {})
    alignment = stored.get("alignment") or {}
    params = alignment.get("sql_params") or snapshot.params or {}
    report = reconcile(contract, answer, snapshot.rows, params)
    evidence = build_evaluation_evidence(
        contract,
        answer_text=answer,
        sql_snapshot=snapshot,
        alignment=alignment,
        reconciliation=report,
        max_input_chars=int(getattr(llm_evaluator, "max_input_chars", 500000) or 500000),
    )
    artifact = llm_evaluator.evaluate(evidence, contract)
    artifact = validate_numeric_differences(contract, artifact)
    artifact = apply_reconciliation(contract, artifact, report)
    return artifact, result_from_llm_evaluation(contract, artifact), report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", required=True, help="运行批次 ID")
    parser.add_argument("--cases", default="", help="逗号分隔的题号；默认全部")
    parser.add_argument("--apply", action="store_true", help="写回结果（默认只预览）")
    args = parser.parse_args()

    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

    st = bootstrap()
    repo = st["repo"]
    run = repo.get_run(args.run)
    if not run:
        print(f"找不到运行批次 {args.run}")
        return 2
    contracts = _load_contracts(repo, args.run, st["contracts"])
    from evaluator.llm_client import LlmEvaluator

    llm_evaluator = LlmEvaluator((st["settings"] or {}).get("evaluator_llm") or {})
    if not llm_evaluator.configured:
        print("评估 LLM 未配置，无法重新判分")
        return 2

    case_ids = [cid.strip() for cid in args.cases.split(",") if cid.strip()] or [
        item["case_id"] for item in repo.list_cases(args.run)
    ]
    results: List[CaseResult] = []
    for case_id in case_ids:
        contract = contracts.get(case_id)
        if contract is None:
            continue
        outcome = _rejudge_one(repo, contract, args.run, case_id, llm_evaluator)
        before = (repo.load_case_detail(args.run, case_id) or {}).get("result") or {}
        if outcome is None:
            print(f"{case_id}: 跳过（未测上/人工平反/无回答）")
            if before.get("status"):
                results.append(CaseResult.model_validate(before))
            continue
        artifact, result, report = outcome
        old = CaseResult.model_validate(before)
        for field in PRESERVED:
            setattr(result, field, getattr(old, field))
        result.alignment = {**(before.get("alignment") or {}), "reconciliation": {
            key: report.get(key) for key in ("available", "sql_rows", "matched_rows", "checked_values",
                                             "hit_values", "row_hit_rate", "value_hit_rate")
        }}
        results.append(result)
        verdict = artifact.evaluation.overall_verdict.value
        print(
            f"{case_id}: {before.get('status', '?')} → {result.status.value}"
            f"  ({verdict}, 行 {report.get('matched_rows')}/{report.get('sql_rows')}"
            f", 值 {report.get('hit_values')}/{report.get('checked_values')})"
        )
        if args.apply:
            repo.save_llm_evaluation(args.run, case_id, artifact)
            repo.save_case(args.run, result)

    if args.apply and results:
        summary = score_run(
            args.run,
            results,
            agent_name=run.get("agent_name") or "",
            anchor_time=run.get("anchor_time") or "",
            timezone_name=(st["settings"].get("app") or {}).get("timezone") or "Asia/Shanghai",
            status=run.get("status"),
            consistency_threshold=run.get("consistency_threshold") or 0.6,
        )
        repo.update_run(summary)
        from evaluator.evaluation_metrics import run_evaluation_metrics
        from evaluator.verdict import headline_from_metrics

        repo.save_headline_metrics(
            args.run, headline_from_metrics(run_evaluation_metrics(repo, args.run, st["contracts"]))
        )
        print("已写回结果与批次汇总")
    elif results:
        print("预览模式，未写回；确认无误后加 --apply")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
