# -*- coding: utf-8 -*-
"""从 perfect 样本生成 20 题混合模拟回答。"""
from __future__ import annotations

import json
from pathlib import Path

from evaluator.golden_loader import load_agent_answers
from evaluator.mock_demo import MIXED_ANSWERS_FILE, MIXED_CASE_IDS, PLANTED_ERRORS, write_mock_script
from evaluator.paths import GOLDEN_DIR


def _keep_table_rows(text: str, n_data_rows: int) -> str:
    lines = text.splitlines()
    header, sep, data = [], [], []
    for line in lines:
        if not line.strip().startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if all(set(c) <= set("-: ") and len(c) >= 2 for c in cells):
            sep.append(line)
            continue
        if not header:
            header.append(line)
        else:
            data.append(line)
    return "\n".join(header + sep + data[:n_data_rows])


def main() -> None:
    perfect = {c["case_id"]: c for c in load_agent_answers("agent_answers_perfect.json")["cases"]}
    out_cases = []
    for case_id in MIXED_CASE_IDS:
        src = perfect[case_id]
        text = src["final_answer_text"]
        if case_id == "CX03":
            text = text.replace("| 莲塘供水 | 14.0800 |", "| 莲塘供水 | 19.0800 |")
        elif case_id == "CX04":
            text = text.replace("| 南山分公司 | 11.8400 |", "| 南山分公司 | 16.8400 |")
        elif case_id == "CX06":
            text = text.replace("| 深汕水务 | 0.0000 | 0.0000 | 0.0000 |", "| 深汕水务 | 5.0000 | 0.0000 | 0.0000 |")
        elif case_id == "CX09":
            text = text.replace("| 南山分公司 | 14,336,141.99 |", "| 南山分公司 | 14,336,146.99 |")
        elif case_id == "CX10":
            text = text.replace("| 宝安产销差率(集团口径) | 5.3500  |", "| 宝安产销差率(集团口径) | 7.3500  |")
            text = text.replace("| 集团产销差率 | 7.5500  |", "| 集团产销差率 | 9.5500  |")
        elif case_id == "LS4":
            text = _keep_table_rows(text, 5)
        elif case_id == "BJ2":
            text = text.replace("| 131005 | 福田保税区 | 福田分公司 | 61.5900 |", "| 131005 | 福田保税区 | 福田分公司 | 91.5900 |")
        elif case_id == "GD2":
            text = text.replace("| 本月(202607)完成率 | 93.2800  |", "| 本月(202607)完成率 | 83.2800  |")
        elif case_id == "GD3":
            text = text.replace("| 45分钟到场及时率 | 34.0900  |", "| 45分钟到场及时率 | 54.0900  |")
        elif case_id == "SB3":
            text = text.replace("| 龙岗水务集团 | 571 |", "| 龙岗水务集团 | 671 |")
        planted = PLANTED_ERRORS.get(case_id)
        out_cases.append(
            {
                "case_id": case_id,
                "question_asked": src["question_asked"],
                "final_answer_text": text,
                "expected_outcome": "ERROR" if planted else "PASS",
                "planted_error": planted,
                "steps": [],
            }
        )
    payload = {
        "run_id": "demo-mixed-20-2026-08-31",
        "agent": "mock-mixed-agent",
        "model": "fixture",
        "run_time": "2026-08-31",
        "note": "无真实智能体时的 20 题模拟：10 题与金标一致，10 题植入错值或漏行。",
        "cases": out_cases,
    }
    dest = GOLDEN_DIR / MIXED_ANSWERS_FILE
    dest.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    script = write_mock_script(out_cases)
    print(f"wrote {dest} cases={len(out_cases)}")
    print(f"wrote {script}")


if __name__ == "__main__":
    main()
