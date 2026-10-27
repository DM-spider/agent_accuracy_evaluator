# -*- coding: utf-8 -*-
"""执行 16 题 CLI 对账，结果写入当天的版本目录 out/<日期>/results.json。"""
from __future__ import annotations

import argparse
import json
import time
import traceback

from cli_eval.cases import CASES, HANDLERS
from cli_eval.outdir import ensure_version_dir, use_date

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
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", help="版本日期 YYYYMMDD（如 20261027），默认今天（同一天重跑会覆盖当天版本）")
    args = parser.parse_args()
    version = use_date(args.date)
    out = ensure_version_dir()
    if (out / "results.json").exists():
        print(f"注意：{version} 已有结果，本次运行会覆盖当天版本")
    if not (out / "golden_db").exists():
        print(f"注意：{out / 'golden_db'} 不存在，标准值将退回黄金集里的静态结果；建议先跑 check_golden_db.py")
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
        "version": version,
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "total": len(results),
        "results": results,
    }
    (out / "results.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(f"\nwritten {out / 'results.json'}")


if __name__ == "__main__":
    main()
