# -*- coding: utf-8 -*-
"""组织名归一、数值解析、标准值比对。"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

EVAL_ROOT = Path(__file__).resolve().parent.parent
ALIAS_FILE = EVAL_ROOT / "config" / "organization_aliases.json"

# CLI 实际返回、评测台别名表尚未覆盖的名称
LOCAL_ALIASES = {
    "大鹏环水公司": "大鹏新区水务",
    "深圳市环境水务集团": "集团",
    "环水集团": "集团",
    "本部区域分公司": "集团本部",
}


def load_aliases() -> Dict[str, str]:
    aliases: Dict[str, str] = {}
    if ALIAS_FILE.exists():
        aliases.update(json.loads(ALIAS_FILE.read_text(encoding="utf-8")))
    aliases.update(LOCAL_ALIASES)
    return aliases


ALIASES = load_aliases()


def org_name(raw: Any) -> str:
    text = "" if raw is None else str(raw).strip()
    return ALIASES.get(text, text)


def num(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "")
    if not text or text.lower() in {"null", "none", "nan", "-"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def key_text(value: Any) -> str:
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return re.sub(r"\s+", "", str(value)).strip().lower()


def period_of(row: Dict[str, Any]) -> str:
    for key in ("periodPoint", "businessyearmonth", "warndate", "create_time"):
        if key in row and row[key] not in (None, ""):
            return str(row[key])[:10] if key in ("warndate", "create_time") else str(row[key])
    return ""


def values_close(expected: Optional[float], actual: Optional[float], tolerance: Dict[str, Any]) -> bool:
    if expected is None and actual is None:
        return True
    if expected is None or actual is None:
        return False
    kind = tolerance.get("kind", "abs")
    if kind == "exact":
        return abs(expected - actual) < 1e-9
    if kind == "rel":
        eps = float(tolerance.get("eps", 0.01))
        floor = float(tolerance.get("abs_floor", 0.0))
        return abs(expected - actual) <= max(abs(expected) * eps, floor)
    eps = float(tolerance.get("eps", 0.01))
    floor = float(tolerance.get("abs_floor", 0.0))
    return abs(expected - actual) <= max(eps, floor)


def compare_rows(
    actual_rows: Sequence[Dict[str, Any]],
    expected_rows: Sequence[Dict[str, Any]],
    *,
    key_actual: str,
    key_expected: str,
    fields: Sequence[Dict[str, Any]],
    alias_actual: bool = True,
) -> List[Dict[str, Any]]:
    """按 key 对齐，逐字段比较。

    fields 每项：{"expected": 标准字段名, "actual": CLI 字段名, "tolerance": {...}}
    """
    def norm(value: Any) -> str:
        if alias_actual:
            value = org_name(value)
        return key_text(value)

    actual_map = {norm(row.get(key_actual)): row for row in actual_rows}
    diffs: List[Dict[str, Any]] = []
    for exp in expected_rows:
        expected_key = org_name(exp.get(key_expected)) if alias_actual else str(exp.get(key_expected))
        act = actual_map.get(norm(exp.get(key_expected)))
        if act is None:
            diffs.append({
                "type": "MISSING_ROW",
                "key": expected_key,
                "message": f"CLI 未返回「{expected_key}」",
            })
            continue
        for spec in fields:
            field = spec["expected"]
            actual_field = spec.get("actual", field)
            exp_val = num(exp.get(field))
            act_val = num(act.get(actual_field))
            if act_val is None and exp_val is None:
                continue
            if act_val is None:
                diffs.append({
                    "type": "MISSING_FIELD",
                    "key": expected_key,
                    "field": field,
                    "expected": exp_val,
                    "actual": None,
                    "message": f"「{expected_key}」缺少字段「{field}」",
                })
                continue
            if exp_val is None:
                diffs.append({
                    "type": "VALUE_DIFF",
                    "key": expected_key,
                    "field": field,
                    "expected": None,
                    "actual": act_val,
                    "delta": None,
                    "message": f"「{expected_key}」{field}: 标准为空 / CLI {act_val}",
                })
                continue
            if not values_close(exp_val, act_val, spec.get("tolerance", {})):
                diffs.append({
                    "type": "VALUE_DIFF",
                    "key": expected_key,
                    "field": field,
                    "expected": exp_val,
                    "actual": act_val,
                    "delta": round(act_val - exp_val, 6) if exp_val is not None else None,
                    "message": f"「{expected_key}」{field}: 标准 {exp_val} / CLI {act_val}",
                })
    return diffs


def compare_detail(
    actual_rows: Sequence[Dict[str, Any]],
    expected: Dict[str, Any],
    *,
    actual_key: str,
    actual_value: str,
    actual_org: str = "dimensionValue",
    expected_org: str = "组织",
) -> List[Dict[str, Any]]:
    """兼容旧调用：单字段、按组织比较。"""
    return compare_rows(
        actual_rows,
        expected.get("rows", []),
        key_actual=actual_org,
        key_expected=expected_org,
        fields=[
            {
                "expected": spec["key"],
                "actual": actual_value,
                "tolerance": spec.get("tolerance", {}),
            }
            for spec in expected.get("value_fields", [])
        ],
    )


def compare_stat(actual: Dict[str, Any], expected: Dict[str, Any]) -> List[Dict[str, Any]]:
    diffs: List[Dict[str, Any]] = []
    for spec in expected.get("fields", []):
        key = spec["key"]
        exp_val = num(spec.get("value"))
        act_val = num(actual.get(key))
        if act_val is None:
            diffs.append({
                "type": "MISSING_FIELD",
                "field": key,
                "expected": exp_val,
                "actual": None,
                "message": f"缺少统计值「{key}」",
            })
        elif not values_close(exp_val, act_val, spec.get("tolerance", {})):
            diffs.append({
                "type": "VALUE_DIFF",
                "field": key,
                "expected": exp_val,
                "actual": act_val,
                "delta": round(act_val - exp_val, 6) if exp_val is not None else None,
                "message": f"{key}: 标准 {exp_val} / CLI {act_val}",
            })
    return diffs
