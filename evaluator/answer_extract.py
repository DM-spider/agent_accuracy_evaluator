# -*- coding: utf-8 -*-
"""从自由文本回答中抽取结构化数值块。

平台回答是自由文本：可能是 Markdown 表格、无边框竖线表、HTML 表、列表项，
也可能是「名称：数值」的键值行。只认 Markdown 表格会让大量正确回答落到
「无法比对」再交给 LLM，因此这里按可靠性分级抽取，由上层按契约指标打分挑选。

抽取器不判断对错，只负责把文本切成「表」或「条目」，保持原文片段以便溯源。
"""
from __future__ import annotations

import html
import re
from typing import Any, Dict, List, Optional, Sequence

SEPARATOR_RE = re.compile(r"^\s*\|?[\s:\-|]+\|?\s*$")
HTML_TABLE_RE = re.compile(r"<table\b.*?</table>", re.I | re.S)
HTML_ROW_RE = re.compile(r"<tr\b.*?</tr>", re.I | re.S)
HTML_CELL_RE = re.compile(r"<t([dh])\b[^>]*>(.*?)</t[hd]>", re.I | re.S)
TAG_RE = re.compile(r"<[^>]+>")
LIST_ITEM_RE = re.compile(r"^\s*(?:[-*+•·]|\d+[.、)）])\s+(.+?)\s*$")
KEY_VALUE_RE = re.compile(r"^\s*([^|:：]{1,40}?)\s*[:：]\s*(.+?)\s*$")
HEADING_RE = re.compile(r"^\s*#{1,6}\s")
NUMBER_RE = re.compile(r"[-+]?\d[\d,]*(?:\.\d+)?")
# 数值后允许紧跟的换算词与单位词，用于保留「158.37 万 m³」这类完整片段
VALUE_TAIL_RE = re.compile(r"[\d,.]+(?:[eE][-+]?\d+)?\s*(?:亿|万|千|%|％)?\s*(?:立方米|立方|m³|m3|万立方米|个百分点|pp|人|户|个|起|次|单|小时|分钟|分|天|元|万元)?", re.I)


def _clean_cell(text: Any) -> str:
    value = str(text if text is not None else "")
    value = value.replace("**", "").replace("`", "").replace("↑", "").replace("↓", "")
    return value.strip()


def _split_row(line: str) -> List[str]:
    body = line.strip()
    if body.startswith("|"):
        body = body[1:]
    if body.endswith("|"):
        body = body[:-1]
    return [cell.strip() for cell in body.split("|")]


def _context_of(lines: Sequence[str], index: int) -> str:
    """向上找最近的标题或非空行，作为区域线索（用于惩罚过程表）。"""
    for cursor in range(index - 1, max(-1, index - 4), -1):
        text = lines[cursor].strip()
        if not text:
            continue
        if HEADING_RE.match(text) or len(text) <= 40:
            return _clean_cell(text)[:80]
    return ""


def _markdown_tables(lines: Sequence[str]) -> List[Dict[str, Any]]:
    blocks: List[Dict[str, Any]] = []
    index = 0
    while index < len(lines):
        line = lines[index].strip()
        if (
            line.startswith("|")
            and index + 1 < len(lines)
            and SEPARATOR_RE.match(lines[index + 1].strip())
        ):
            header = _split_row(lines[index])
            rows: List[List[str]] = []
            cursor = index + 2
            while cursor < len(lines) and lines[cursor].strip().startswith("|"):
                rows.append(_split_row(lines[cursor]))
                cursor += 1
            if rows:
                blocks.append({
                    "kind": "table", "source": "markdown",
                    "header": header, "rows": rows,
                    "context": _context_of(lines, index), "line": index + 1,
                })
            index = cursor
            continue
        index += 1
    return blocks


def _pipe_tables(lines: Sequence[str]) -> List[Dict[str, Any]]:
    """无首尾竖线的表：`单位 | 数值`，连续两行以上且列数一致。"""
    blocks: List[Dict[str, Any]] = []
    index = 0
    while index < len(lines):
        line = lines[index].strip()
        if line.count("|") >= 1 and not line.startswith("|") and not SEPARATOR_RE.match(line):
            header = _split_row(line)
            width = len(header)
            if width >= 2:
                rows: List[List[str]] = []
                cursor = index + 1
                # 无边框表也可能带一行 --- 分隔线，跳过而不是就此中断
                if cursor < len(lines) and SEPARATOR_RE.match(lines[cursor].strip()):
                    cursor += 1
                while cursor < len(lines):
                    candidate = lines[cursor].strip()
                    if not candidate or candidate.count("|") < 1 or SEPARATOR_RE.match(candidate):
                        break
                    cells = _split_row(candidate)
                    if len(cells) != width:
                        break
                    rows.append(cells)
                    cursor += 1
                if rows:
                    blocks.append({
                        "kind": "table", "source": "pipe",
                        "header": header, "rows": rows,
                        "context": _context_of(lines, index), "line": index + 1,
                    })
                index = cursor
                continue
        index += 1
    return blocks


