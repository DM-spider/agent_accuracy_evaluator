# -*- coding: utf-8 -*-
"""执行 16 题 CLI 对账，结果写入 out/results.json。"""
from __future__ import annotations

import json
import time
import traceback
from pathlib import Path

from cli_eval.cases import CASES, HANDLERS

OUT = Path(__file__).resolve().parent / "out"
ORDER = [
    "CX-033",
    "CX-032",
    "CX-036",
    "CX-037",
    "JL-018",
    "JL-036",
    "BJ-003",
    "BJ-016",
    "BJ-017",
    "DMA-002",
    "DMA-026",
    "JL-016",
    "JL-019",
    "JL-021",
    "JL-022",
    "BJ-012",
]


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    results = []
    for case_id in ORDER:
        handler = HANDLERS[case_id]
        started = time.perf_counter()
        print(f"[{case_id}] running ...", flush=True)
        try:
            record = handler()
        except Exception as exc:  # 单题失败不阻塞整批
            record = {
                "case_id": case_id,
                "question": CASES[case_id]["question"],
                "category": CASES[case_id].get("category"),
                "result_type": CASES[case_id].get("result_type"),
                "status": "ERROR",
                "plan": [],
                "actual": None,
                "actual_text": "",
                "expected_text": "",
                "db_text": "",
                "diffs": [],
                "root_cause": f"测试脚本异常：{type(exc).__name__}: {exc}",
                "recommendation": "需要人工复核该题的取数脚本。",
                "evidence": traceback.format_exc()[-1200:],
                "standard_vs_db": {},
            }
        record["elapsed_seconds"] = round(time.perf_counter() - started, 1)
        results.append(record)
        print(
            f"[{case_id}] {record['status']}  diffs={len(record.get('diffs') or [])}  "
            f"{record['elapsed_seconds']}s",
            flush=True,
        )
    payload = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "total": len(results),
        "results": results,
    }
    (OUT / "results.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(f"\nwritten {OUT / 'results.json'}")


if __name__ == "__main__":
    main()
