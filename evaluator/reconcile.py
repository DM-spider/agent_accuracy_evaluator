# -*- coding: utf-8 -*-
"""把智能体回答里的表格抽成结构化行，再按契约容差与 SQL 基准逐行比对。

和 cli_eval 的取数比对同一套语义：按 row_key 对齐、每个指标用自己的容差、
全中才算命中。区别只在于数据来源是自然语言回答，而不是接口返回的 JSON。

抽取失败（回答没有表格）时返回 available=False，由上层退回 LLM 判分。
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from evaluator.aliases import _norm, canonical_metric, dimension_aliases, organization_aliases
from evaluator.models import CaseContract, MeasureSpec
from evaluator.normalizer import is_empty, parse_number, values_close

SEPARATOR_RE = re.compile(r"^\s*\|?[\s:\-|]+\|?\s*$")
PERIOD_DIM_RE = re.compile(r"月份|期数|期间|period|ym|年月", re.I)
ORG_DIM_RE = re.compile(r"组织|单位|分公司|部门|水司|org", re.I)
AGGREGATE_KEYS = {"合计", "总计", "集团合计", "整体", "环水集团", "环水集团(整体)", "全部", "全集团", "集团整体"}

# 表头单位词只影响该列所有单元格的换算，单元格自身再带单位时以单元格为准。
HEADER_SCALE = (("亿", 100000000.0), ("万", 10000.0))


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
    """抽取回答中的全部 Markdown 表格，按出现顺序返回。"""
    tables: List[Dict[str, Any]] = []
    lines = (text or "").splitlines()
    index = 0
    while index < len(lines):
        line = lines[index].strip()
        if (
            line.startswith("|")
            and index + 1 < len(lines)
            and "-" in lines[index + 1]
            and SEPARATOR_RE.match(lines[index + 1].strip())
        ):
            header = split_row(lines[index])
            rows: List[List[str]] = []
            cursor = index + 2
            while cursor < len(lines) and lines[cursor].strip().startswith("|"):
                rows.append(split_row(lines[cursor]))
                cursor += 1
            tables.append({"header": header, "rows": rows})
            index = cursor
            continue
        index += 1
    return tables


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


def _find_key_columns(header: Sequence[str], contract: CaseContract) -> Dict[str, int]:
    found: Dict[str, int] = {}
    for dim in contract.row_key:
        names = {_norm(name) for name in dimension_aliases(dim)}
        for index, title in enumerate(header):
            title_norm = _norm(title)
            if title_norm == _norm(dim) or title_norm in names or any(name in title_norm for name in names):
                found[dim] = index
                break
        if dim not in found:
            # 退回位置约定：第一个维度用第一列
            found[dim] = 0
    return found


def _row_key_value(row: Sequence[str], columns: Dict[str, int], contract: CaseContract, default_year) -> str:
    parts = []
    for dim in contract.row_key:
        index = columns.get(dim, 0)
        parts.append(_key_of(row[index] if index < len(row) else "", dim, default_year))
    return "|".join(parts)


def _pick_table(tables, measures, contract):
    best = None
    for table in tables:
        columns = _column_map(table["header"], measures)
        if not columns:
            continue
        key_columns = _find_key_columns(table["header"], contract) if contract.row_key else {}
        score = len(columns) * 10 + (5 if key_columns else 0) + min(len(table["rows"]), 20)
        if best is None or score > best[0]:
            best = (score, table, columns, key_columns)
    return best


def _compare_value(cell: str, sql_value: Any, spec: MeasureSpec, scale: float):
    """返回 (状态, 归一后的回答值, 归一后的基准值)。状态：hit / miss / empty。"""
    if spec.value_scale == "text":
        left = clean_cell(cell)
        right = clean_cell(sql_value)
        return ("hit" if left == right else "miss"), left, right
    if is_empty(cell) or not re.search(r"\d", cell):
        # 「—（年起始月，无上月累计基数）」这类说明性空值按空处理
        return ("empty" if not is_empty(sql_value) else "hit"), None, sql_value
    agent_value, _unit, _error = parse_number(cell, spec, "agent")
    if agent_value is None:
        return "miss", None, sql_value
    if scale != 1.0 and not re.search(r"[亿万]", cell):
        agent_value *= scale
    sql_value_norm, _sql_unit, _sql_error = parse_number(sql_value, spec, "sql")
    if sql_value_norm is None:
        return "empty", agent_value, sql_value
    close, _delta, _rule = values_close(sql_value_norm, agent_value, spec)
    if not close and spec.value_scale in {"ratio01", "percent", "pp"}:
        # 裸数在 0–1 与百分数两种尺度间是歧义的（-0.59pp vs -59pp），取能对上的那个
        for alternative in (agent_value * 100, agent_value / 100):
            ok, _delta, _rule = values_close(sql_value_norm, alternative, spec)
            if ok:
                agent_value = alternative
                close = True
                break
    return ("hit" if close else "miss"), agent_value, sql_value_norm


def align_rows(
    contract: CaseContract,
    answer_text: str,
    sql_rows: Sequence[Dict[str, Any]],
    requested_params: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """把回答表格与 SQL 基准按行键对齐。

    除了命中统计，还返回逐行逐指标的取值和命中状态，供导出报告左右并排渲染。
    """
    detail: Dict[str, Any] = {
        "available": False,
        "reason": "",
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
    }
    if not sql_rows:
        detail["reason"] = "no_sql_rows"
        return detail
    if not contract.row_key and len(sql_rows) > 1:
        # 没有行键就无法把多行基准对到回答的某一行，交给 LLM
        detail["reason"] = "no_row_key"
        return detail
    tables = parse_tables(answer_text)
    if not tables:
        detail["reason"] = "no_table"
        return detail
    measures = contract.measures or {}
    picked = _pick_table(tables, measures, contract)
    if picked is None:
        detail["reason"] = "no_measure_columns"
        return detail

    _score, table, columns, key_columns = picked
    ordered = sorted(columns.items())
    detail["available"] = True
    detail["answer_rows"] = len(table["rows"])
    detail["columns"] = [measure_key for _index, (measure_key, _scale) in ordered]
    detail["labels"] = {key: (measures[key].label or key) for key in detail["columns"]}
    # 基准表要展示契约里的全部指标，而不是只展示能从回答表里对上的那几列
    detail["display_columns"] = list(measures)

    default_year = _default_year(requested_params or {})
    answer_rows: Dict[str, List[str]] = {}
    for row in table["rows"]:
        key = _row_key_value(row, key_columns, contract, default_year) if contract.row_key else ""
        if key and key not in answer_rows:
            answer_rows[key] = row

    sql_by_key: Dict[str, Dict[str, Any]] = {}
    for row in sql_rows:
        if contract.row_key:
            key = "|".join(_key_of(row.get(dim), dim, default_year) for dim in contract.row_key)
        else:
            key = ""
        sql_by_key.setdefault(key, row)

    rows: List[Dict[str, Any]] = []
    matched = checked = hits = 0
    wrong_values: List[Dict[str, Any]] = []
    missing_values: List[Dict[str, Any]] = []
    for key, sql_row in sql_by_key.items():
        entry = {"key": key, "status": "hit", "answer": {}, "answer_norm": {}, "sql": {}, "cells": {}}
        answer_row = answer_rows.get(key)
        for measure_key, spec in measures.items():
            column = spec.sql_column or measure_key
            value = sql_row.get(column)
            if value is None:
                value = sql_row.get(measure_key)
            entry["sql"][measure_key] = value
        for index, (measure_key, scale) in ordered:
            spec = measures[measure_key]
            column = spec.sql_column or measure_key
            sql_value = sql_row.get(column)
            if sql_value is None:
                sql_value = sql_row.get(measure_key)
            entry["sql"][measure_key] = sql_value
            if not spec.required and is_empty(sql_value):
                entry["cells"][measure_key] = "na"
                continue
            if answer_row is None:
                continue
            cell = answer_row[index] if index < len(answer_row) else ""
            status, agent_value, sql_norm = _compare_value(cell, sql_value, spec, scale)
            entry["answer"][measure_key] = cell
            entry["answer_norm"][measure_key] = agent_value
            if status == "empty":
                entry["cells"][measure_key] = "empty"
                missing_values.append({"key": key, "field": measure_key})
                continue
            checked += 1
            if status == "hit":
                hits += 1
                entry["cells"][measure_key] = "hit"
            else:
                entry["cells"][measure_key] = "miss"
                wrong_values.append(
                    {"key": key, "field": measure_key, "agent": agent_value, "sql": sql_norm, "evidence": cell}
                )
        if answer_row is None:
            entry["status"] = "missing"
            detail["missing_rows"].append({"key": key})
        else:
            matched += 1
        rows.append(entry)

    for key, answer_row in answer_rows.items():
        if key in sql_by_key:
            continue
        if contract.row_key and _norm(key) in AGGREGATE_KEYS:
            continue
        entry = {"key": key, "status": "extra", "answer": {}, "sql": {}, "cells": {}}
        for index, (measure_key, _scale) in ordered:
            entry["answer"][measure_key] = answer_row[index] if index < len(answer_row) else ""
        detail["extra_rows"].append({"key": key})
        rows.append(entry)

    detail["rows"] = rows
    detail["wrong_values"] = wrong_values
    detail["missing_values"] = missing_values
    detail["matched_rows"] = matched
    detail["checked_values"] = checked
    detail["hit_values"] = hits
    detail["row_hit_rate"] = round(matched / len(sql_by_key), 4) if sql_by_key else None
    detail["value_hit_rate"] = round(hits / checked, 4) if checked else None
    return detail


RECONCILE_KEYS = (
    "available", "reason", "row_key", "sql_rows", "answer_rows", "matched_rows",
    "missing_rows", "extra_rows", "checked_values", "hit_values", "wrong_values",
    "missing_values", "row_hit_rate", "value_hit_rate",
)


def reconcile(
    contract: CaseContract,
    answer_text: str,
    sql_rows: Sequence[Dict[str, Any]],
    requested_params: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """把回答表格与 SQL 基准逐行比对。不可比时 available=False。"""
    return {key: value for key, value in align_rows(contract, answer_text, sql_rows, requested_params).items() if key in RECONCILE_KEYS}


def verdict_from_report(report: Dict[str, Any], *, partial_floor: float = 0.5) -> Optional[str]:
    """按命中率给出结论；不可比时返回 None，交给 LLM 判。"""
    if not report.get("available"):
        return None
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
