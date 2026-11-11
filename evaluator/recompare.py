# -*- coding: utf-8 -*-
"""离线回放：用冻结的回答与 SQL 重算结论，不调用智能体、数据库或 LLM。

用途是“改判规则前后的回归闸门”：
- 默认按当前线上策略（v1）重放，验证回放器能复现存档结论；
- `--check` 用 annotations/annotations.csv 的人工结论逐题断言；
- `--baseline` 与上一版报告对比，列出预期之外的变化。

历史批次只读，不覆盖任何 runtime 文件；报告写到 --out 指定目录。
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from evaluator.annotations import latest_by_case, load_annotations
from evaluator.llm_evaluation import apply_reconciliation
from evaluator.models import CaseContract, LlmEvaluationArtifact, SqlSnapshot
from evaluator.paths import TOOL_ROOT
from evaluator.reconcile import reconcile, verdict_from_report

SUPPORTED_POLICIES = ("v1",)
DEFAULT_RUNTIME = TOOL_ROOT / "runtime"
DEFAULT_ANNOTATIONS = TOOL_ROOT / "annotations" / "annotations.csv"

REPORT_COLUMNS: Tuple[str, ...] = (
    "run_id",
    "case_id",
    "stored_verdict",
    "replay_verdict",
    "match",
    "available",
    "coverage",
    "value_recall",
    "hit",
    "wrong",
    "absent",
    "sql_rows",
    "issue_codes",
    "manual_verdict",
    "reason_code",
    "annotation_note",
    "note",
)


def _now() -> str:
    return datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds")


def _stamp() -> str:
    return datetime.now(timezone(timedelta(hours=8))).strftime("%Y%m%d_%H%M%S")


def _read_json(path: Path) -> Optional[Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _load_contracts(path: Path) -> Dict[str, CaseContract]:
    payload = _read_json(path)
    if not isinstance(payload, list):
        return {}
    out: Dict[str, CaseContract] = {}
    for item in payload:
        try:
            contract = CaseContract.model_validate(item)
        except Exception:
            continue
        out[contract.case_id] = contract
    return out


def _load_artifact(path: Path) -> Optional[LlmEvaluationArtifact]:
    payload = _read_json(path)
    if not isinstance(payload, dict):
        return None
    try:
        return LlmEvaluationArtifact.model_validate(payload)
    except Exception:
        return None


def _load_snapshot(path: Path) -> Optional[SqlSnapshot]:
    payload = _read_json(path)
    if not isinstance(payload, dict):
        return None
    try:
        return SqlSnapshot.model_validate(payload)
    except Exception:
        return None


def replay_case(
    contract: CaseContract,
    answer_text: str,
    snapshot: Optional[SqlSnapshot],
    artifact: Optional[LlmEvaluationArtifact],
    *,
    policy: str = "v1",
) -> Dict[str, Any]:
    """重放单题，返回结论与比对明细。"""
    if policy not in SUPPORTED_POLICIES:
        raise ValueError(f"策略 {policy} 尚未实现，当前只支持 {', '.join(SUPPORTED_POLICIES)}")
    rows = list(snapshot.rows or []) if snapshot else []
    params = dict(snapshot.params or {}) if snapshot else {}
    report = reconcile(contract, answer_text, rows, params)
    if artifact is not None:
        updated = apply_reconciliation(contract, artifact, report, answer_text=answer_text)
        verdict = updated.evaluation.overall_verdict.value
        issue_codes = list(updated.evaluation.issue_codes)
        source = "artifact+reconcile"
    else:
        verdict = verdict_from_report(report)
        issue_codes = []
        source = "reconcile-only"
    counts = report.get("counts") or {}
    return {
        "verdict": verdict,
        "issue_codes": issue_codes,
        "source": source,
        "available": bool(report.get("available")),
        "coverage": report.get("coverage"),
        "value_recall": report.get("value_recall"),
        "hit": counts.get("hit"),
        "wrong": counts.get("wrong"),
        "absent": counts.get("absent"),
        "sql_rows": report.get("sql_rows"),
        "reconcile_reason": report.get("reason") or "",
    }


def replay_runtime(
    runtime: Path,
    *,
    policy: str = "v1",
    run_id: Optional[str] = None,
    annotations: Optional[Sequence[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """回放整个 runtime（可按批次过滤），返回逐题结果与整体统计。"""
    base = Path(runtime) / "runs"
    if run_id:
        run_dirs = [base / run_id] if (base / run_id).is_dir() else []
    else:
        run_dirs = sorted(path for path in base.iterdir() if path.is_dir()) if base.exists() else []

    latest = latest_by_case(annotations or [])
    rows: List[Dict[str, Any]] = []
    skipped: List[Dict[str, str]] = []
    for run_dir in run_dirs:
        contracts = _load_contracts(run_dir / "contracts_snapshot.json")
        if not contracts:
            skipped.append({"run_id": run_dir.name, "reason": "缺少 contracts_snapshot.json"})
            continue
        cases_dir = run_dir / "cases"
        if not cases_dir.exists():
            skipped.append({"run_id": run_dir.name, "reason": "缺少 cases 目录"})
            continue
        for case_dir in sorted(path for path in cases_dir.iterdir() if path.is_dir()):
            case_id = case_dir.name
            contract = contracts.get(case_id)
            if contract is None:
                skipped.append({"run_id": run_dir.name, "case_id": case_id, "reason": "契约快照里没有这道题"})
                continue
            answer_payload = _read_json(case_dir / "agent_answer.json")
            if not isinstance(answer_payload, dict):
                skipped.append({"run_id": run_dir.name, "case_id": case_id, "reason": "缺少 agent_answer.json"})
                continue
            answer_text = str(answer_payload.get("text") or "")
            snapshot = _load_snapshot(case_dir / "sql_snapshot.json")
            artifact = _load_artifact(case_dir / "llm_evaluation.json")
            stored = artifact.evaluation.overall_verdict.value if artifact else None
            try:
                replay = replay_case(contract, answer_text, snapshot, artifact, policy=policy)
            except Exception as exc:  # 单题失败不中断整批
                skipped.append(
                    {"run_id": run_dir.name, "case_id": case_id, "reason": f"回放失败：{type(exc).__name__}"}
                )
                continue
            note = latest.get((run_dir.name, case_id)) or {}
            row = {
                "run_id": run_dir.name,
                "case_id": case_id,
                "stored_verdict": stored or "",
                "replay_verdict": replay["verdict"] or "",
                "match": (None if not stored or not replay["verdict"] else stored == replay["verdict"]),
                "available": replay["available"],
                "coverage": replay["coverage"],
                "value_recall": replay["value_recall"],
                "hit": replay["hit"],
                "wrong": replay["wrong"],
                "absent": replay["absent"],
                "sql_rows": replay["sql_rows"],
                "issue_codes": replay["issue_codes"],
                "manual_verdict": note.get("manual_verdict") or "",
                "reason_code": note.get("reason_code") or "",
                "annotation_note": note.get("note") or "",
                "note": replay["reconcile_reason"] if not replay["available"] else "",
            }
            rows.append(row)

    stored_rows = [row for row in rows if row["stored_verdict"]]
    matched = [row for row in stored_rows if row["match"] is True]
    return {
        "generated_at": _now(),
        "policy": policy,
        "runtime": str(Path(runtime)),
        "summary": {
            "runs": len(run_dirs),
            "cases": len(rows),
            "with_stored": len(stored_rows),
            "matched": len(matched),
            "mismatched": len(stored_rows) - len(matched),
            "annotated": sum(1 for row in rows if row["manual_verdict"]),
            "skipped": len(skipped),
        },
        "rows": rows,
        "skipped": skipped,
    }


def check_annotations(rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """用人工结论断言回放结果。没有标注时视为通过（无可校验项）。"""
    checked = 0
    failures: List[Dict[str, Any]] = []
    seen: set = set()
    for row in rows:
        manual = row.get("manual_verdict") or ""
        if not manual:
            continue
        checked += 1
        seen.add((row["run_id"], row["case_id"]))
        replay = row.get("replay_verdict") or ""
        if replay != manual:
            failures.append(
                {
                    "run_id": row["run_id"],
                    "case_id": row["case_id"],
                    "manual_verdict": manual,
                    "replay_verdict": replay or "无法回放",
                    "reason_code": row.get("reason_code") or "",
                    "note": row.get("annotation_note") or "",
                }
            )
    return {"checked": checked, "failures": failures, "passed": checked - len(failures)}


def compare_baseline(rows: Sequence[Dict[str, Any]], baseline: Dict[str, Any]) -> List[Dict[str, Any]]:
    """与上一版报告对比，列出重放结论发生变化、但没有人工预期的题。"""
    previous = {
        (row.get("run_id"), row.get("case_id")): row.get("replay_verdict")
        for row in baseline.get("rows") or []
    }
    changes: List[Dict[str, Any]] = []
    for row in rows:
        key = (row["run_id"], row["case_id"])
        if key not in previous:
            continue
        before = previous[key]
        if before and row.get("replay_verdict") and before != row["replay_verdict"]:
            changes.append(
                {
                    "run_id": row["run_id"],
                    "case_id": row["case_id"],
                    "before": before,
                    "after": row["replay_verdict"],
                    "annotated": bool(row.get("manual_verdict")),
                }
            )
    return changes


def _write_csv(path: Path, rows: Sequence[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=REPORT_COLUMNS, lineterminator="\r\n")
        writer.writeheader()
        for row in rows:
            record = dict(row)
            record["issue_codes"] = "|".join(record.get("issue_codes") or [])
            record["available"] = "1" if record.get("available") else "0"
            writer.writerow({key: record.get(key, "") for key in REPORT_COLUMNS})


def _write_markdown(path: Path, payload: Dict[str, Any]) -> None:
    summary = payload["summary"]
    lines = [
        "# 离线回放报告",
        "",
        f"- 生成时间：{payload['generated_at']}",
        f"- 策略：{payload['policy']}",
        f"- 批次：{summary['runs']}　题目：{summary['cases']}　有存档结论：{summary['with_stored']}",
        f"- 与存档一致：{summary['matched']} / {summary['with_stored']}",
        f"- 已标注：{summary['annotated']}　跳过：{summary['skipped']}",
    ]
    check = payload.get("check")
    if check:
        lines.append("")
        if check["checked"] == 0:
            lines.append("- 金标校验：没有可校验的标注")
        else:
            status = "通过" if not check["failures"] else f"未通过（{len(check['failures'])} 题）"
            lines.append(f"- 金标校验：{status}，共 {check['checked']} 题")
    mismatched = [row for row in payload["rows"] if row["match"] is False]
    if mismatched:
        lines += ["", "## 与存档不一致", "", "| 批次 | 题号 | 存档 | 重放 | 覆盖率 |", "|---|---|---|---|---|"]
        for row in mismatched:
            lines.append(
                f"| {row['run_id']} | {row['case_id']} | {row['stored_verdict']} | {row['replay_verdict']} | {row['coverage']} |"
            )
    if check and check["failures"]:
        lines += ["", "## 金标未通过", "", "| 批次 | 题号 | 人工结论 | 重放结果 | 归因 |", "|---|---|---|---|---|"]
        for row in check["failures"]:
            lines.append(
                f"| {row['run_id']} | {row['case_id']} | {row['manual_verdict']} | {row['replay_verdict']} | {row['reason_code']} |"
            )
    changes = payload.get("changes") or []
    if changes:
        lines += ["", "## 与上一版相比的变化", "", "| 批次 | 题号 | 上一版 | 本版 | 已标注 |", "|---|---|---|---|---|"]
        for row in changes:
            lines.append(
                f"| {row['run_id']} | {row['case_id']} | {row['before']} | {row['after']} | {'是' if row['annotated'] else '否'} |"
            )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="离线回放历史批次，校验判定规则")
    parser.add_argument("--runtime", default=str(DEFAULT_RUNTIME), help="runtime 目录")
    parser.add_argument("--run-id", default=None, help="只回放某一批")
    parser.add_argument("--policy", default="v1", help="判定策略，当前只支持 v1")
    parser.add_argument("--annotations", default=str(DEFAULT_ANNOTATIONS), help="人工标注 CSV")
    parser.add_argument("--out", default=None, help="报告输出目录，默认 runtime/recompare/<策略_时间>")
    parser.add_argument("--check", action="store_true", help="用人工结论断言，失败返回退出码 1")
    parser.add_argument("--baseline", default=None, help="上一版 report.json，用于列出变化")
    args = parser.parse_args(argv)

    if args.policy not in SUPPORTED_POLICIES:
        print(f"策略 {args.policy} 尚未实现，当前只支持 {', '.join(SUPPORTED_POLICIES)}")
        return 2

    annotations = load_annotations(args.annotations) if Path(args.annotations).exists() else []
    payload = replay_runtime(
        Path(args.runtime), policy=args.policy, run_id=args.run_id, annotations=annotations
    )
    summary = payload["summary"]

    check = None
    if args.check:
        check = check_annotations(payload["rows"])
        payload["check"] = check

    changes: List[Dict[str, Any]] = []
    if args.baseline:
        baseline = _read_json(Path(args.baseline))
        if isinstance(baseline, dict):
            changes = compare_baseline(payload["rows"], baseline)
    payload["changes"] = changes

    out_dir = Path(args.out) if args.out else Path(args.runtime) / "recompare" / f"{args.policy}_{_stamp()}"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    _write_csv(out_dir / "cases.csv", payload["rows"])
    _write_markdown(out_dir / "report.md", payload)

    print(
        f"批次 {summary['runs']}　题目 {summary['cases']}　"
        f"与存档一致 {summary['matched']}/{summary['with_stored']}　已标注 {summary['annotated']}"
    )
    if check is not None:
        if check["checked"] == 0:
            print("金标校验：没有可校验的标注")
        else:
            print(f"金标校验：{check['passed']}/{check['checked']} 通过")
            for failure in check["failures"]:
                print(
                    f"  未通过 {failure['run_id']} {failure['case_id']}："
                    f"人工={failure['manual_verdict']} 重放={failure['replay_verdict']}"
                )
    if changes:
        print(f"与上一版相比 {len(changes)} 题变化")
    print(f"报告目录：{out_dir}")
    return 1 if check and check["failures"] else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
