# -*- coding: utf-8 -*-
"""加载并校验 CaseContract。"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Dict, List, Optional

from evaluator.models import CaseContract
from evaluator.paths import CONFIG_DIR

CONTRACTS_PATH = CONFIG_DIR / "contracts.json"


class ContractError(ValueError):
    pass


def resolve_contracts_path() -> Path:
    """唯一标准集契约（config/contracts.json，由 golden_builder.runner 生成）。"""
    return CONTRACTS_PATH


def validate_contracts(contracts: List[CaseContract], expected_count: Optional[int] = None) -> List[str]:
    errors: List[str] = []
    ids = [c.case_id for c in contracts]
    blank = [i for i, cid in enumerate(ids) if not cid]
    if blank:
        errors.append("契约 case_id 不能为空")
    dup = [cid for cid, n in Counter(ids).items() if cid and n > 1]
    if dup:
        errors.append(f"契约 case_id 重复: {dup}")
    if expected_count is not None and len(contracts) != expected_count:
        errors.append(f"契约总数应为 {expected_count}，实际 {len(contracts)}")
    for contract in contracts:
        if not contract.numeric_evaluable:
            continue
        if not contract.sql_template and not contract.sql_source:
            errors.append(f"{contract.case_id}: 数值题缺少 SQL")
        if not contract.measures and "measures" not in contract.review_required:
            errors.append(f"{contract.case_id}: 数值题缺少指标")
        if contract.result_type == "detail" and not contract.row_key:
            errors.append(f"{contract.case_id}: 明细题缺少 row_key")
        if contract.realtime_ready and contract.parameter_resolver in {"", "unresolved"}:
            errors.append(f"{contract.case_id}: realtime_ready 但解析器无效")
    return errors


def load_contracts(path: Optional[Path] = None, *, generate_if_missing: bool = True) -> List[CaseContract]:
    target = Path(path) if path else resolve_contracts_path()
    if not target.exists():
        raise FileNotFoundError(target)
    payload = json.loads(target.read_text(encoding="utf-8"))
    raw = payload.get("contracts") if isinstance(payload, dict) else payload
    contracts = [CaseContract.model_validate(item) for item in raw]
    errors = validate_contracts(contracts)
    fatal = [e for e in errors if "缺少 SQL" in e or "重复" in e or "不能为空" in e]
    if fatal:
        raise ContractError("; ".join(fatal))
    return contracts


def contracts_by_id(contracts: Optional[List[CaseContract]] = None) -> Dict[str, CaseContract]:
    return {c.case_id: c for c in (contracts or load_contracts())}