def _html_tables(text: str) -> List[Dict[str, Any]]:
    blocks: List[Dict[str, Any]] = []
    for table in HTML_TABLE_RE.findall(text or ""):
        rows: List[List[str]] = []
        for row_html in HTML_ROW_RE.findall(table):
            cells = [
                html.unescape(TAG_RE.sub("", cell)).strip()
                for _tag, cell in HTML_CELL_RE.findall(row_html)
            ]
            if cells:
                rows.append(cells)
        if len(rows) >= 2 and len(rows[0]) >= 2:
            blocks.append({
                "kind": "table", "source": "html",
                "header": rows[0], "rows": rows[1:],
                "context": "", "line": 0,
            })
    return blocks


def _measure_patterns(measures: Optional[Dict[str, Sequence[str]]]):
    """把契约指标别名编译成 (key, pattern)，长的优先，避免「漏损量」抢先匹配「维修漏损量」。"""
    pairs = []
    for key, labels in (measures or {}).items():
        for label in {key, *(labels or [])}:
            text = str(label or "").strip()
            if len(text) < 2:
                continue
            pairs.append((key, text))
    pairs.sort(key=lambda item: len(item[1]), reverse=True)
    return [(key, re.compile(re.escape(label) + r"\s*(?:[:：=为是]\s*)?(" + VALUE_TAIL_RE.pattern + r")")) for key, label in pairs]


def _split_item(text: str, patterns, single_measure: Optional[str] = None) -> Optional[Dict[str, str]]:
    """把一行文本拆成 {_name, 指标: 数值片段}。拆不出指标时返回 None。"""
    row: Dict[str, str] = {}
    first_match_at: Optional[int] = None
    for key, pattern in patterns:
        if key in row:
            continue
        match = pattern.search(text)
        if not match:
            continue
        row[key] = match.group(1).strip()
        if first_match_at is None or match.start() < first_match_at:
            first_match_at = match.start()
    if not row and single_measure:
        # 只有一个指标时，行内不必重复指标名：「南山分公司：5,794.95」。
        # 取行内最后一个数值，避免把名称里的年份（2026-06）当成指标值。
        matches = [item for item in VALUE_TAIL_RE.finditer(text) if re.search(r"\d", item.group(0))]
        if matches:
            last = matches[-1]
            name = re.split(r"[:：，,、；;]\s*$", text[:last.start()].strip())[0].strip(" -—*")
            if name:
                return {"_name": name, single_measure: last.group(0).strip()}
    if not row:
        return None
    name = text[:first_match_at] if first_match_at is not None else text
    name = re.split(r"[:：，,、；;]\s*$", name.strip())[0].strip(" -—*")
    row["_name"] = name
    return row


def _listed_items(lines: Sequence[str], patterns, single_measure: Optional[str]) -> List[Dict[str, Any]]:
    """连续两行以上的列表项或键值行，抽成条目块。"""
    blocks: List[Dict[str, Any]] = []
    pending: List[Dict[str, str]] = []
    pending_start = 0
    pending_context = ""

    def flush():
        nonlocal pending, pending_start, pending_context
        if len(pending) >= 2:
            blocks.append({
                "kind": "items",
                "source": "items",
                "header": ["名称"],
                "rows": pending,
                "context": pending_context,
                "line": pending_start + 1,
            })
        pending = []

    for index, raw in enumerate(lines):
        line = raw.strip()
        if not line:
            flush()
            continue
        bullet = LIST_ITEM_RE.match(line)
        text = bullet.group(1) if bullet else None
        if text is None:
            kv = KEY_VALUE_RE.match(line)
            # 键值行只在该行至少含一个数字时才可能是数据行，避免把结论段落吃进来
            if kv and NUMBER_RE.search(kv.group(2)) and len(kv.group(1)) <= 30:
                text = line
        if text is None:
            flush()
            continue
        row = _split_item(text, patterns, single_measure)
        if row is None:
            flush()
            continue
        if not pending:
            pending_start = index
            pending_context = _context_of(lines, index)
        pending.append(row)
    flush()
    return blocks


def extract_blocks(text: str, measures: Optional[Dict[str, Sequence[str]]] = None) -> List[Dict[str, Any]]:
    """抽取全部候选块。measures 为 {指标键: [别名...]}，用于拆分列表与键值行。"""
    content = (text or "").replace("\r\n", "\n")
    lines = content.split("\n")
    blocks: List[Dict[str, Any]] = []
    blocks.extend(_markdown_tables(lines))
    blocks.extend(_pipe_tables(lines))
    blocks.extend(_html_tables(content))
    if measures:
        single_measure = next(iter(measures)) if len(measures) == 1 else None
        blocks.extend(_listed_items(lines, _measure_patterns(measures), single_measure))
    return blocks


def table_blocks(blocks: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [block for block in blocks if block.get("kind") == "table"]
