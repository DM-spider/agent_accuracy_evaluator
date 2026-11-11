# -*- coding: utf-8 -*-
"""按当前规则重算已存档批次的过程记录。

行数统计、过程评测、过程回顾这几处改过之后，旧存档里的步骤状态与结论
还是旧口径。本脚本从 trace.jsonl 重建步骤状态与统计，覆盖 process.json
（旧文件先归档到 runtime/backups），并按需补生成「过程回顾」。

不重新问智能体、不重查 SQL、不改判分。默认只预览，加 --apply 才写回。
"""
from __future__ import annotations

import argparse
import io
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from evaluator.api import bootstrap
from evaluator.paths import runtime_dir, runs_dir
from evaluator.process_narrative import narrate, needs_narrative
from evaluator.process_review import review_from_process
from evaluator.process_trace import build_process_from_entries
from evaluator.repository import process_for_disk


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _load_entries(path: Path) -> Optional[List[Dict[str, Any]]]:
    """trace.jsonl 是原始条目的逐行 JSON；没有就返回 None。"""
    if not path.exists():
        return None
    entries: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except ValueError:
            continue
    return entries


def rebuild_payload(
    process: Dict[str, Any],
    entries: List[Dict[str, Any]],
    *,
    case_id: str,
    result: Dict[str, Any],
    answer_text: str = "",
    narrative: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """用存档里的原始条目重建一份过程记录。"""
    summary = process.get("summary") or {}
    timings = dict(result.get("agent_timings") or {})
    if not timings.get("transport_total_ms"):
        timings["transport_total_ms"] = summary.get("total_ms")
    payload = build_process_from_entries(
        entries,
        case_id=case_id,
        timings=timings,
        completion_status=str(result.get("completion_status") or summary.get("completion_status") or ""),
        error=str(summary.get("error") or ""),
        answer_text=answer_text,
        truncated=bool(process.get("truncated")),
    )
    # 与详情接口读时重算保持同一口径：旧批次读超时且没收到响应头的，补判成平台无响应。
    payload["review"] = review_from_process(payload, answer_text, timings)
    if narrative:
        payload["narrative"] = narrative
    return payload


def _stats(process: Dict[str, Any]) -> str:
    review = process.get("review") or {}
    counts = process.get("counts") or {}
    stats = review.get("stats") or {}
    return (
        f"{review.get('verdict', '?')}"
        f"（空 {counts.get('empty', 0)}/{counts.get('tool_calls', 0)}，"
        f"失败 {counts.get('error', 0)}，超时 {counts.get('timeout', 0)}）"
    )


def _backup(case_dir: Path, run_id: str, case_id: str, stamp: str) -> None:
    target = runtime_dir() / "backups" / f"{stamp}_process_backfill" / run_id / case_id
    target.mkdir(parents=True, exist_ok=True)
    (target / "process.json").write_bytes((case_dir / "process.json").read_bytes())


def _case_ids(repo, run_id: str) -> List[str]:
    case_root = repo.run_dir(run_id) / "cases"
    if not case_root.exists():
        return []
    return sorted(path.name for path in case_root.iterdir() if path.is_dir())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", default="", help="运行批次 ID，逗号分隔；默认全部")
    parser.add_argument("--cases", default="", help="逗号分隔的题号；默认全部")
    parser.add_argument("--apply", action="store_true", help="写回过程记录（默认只预览）")
    parser.add_argument("--no-narrative", action="store_true", help="不补生成过程回顾")
    args = parser.parse_args()

    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

    st = bootstrap()
    repo = st["repo"]
    settings = st["settings"] or {}

    run_ids = [item.strip() for item in args.run.split(",") if item.strip()]
    if not run_ids:
        run_ids = sorted(path.name for path in runs_dir().iterdir() if path.is_dir())
    wanted = {item.strip() for item in args.cases.split(",") if item.strip()}

    llm = None
    if args.apply and not args.no_narrative:
        from evaluator.llm_client import LlmEvaluator

        llm = LlmEvaluator(settings.get("evaluator_llm") or {})
        if not llm.configured:
            print("评估 LLM 未配置：本次只回填过程结论，不生成过程回顾。")
            llm = None

    total = changed = narrated = skipped = 0
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    for run_id in run_ids:
        case_ids = [cid for cid in _case_ids(repo, run_id) if not wanted or cid in wanted]
        if not case_ids:
            print(f"{run_id}: 没有可回填的题")
            continue
        print(f"\n== {run_id}（{len(case_ids)} 题）==")
        for case_id in case_ids:
            case_dir = repo.run_dir(run_id) / "cases" / case_id
            process = _read_json(case_dir / "process.json")
            if not isinstance(process, dict):
                skipped += 1
                print(f"  {case_id}: 跳过（没有 process.json）")
                continue
            entries = _load_entries(case_dir / "trace.jsonl")
            if entries is None:
                # 平台没响应时可能一条条目都没有：时间轴本来就是空的，可以直接按空轨迹重建
                # （只更新「平台无响应」这类结论）；已有时间轴却不能重建状态的才跳过。
                if process.get("timeline"):
                    skipped += 1
                    print(f"  {case_id}: 跳过（没有 trace.jsonl，无法重建已有时间轴的状态）")
                    continue
                entries = []
            result = _read_json(case_dir / "result.json") or {}
            answer = _read_json(case_dir / "agent_answer.json") or {}
            answer_text = str(answer.get("text") or "") if isinstance(answer, dict) else ""
            total += 1

            old_text = _stats(process)
            payload = rebuild_payload(
                process, entries, case_id=case_id, result=result, answer_text=answer_text
            )
            new_text = _stats(payload)
            if old_text != new_text:
                print(f"  {case_id}: {old_text} → {new_text}")
                changed += 1
            else:
                print(f"  {case_id}: {new_text} 不变")

            lines = ""
            has_steps = any(item.get("kind") == "step" for item in payload.get("timeline") or [])
            want_narrative = has_steps and needs_narrative(payload)
            if want_narrative and not args.apply:
                lines = "  （需生成过程回顾）"
            if args.apply:
                if want_narrative and llm is not None:
                    narrative = narrate(llm, payload, str(result.get("question") or ""))
                    if narrative:
                        payload["narrative"] = narrative
                        narrated += 1
                        lines = f"  回顾 {len(narrative.get('walk') or [])} 行"
                    elif process.get("narrative"):
                        payload["narrative"] = process["narrative"]
                        lines = "  回顾生成失败，沿用旧回顾"
                    else:
                        lines = "  回顾生成失败"
                elif process.get("narrative"):
                    # 这次不需要重新解释（或没开生成）：保留已有回顾，重跑是幂等的。
                    payload["narrative"] = process["narrative"]
                _backup(case_dir, run_id, case_id, stamp)
                (case_dir / "process.json").write_text(
                    json.dumps(process_for_disk(payload), ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            if lines:
                print(lines)

    print(
        f"\n{'已写回' if args.apply else '预览'}：{total} 题，结论变化 {changed} 题"
        + (f"，生成回顾 {narrated} 题" if args.apply else "")
        + (f"，跳过 {skipped} 题" if skipped else "")
    )
    if not args.apply:
        print("加 --apply 写回（旧 process.json 会归档到 runtime/backups）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
