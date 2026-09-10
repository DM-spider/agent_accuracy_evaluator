# -*- coding: utf-8 -*-
"""表级数据水位：执行前后比较，变化则该题不可评分。"""
from __future__ import annotations

import re
from datetime import datetime, timezone, timedelta
from typing import Dict, Iterable, List, Optional

from evaluator.models import WatermarkSnapshot
from evaluator.sql_executor import SqlExecutor

TABLE_RE = re.compile(r"\b(?:FROM|JOIN)\s+([A-Za-z_][\w.]*)", re.I)
IDENT_RE = re.compile(r"^[A-Za-z_][\w.]*$")


def tables_in_sql(sql: str) -> List[str]:
    names = []
    for match in TABLE_RE.finditer(sql or ""):
        name = match.group(1).strip()
        if IDENT_RE.match(name) and name.lower() not in {"select", "lateral"}:
            names.append(name)
    return list(dict.fromkeys(names))


def capture_watermark(
    executor: Optional[SqlExecutor],
    tables: Iterable[str],
    timestamp_columns: Optional[Iterable[str]] = None,
) -> WatermarkSnapshot:
    stamp = datetime.now(timezone(timedelta(hours=8))).isoformat()
    columns = list(timestamp_columns or ["updated_at", "sjsj", "etl_time", "load_time", "modify_time"])
    tables_map: Dict[str, Optional[str]] = {}
    if executor is None or executor.connect is None:
        return WatermarkSnapshot(tables={t: None for t in tables}, captured_at=stamp)
    for table in tables:
        if not IDENT_RE.match(table):
            tables_map[table] = None
            continue
        value = None
        for col in columns:
            if not IDENT_RE.match(col):
                continue
            snapshot = executor.query(f"SELECT MAX({col}) AS watermark FROM {table}")
            if snapshot.error:
                continue
            if snapshot.rows:
                value = snapshot.rows[0].get("watermark")
                if value is not None:
                    value = str(value)
                    break
        tables_map[table] = value
    return WatermarkSnapshot(tables=tables_map, captured_at=stamp)


def watermark_changed(before: Optional[WatermarkSnapshot], after: Optional[WatermarkSnapshot]) -> bool:
    if before is None or after is None:
        return False
    for table, value in before.tables.items():
        if table in after.tables and value is not None and after.tables[table] is not None:
            if str(value) != str(after.tables[table]):
                return True
    return False
