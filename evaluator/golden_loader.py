# -*- coding: utf-8 -*-
"""加载标准集 JSON（data/golden/golden_dataset.json）。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from evaluator.paths import golden_dir as configured_golden_dir

JSON_NAME = "golden_dataset.json"
NUMERIC_RESULT_TYPES = {"stat", "detail"}


def default_golden_dir(override: Optional[str | Path] = None) -> Path:
    if override:
        return Path(override)
    return configured_golden_dir()


def load_golden_json(golden_dir: Optional[Path] = None, *, name: str = JSON_NAME) -> Dict[str, Any]:
    path = default_golden_dir(golden_dir) / name
    if not path.exists():
        raise FileNotFoundError(f"找不到黄金集 JSON: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def is_numeric_case(case: Dict[str, Any]) -> bool:
    return str(case.get("result_type") or "") in NUMERIC_RESULT_TYPES


def merge_golden(golden_dir: Optional[Path] = None, *, json_name: str = JSON_NAME) -> Dict[str, Any]:
    payload = load_golden_json(golden_dir, name=json_name)
    cases: List[Dict[str, Any]] = []
    for case in payload.get("cases", []):
        row = dict(case)
        row["numeric_evaluable"] = is_numeric_case(case)
        cases.append(row)
    return {
        "meta": payload.get("meta") or {},
        "cases": cases,
        "json_count": len(cases),
        "numeric_count": sum(1 for c in cases if c["numeric_evaluable"]),
    }


def business_verified_ids(golden: Dict[str, Any]) -> set:
    """黄金集里标记为业务核对过的题号。"""
    return {c["case_id"] for c in (golden or {}).get("cases", []) if c.get("business_verified")}


def case_by_id(golden: Dict[str, Any], case_id: str) -> Dict[str, Any]:
    for case in golden["cases"]:
        if case["case_id"] == case_id:
            return case
    raise KeyError(case_id)
