# -*- coding: utf-8 -*-
"""标准集结果组装：比率/水量/差距四位小数，明细不截断，列名简体中文。"""
from datetime import date, datetime, time
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation

from golden_builder.fragments import chinese_col

_Q4 = Decimal("0.0001")


def _round4(value):
    try:
        return float(Decimal(str(value)).quantize(_Q4, rounding=ROUND_HALF_UP))
    except (InvalidOperation, ValueError, TypeError):
        return value


def _as_period(value):
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    if 200001 <= number <= 209912 and 1 <= number % 100 <= 12 and float(value) == number:
        return number
    return None


def _cell(value, scale):
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            value = value.replace(tzinfo=None)
        return value.isoformat(sep=" ", timespec="seconds")
    if isinstance(value, date) and not isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, time):
        return value.replace(tzinfo=None).isoformat() if value.tzinfo else value.isoformat()
    period = _as_period(value)
    if period is not None and scale in {"count", "text", "volume", None}:
        return period
    if isinstance(value, Decimal):
        value = float(value)
    if scale in {"percent", "pp", "ratio01", "volume"}:
        return _round4(value)
    if scale == "count":
        try:
            return int(round(float(value)))
        except (TypeError, ValueError):
            return str(value)
    if isinstance(value, float):
        return _round4(value)
    return value


def _lookup_col(spec, index, measures):
    for name in (spec.get("sql_column"), spec.get("key"), chinese_col(spec.get("sql_column") or "", measures)):
        if name and name in index:
            return name
    return spec.get("sql_column")


def make_stat(cols, rows, measures):
    row = rows[0] if rows else [None] * len(cols)
    index = {c: i for i, c in enumerate(cols)}
    fields = []
    for spec in measures:
        col = _lookup_col(spec, index, measures)
        raw = row[index[col]] if col in index else None
        fields.append({
            "key": spec["key"],
            "label": spec["key"],
            "unit": spec.get("unit", ""),
            "sql_column": spec["key"],
            "value_scale": spec.get("value_scale", "volume"),
            "value": _cell(raw, spec.get("value_scale", "volume")),
            "tolerance": _tolerance(spec.get("value_scale", "volume")),
        })
    return {"fields": fields}


def make_detail(cols, rows, key_fields, measures):
    cols_cn = [chinese_col(c, measures) for c in cols]
    scale_by_sql = {m["sql_column"]: m.get("value_scale", "volume") for m in measures}
    scale_by_key = {m["key"]: m.get("value_scale", "volume") for m in measures}
    dim_names = set(chinese_col(k, measures) for k in key_fields) | set(key_fields)
    detail_rows = []
    for row in rows:
        item = {}
        for i, col in enumerate(cols):
            name = cols_cn[i]
            scale = scale_by_sql.get(col) or scale_by_key.get(name)
            if name in dim_names:
                scale = "count" if _as_period(row[i]) is not None else "text"
            elif scale is None:
                scale = "text" if not isinstance(row[i], (int, float, Decimal)) else "volume"
            item[name] = _cell(row[i], scale)
        detail_rows.append(item)
    return {
        "key_fields": [chinese_col(k, measures) for k in key_fields],
        "value_fields": [{"key": m["key"], "tolerance": _tolerance(m.get("value_scale", "volume"))} for m in measures],
        "rows": detail_rows,
        "total_rows": len(rows),
        "max_print_rows": 20 if len(rows) <= 20 else 10,
    }


def _tolerance(scale):
    # ratio01 存 0-1 小数，容差定义在换算后的业务空间（%/pp），与原 percent/pp 一致
    if scale in {"percent", "pp", "ratio01"}:
        return {"kind": "abs", "eps": 0.01, "abs_floor": 0.0}
    if scale == "count":
        return {"kind": "exact", "eps": 0.0, "abs_floor": 0.0}
    return {"kind": "rel", "eps": 0.001, "abs_floor": 0.01}
