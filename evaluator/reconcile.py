# -*- coding: utf-8 -*-
"""把智能体回答里的数值抽成结构化集合，再按契约容差与 SQL 基准比对。

比对语义是「基准值覆盖」而不是「整表对齐」：
- HIT   基准值在回答里被正确给出
- MISS  找到了对应维度但数值超出容差
- ABSENT 在回答里定位不到（不等于答错，单独统计）
主指标是 coverage（正确覆盖率）与 value_recall（已定位部分的正确率）。

抽取失败（回答里没有任何可识别结构）时返回 available=False，由上层退回 LLM 判分。
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from evaluator.aliases import _norm, canonical_metric, dimension_aliases, organization_aliases
from evaluator.answer_extract import extract_blocks
from evaluator.models import CaseContract, MeasureSpec
from evaluator.normalizer import is_empty, parse_number, relaxed_tolerance, values_close

SEPARATOR_RE = re.compile(r"^\s*\|?[\s:\-|]+\|?\s*$")
PERIOD_DIM_RE = re.compile(r"月份|期数|期间|period|ym|年月", re.I)
ORG_DIM_RE = re.compile(r"组织|单位|分公司|部门|水司|org", re.I)
AGGREGATE_KEYS = {"合计", "总计", "集团合计", "整体", "环水集团", "环水集团(整体)", "全部", "全集团", "集团整体"}

# 表头单位词只影响该列所有单元格的换算，单元格自身再带单位时以单元格为准。
HEADER_SCALE = (("亿", 100000000.0), ("万", 10000.0))

# 命中这些词的区块多半是取证过程、历史对照，不是最终答案；降权而不是丢弃。
REGION_PENALTY_WORDS = ("过程", "取证", "排查", "尝试", "历史", "对比", "附录", "校验", "探索")
MAX_BLOCKS = 3


def clean_cell(raw: Any) -> str:
    text = str(raw if raw is not None else "")
    text = text.replace("**", "").replace("`", "").replace("↑", "").replace("↓", "")
    return text.strip()


def split_row(line: str) -> List[str]:
    body = line.strip()
    if body.startswith("|"):
        body = body[1:]
    if body.endswith("|"):
        body = body[:-1]
    return [clean_cell(cell) for cell in body.split("|")]


def parse_tables(text: str) -> List[Dict[str, Any]]:
    """抽取回答中的全部 Markdown 表格，按出现顺序返回（保留旧接口）。"""
    blocks = [block for block in extract_blocks(text) if block.get("kind") == "table"]
    return [{"header": [clean_cell(cell) for cell in block["header"]],
             "rows": [[clean_cell(cell) for cell in row] for row in block["rows"]]} for block in blocks]


def _core(label: str) -> str:
    """去掉括号说明和“数/量/合计”类后缀，留下指标主干。"""
    text = re.sub(r"[（(][^）)]*[）)]", "", _norm(label))
    return re.sub(r"(小时数|数量|数|量|合计|总计)$", "", text)


def _header_measure(header: str, measures: Dict[str, MeasureSpec]) -> Optional[str]:
    key = canonical_metric(header, measures)
    if key:
        return key
    base = re.sub(r"[（(][^）)]*[）)]", "", header).strip()
    base = re.sub(r"^(?:20\d{2}\s*年\s*)?\d{1,2}\s*月", "", base).strip()
    if base and base != header:
        key = canonical_metric(base, measures)
        if key:
            return key
    for name, spec in measures.items():
        for label in [name, spec.label, *spec.aliases]:
            if label and _norm(label) and _norm(label) in _norm(header):
                return name
    # 主干包含：处理「在用居民小区 DMA（个）」「平均响应时间(h)」这类带修饰的表头
    header_core = _core(header)
    for name, spec in measures.items():
        for label in [name, spec.label, *spec.aliases]:
            core = _core(label)
            if len(core) >= 2 and (core in header_core or header_core in core):
                return name
    return None


def _header_scale(header: str, spec: MeasureSpec) -> float:
    if spec.value_scale != "volume":
        return 1.0
    for word, factor in HEADER_SCALE:
        if word in header:
            return factor
    return 1.0


def _column_map(header: Sequence[str], measures: Dict[str, MeasureSpec]) -> Dict[int, Tuple[str, float]]:
    out: Dict[int, Tuple[str, float]] = {}
    for index, title in enumerate(header):
        key = _header_measure(title, measures)
        if key and key not in {item[0] for item in out.values()}:
            out[index] = (key, _header_scale(title, measures[key]))
    # 兜底：若只剩一个指标和一行未被占用的列，按位置配对（表头写法差异太大时用）
    missing = [name for name in measures if name not in {item[0] for item in out.values()}]
    free = [index for index in range(len(header)) if index not in out]
    if len(missing) == 1 and len(free) == 1:
        out[free[0]] = (missing[0], _header_scale(header[free[0]], measures[missing[0]]))
    return out


def _default_year(requested: Dict[str, Any]) -> Optional[int]:
    for value in (requested or {}).values():
        text = str(value)
        if re.fullmatch(r"20\d{2}(0[1-9]|1[0-2])", text):
            return int(text[:4])
        match = re.fullmatch(r"(20\d{2})-\d{2}-\d{2}", text)
        if match:
            return int(match.group(1))
    return None


def _period_key(text: str, default_year: Optional[int]) -> str:
    raw = str(text or "").strip()
    if re.fullmatch(r"20\d{2}(0[1-9]|1[0-2])", raw):
        return raw
    match = re.search(r"(20\d{2})\D{0,4}(\d{1,2})", raw)
    if match and 1 <= int(match.group(2)) <= 12:
        return f"{match.group(1)}{int(match.group(2)):02d}"
    match = re.fullmatch(r"(\d{1,2})\s*月?", raw)
    if match and default_year and 1 <= int(match.group(1)) <= 12:
        return f"{default_year}{int(match.group(1)):02d}"
    match = re.search(r"(\d{1,2})\s*月", raw)
    if match and default_year and 1 <= int(match.group(1)) <= 12:
        return f"{default_year}{int(match.group(1)):02d}"
    return raw


def _org_key(text: str) -> str:
    raw = _norm(text)
    aliases = organization_aliases()
    if raw in aliases:
        return aliases[raw]
    # 「其他(数据归口未细分)」「环水集团(整体)」这类带说明的写法，去掉括号再查一次
    stripped = re.sub(r"\([^)]*\)", "", raw).strip()
    return aliases.get(stripped, stripped or raw)


def _key_of(value: Any, dim: str, default_year: Optional[int]) -> str:
    text = clean_cell(value)
    if PERIOD_DIM_RE.search(dim):
        return _period_key(text, default_year)
    if ORG_DIM_RE.search(dim):
        return _org_key(text)
    return _norm(text)


def _row_key_value(row: Sequence[str], columns: Dict[str, int], contract: CaseContract, default_year) -> str:
    parts = []
    for dim in contract.row_key:
        index = columns.get(dim)
        if index is None:
            return ""
        parts.append(_key_of(row[index] if index < len(row) else "", dim, default_year))
    return "|".join(parts)


def _alias_columns(header: Sequence[str], contract: CaseContract) -> Dict[str, int]:
    """按维度别名定位行键列；找不到就留空，绝不退回第 0 列。"""
    found: Dict[str, int] = {}
    for dim in contract.row_key:
        names = {_norm(name) for name in dimension_aliases(dim)}
        for index, title in enumerate(header):
            title_norm = _norm(title)
            if title_norm == _norm(dim) or title_norm in names or any(name in title_norm for name in names):
                found[dim] = index
                break
    return found


def _match_rate(values: Sequence[str], targets: set) -> float:
    if not values or not targets:
        return 0.0
    hits = sum(1 for value in values if value in targets)
    return hits / len(values)


def _resolve_key_columns(
    header: Sequence[str],
    rows: Sequence[Sequence[str]],
    contract: CaseContract,
    targets: Dict[str, set],
    default_year: Optional[int],
) -> Dict[str, int]:
    """先按别名定位，再按「列值能对上多少基准键」数据驱动定位。"""
    found = _alias_columns(header, contract)
    for dim in contract.row_key:
        if dim in found:
            continue
        best_index, best_rate = None, 0.0
        for index, _title in enumerate(header):
            values = [_key_of(row[index], dim, default_year) for row in rows if index < len(row)]
            rate = _match_rate(values, targets.get(dim, set()))
            if rate > best_rate:
                best_index, best_rate = index, rate
        if best_index is not None and best_rate >= 0.5:
            found[dim] = best_index
    return found


def _region_penalty(context: str) -> int:
    text = str(context or "")
    return 12 if any(word in text for word in REGION_PENALTY_WORDS) else 0


def _score_table(block, measure_count: int, key_ok: bool, penalty: int) -> int:
    return measure_count * 10 + (5 if key_ok else 0) + min(len(block["rows"]), 20) - penalty


def _compare_value(cell: str, sql_value: Any, spec: MeasureSpec, scale: float):
    """返回 (状态, 归一后的回答值, 归一后的基准值, 容差说明)。状态：hit / miss / empty。"""
    if spec.value_scale == "text":
        left = clean_cell(cell)
        right = clean_cell(sql_value)
        return ("hit" if left == right else "miss"), left, right, None
    if is_empty(cell) or not re.search(r"\d", cell):
        # 「—（年起始月，无上月累计基数）」这类说明性空值按空处理
        return ("hit" if is_empty(sql_value) else "empty"), None, sql_value, None
    agent_value, _unit, _error = parse_number(cell, spec, "agent")
    if agent_value is None:
        return "empty", None, sql_value, None
    if scale != 1.0 and not re.search(r"[亿万]", cell):
        agent_value *= scale
    sql_value_norm, _sql_unit, _sql_error = parse_number(sql_value, spec, "sql")
    if sql_value_norm is None:
        return "empty", agent_value, sql_value, None
    tolerance, reason = relaxed_tolerance(spec, sql_value_norm, cell, scale)
    effective = spec if tolerance is None or tolerance == spec.tolerance else spec.model_copy(update={"tolerance": tolerance})
    close, _delta, _rule = values_close(sql_value_norm, agent_value, effective)
    if not close and spec.value_scale in {"ratio01", "percent", "pp"}:
        # 裸数在 0–1 与百分数两种尺度间是歧义的（-0.59pp vs -59pp），取能对上的那个
        for alternative in (agent_value * 100, agent_value / 100):
            ok, _delta, _rule = values_close(sql_value_norm, alternative, effective)
            if ok:
                agent_value = alternative
                close = True
                break
    note = None
    if tolerance is not None and tolerance != spec.tolerance:
        note = {"kind": tolerance.kind, "eps": tolerance.eps, "abs_floor": tolerance.abs_floor, "reason": reason}
    return ("hit" if close else "miss"), agent_value, sql_value_norm, note


def _items_to_entries(block, contract: CaseContract, measures, default_year, targets=None):
    """条目块：每行是 {_name, 指标: 片段}，只支持单层行键。

    只保留名称能对上基准键的条目：散文里的冒号行很容易被误当成数据行，
    不放行它们就不会污染覆盖率。
    """
    if len(contract.row_key) > 1:
        return []
    dim = contract.row_key[0] if contract.row_key else ""
    allowed = (targets or {}).get(dim) or set()
    entries = []
    for row in block["rows"]:
        name = row.get("_name", "")
        key = _key_of(name, dim, default_year) if dim else ""
        if allowed and key not in allowed:
            continue
        cells = {measure: value for measure, value in row.items() if measure in measures}
        if not cells:
            continue
        entries.append((key, cells, [name]))
    return entries


def _table_to_entries(block, contract, measures, targets, default_year):
    header = [clean_cell(cell) for cell in block["header"]]
    rows = [[clean_cell(cell) for cell in row] for row in block["rows"]]
    columns = _column_map(header, measures)
    if not columns:
        # 表头认不出指标就放弃这块，交给 LLM。凭位置猜列会把「指标 | 数值」这类
        # 长表错配成数值列，反而制造误判（见 tests/test_orchestrator.py 的失败兜底约定）。
        return [], None, None
    key_columns = _resolve_key_columns(header, rows, contract, targets, default_year) if contract.row_key else {}
    if contract.row_key and len(key_columns) < len(contract.row_key):
        return [], columns, None
    entries = []
    for row in rows:
        key = _row_key_value(row, key_columns, contract, default_year) if contract.row_key else ""
        cells = {measure: (row[index] if index < len(row) else "") for index, (measure, _scale) in columns.items()}
        entries.append((key, cells, row))
    scales = {measure: scale for _index, (measure, scale) in columns.items()}
    return entries, columns, scales


def align_rows(
    contract: CaseContract,
    answer_text: str,
    sql_rows: Sequence[Dict[str, Any]],
    requested_params: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """把回答里的数值与 SQL 基准按 (维度组合, 指标) 做覆盖比对。"""
    detail: Dict[str, Any] = {
        "available": False,
        "reason": "",
        "schema_version": "v2",
        "row_key": list(contract.row_key),
        "columns": [],
        "labels": {},
        "rows": [],
        "sql_rows": len(sql_rows or []),
        "answer_rows": 0,
        "matched_rows": 0,
        "missing_rows": [],
        "extra_rows": [],
        "checked_values": 0,
        "hit_values": 0,
        "wrong_values": [],
        "missing_values": [],
        "row_hit_rate": None,
        "value_hit_rate": None,
        "counts": {"total": 0, "hit": 0, "wrong": 0, "absent": 0},
        "coverage": None,
        "value_recall": None,
        "absent_rate": None,
        "items": [],
        "sources": [],
    }
    if not sql_rows:
        detail["reason"] = "no_sql_rows"
        return detail
    if not contract.row_key and len(sql_rows) > 1:
        # 没有行键就无法把多行基准对到回答的某一行，交给 LLM
        detail["reason"] = "no_row_key"
        return detail
    measures = contract.measures or {}
    if not measures:
        detail["reason"] = "no_measure_columns"
        return detail

    default_year = _default_year(requested_params or {})
    measure_labels = {
        key: [label for label in [spec.label, *spec.aliases] if label] for key, spec in measures.items()
    }
    blocks = extract_blocks(answer_text or "", measure_labels)
    if not blocks:
        detail["reason"] = "no_table"
        return detail

    targets = {
        dim: {_key_of(row.get(dim), dim, default_year) for row in sql_rows} for dim in contract.row_key
    }

    # 逐块解析并打分，按分数从高到低合并，避免只取一张表导致选错。
    scored = []
    for block in blocks:
        if block.get("kind") == "items":
            entries = _items_to_entries(block, contract, measures, default_year, targets)
            if not entries:
                continue
            score = len(measures) * 10 + (5 if contract.row_key else 0) + min(len(entries), 20) - 3 - _region_penalty(block.get("context"))
            scored.append((score, block, entries, {measure: 1.0 for measure in measures}, [measure for measure in measures]))
            continue
        entries, columns, scales = _table_to_entries(block, contract, measures, targets, default_year)
        if not entries or not columns:
            continue
        key_ok = not contract.row_key or bool(columns) and scales is not None
        score = _score_table(block, len(columns), key_ok, _region_penalty(block.get("context")))
        if score <= 0:
            continue
        scored.append((score, block, entries, scales, [measure for _index, (measure, _scale) in sorted(columns.items())]))

    if not scored:
        detail["reason"] = "no_measure_columns"
        return detail

    scored.sort(key=lambda item: item[0], reverse=True)
    picked = scored[:MAX_BLOCKS]
    detail["available"] = True
    detail["sources"] = [{"source": block.get("source"), "line": block.get("line"), "score": score,
                          "context": block.get("context", "")} for score, block, _entries, _scales, _cols in picked]

    answer_cells: Dict[str, Dict[str, str]] = {}
    scales_by_measure: Dict[str, float] = {measure: 1.0 for measure in measures}
    ordered_measures: List[str] = []
    for _score, _block, entries, scales, columns in picked:
        for measure, scale in (scales or {}).items():
            scales_by_measure.setdefault(measure, scale)
        for measure in columns:
            if measure not in ordered_measures:
                ordered_measures.append(measure)
        for key, cells, _raw in entries:
            bucket = answer_cells.setdefault(key, {})
            for measure, cell in cells.items():
                if measure not in bucket or not str(bucket[measure]).strip():
                    bucket[measure] = cell

    detail["answer_rows"] = len(answer_cells)
    detail["columns"] = ordered_measures
    detail["labels"] = {key: (measures[key].label or key) for key in ordered_measures}
    detail["display_columns"] = list(measures)

    sql_by_key: Dict[str, Dict[str, Any]] = {}
    for row in sql_rows:
        if contract.row_key:
            key = "|".join(_key_of(row.get(dim), dim, default_year) for dim in contract.row_key)
        else:
            key = ""
        sql_by_key.setdefault(key, row)

    rows: List[Dict[str, Any]] = []
    matched = hits = checked = 0
    wrong_count = absent_count = 0
    wrong_values: List[Dict[str, Any]] = []
    missing_values: List[Dict[str, Any]] = []
    items: List[Dict[str, Any]] = []

    for key, sql_row in sql_by_key.items():
        entry = {"key": key, "status": "hit", "answer": {}, "answer_norm": {}, "sql": {}, "cells": {}}
        bucket = answer_cells.get(key)
        for measure_key, spec in measures.items():
            column = spec.sql_column or measure_key
            value = sql_row.get(column)
            if value is None:
                value = sql_row.get(measure_key)
            entry["sql"][measure_key] = value
            if not spec.required and is_empty(value):
                entry["cells"][measure_key] = "na"
                continue
            cell = "" if bucket is None else str(bucket.get(measure_key, ""))
            status, agent_value, sql_norm, tolerance = _compare_value(
                cell, value, spec, scales_by_measure.get(measure_key, 1.0)
            )
            if status == "empty":
                entry["cells"][measure_key] = "absent"
                absent_count += 1
                missing_values.append({"key": key, "field": measure_key})
                items.append({
                    "key": key, "measure": measure_key, "status": "ABSENT",
                    "sql_value": sql_norm if sql_norm is not None else value,
                    "answer_span": cell or None, "tolerance": tolerance,
                })
                continue
            entry["answer"][measure_key] = cell
            entry["answer_norm"][measure_key] = agent_value
            checked += 1
            if status == "hit":
                hits += 1
                entry["cells"][measure_key] = "hit"
                items.append({
                    "key": key, "measure": measure_key, "status": "HIT",
                    "sql_value": sql_norm, "answer_value": agent_value,
                    "answer_span": cell, "tolerance": tolerance,
                })
            else:
                wrong_count += 1
                entry["cells"][measure_key] = "miss"
                wrong_values.append(
                    {"key": key, "field": measure_key, "agent": agent_value, "sql": sql_norm, "evidence": cell}
                )
                items.append({
                    "key": key, "measure": measure_key, "status": "MISS",
                    "sql_value": sql_norm, "answer_value": agent_value,
                    "answer_span": cell, "tolerance": tolerance,
                })
        if bucket is None:
            entry["status"] = "missing"
            detail["missing_rows"].append({"key": key})
        else:
            matched += 1
        rows.append(entry)

    for key, bucket in answer_cells.items():
        if key in sql_by_key:
            continue
        if contract.row_key and _norm(key) in AGGREGATE_KEYS:
            continue
        entry = {"key": key, "status": "extra", "answer": dict(bucket), "sql": {}, "cells": {}}
        detail["extra_rows"].append({"key": key})
        rows.append(entry)

    total = hits + wrong_count + absent_count
    detail["rows"] = rows
    detail["items"] = items
    detail["wrong_values"] = wrong_values
    detail["missing_values"] = missing_values
    detail["matched_rows"] = matched
    detail["checked_values"] = checked
    detail["hit_values"] = hits
    detail["counts"] = {"total": total, "hit": hits, "wrong": wrong_count, "absent": absent_count}
    detail["row_hit_rate"] = round(matched / len(sql_by_key), 4) if sql_by_key else None
    detail["value_hit_rate"] = round(hits / checked, 4) if checked else None
    detail["coverage"] = round(hits / total, 4) if total else None
    located = hits + wrong_count
    detail["value_recall"] = round(hits / located, 4) if located else None
    detail["absent_rate"] = round(absent_count / total, 4) if total else None
    return detail


RECONCILE_KEYS = (
    "available", "reason", "schema_version", "row_key", "sql_rows", "answer_rows", "matched_rows",
    "missing_rows", "extra_rows", "checked_values", "hit_values", "wrong_values",
    "missing_values", "row_hit_rate", "value_hit_rate",
    "counts", "coverage", "value_recall", "absent_rate", "sources",
)


def reconcile(
    contract: CaseContract,
    answer_text: str,
    sql_rows: Sequence[Dict[str, Any]],
    requested_params: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """把回答数值与 SQL 基准做覆盖比对。不可比时 available=False。"""
    return {key: value for key, value in align_rows(contract, answer_text, sql_rows, requested_params).items() if key in RECONCILE_KEYS}


def verdict_from_report(report: Dict[str, Any], *, partial_floor: float = 0.5) -> Optional[str]:
    """按覆盖情况给出结论；不可比时返回 None，交给 LLM 判。

    v2 语义：多给信息不扣分，漏给扣分；一个基准值都定位不到时不下结论。
    旧批次没有 coverage/value_recall，退回 v1 的行命中率口径。
    """
    if not report.get("available"):
        return None
    coverage = report.get("coverage")
    recall = report.get("value_recall")
    if coverage is None or recall is None:
        row_rate = report.get("row_hit_rate")
        value_rate = report.get("value_hit_rate")
        if row_rate is None:
            return None
        rates = [rate for rate in (row_rate, value_rate) if rate is not None]
        if not rates:
            return None
        # 多出来的非汇总行没有基准可比，不能据此判合格，最多算部分作答
        if row_rate >= 1.0 and not report.get("extra_rows") and (value_rate is None or value_rate >= 1.0):
            return "QUALIFIED"
        if min(rates) >= partial_floor:
            return "PARTIAL"
        return "UNQUALIFIED"
    # 抽取不足：几乎什么都没定位到，交回 LLM 判，不冒充"答错"。
    absent_rate = report.get("absent_rate")
    if absent_rate is not None and absent_rate >= 0.9:
        return None
    if coverage >= 1.0 and recall >= 1.0:
        return "QUALIFIED"
    if recall < partial_floor:
        return "UNQUALIFIED"
    if coverage >= partial_floor or recall >= partial_floor:
        return "PARTIAL"
    return "UNQUALIFIED"
