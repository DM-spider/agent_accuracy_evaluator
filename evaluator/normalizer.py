# -*- coding: utf-8 -*-
"""单位与数值归一。不做无契约的单位猜测。"""
from __future__ import annotations

import re
from typing import Optional, Tuple

from evaluator.models import MeasureSpec

EMPTY_TOKENS = {"", "--", "—", "–", "暂无", "无", "n/a", "na", "null", "none", "-", "nan"}
# 数据库可能以科学计数法返回极小值（实测出现过 4.72e-05），数字需整体解析
NUMBER_RE = re.compile(r"[-+]?\d[\d,]*(?:\.\d+)?(?:[eE][-+]?\d+)?")
VALUE_TAIL = r"[-+]?\d[\d,]*(?:\.\d+)?(?:[eE][-+]?\d+)?(?:\s*(?:亿|万))?(?:\s*(?:立方米|立方|方|m³|m3|%|‰|pp|个百分点))?"
RANK_RE = re.compile(r"^第?\d+名$")
ID_RE = re.compile(r"^(WS|GD|DMA)?\d{6,}[A-Za-z0-9]*$", re.I)
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$|^\d{8}$|^\d{4}/\d{2}/\d{2}$")


def is_empty(raw: object) -> bool:
    if raw is None:
        return True
    return str(raw).strip().lower() in EMPTY_TOKENS


def is_non_metric_entity(raw: object) -> bool:
    text = str(raw or "").strip()
    if not text:
        return False
    if DATE_RE.match(text):
        return True
    if RANK_RE.match(text):
        return True
    if ID_RE.match(text.replace("-", "")):
        return True
    return False


def _sign_from_text(text: str) -> float:
    if re.search(r"(下降|减少|降低|下跌|负|低于|低出)", text):
        return -1.0
    if re.search(r"(上升|增加|增长|提高|高于|高出)", text):
        return 1.0
    return 1.0


def conversion_meta(raw: object, spec: Optional[MeasureSpec] = None) -> Tuple[str, Optional[float]]:
    text = str(raw or "")
    scale = spec.value_scale if spec else ""
    unit = spec.unit if spec else ""
    if "亿" in text and (scale == "volume" or re.search(r"立方米|立方|方|m³|m3", text)):
        return "亿", 100000000.0
    if "万" in text and (scale == "volume" or unit in {"m³", "m3"} or re.search(r"立方米|立方|方|m³|m3", text)):
        return "万", 10000.0
    return "", None


def parse_number(
    raw: object,
    spec: Optional[MeasureSpec] = None,
    source: str = "agent",
) -> Tuple[Optional[float], str, Optional[str]]:
    """返回 (value, unit, error)。空值是合法的 None，不转 0。

    source="sql" 表示原始值来自契约 SQL 查询结果（黄金集/复检同源），
    ratio01 契约下按声明语义确定性 ×100；智能体文本则结合单位词与量级判断。
    """
    if is_empty(raw):
        return None, (spec.unit if spec else ""), None
    text = str(raw).strip().replace("％", "%")
    if is_non_metric_entity(text) and not re.fullmatch(r"[-+]?\d+(?:\.\d+)?", text) and spec and spec.value_scale != "text":
        return None, spec.unit, "non_metric_entity"
    if re.search(r"同比[^。]{0,8}\d", text) and "百分点" not in text and "%" in text and spec and spec.unit == "pp":
        return None, "pp", "unparseable_yoy"
    m = NUMBER_RE.search(text.replace("，", ","))
    if not m:
        return None, (spec.unit if spec else ""), "unparseable"
    core = m.group(0).replace(",", "")
    try:
        value = float(core)
    except ValueError:
        return None, (spec.unit if spec else ""), "unparseable"
    unit = spec.unit if spec else ""
    scale = spec.value_scale if spec else "volume"
    token, factor = conversion_meta(text, spec)
    # 文本自带单位词（%/‰/pp/个百分点）说明值已是业务读数，ratio01 不再放大
    unit_word = bool(re.search(r"[%％‰]", text) or "pp" in text.lower() or "个百分点" in text)
    if factor:
        value *= factor
        unit = "m³"
    elif "‰" in text:
        value *= 0.1
        unit = "%"
    elif "个百分点" in text or (spec and spec.unit == "pp") or "pp" in text.lower():
        unit = "pp"
        if value >= 0:
            value *= _sign_from_text(text)
    elif "%" in text:
        unit = "%"
    elif "m³" in text or "立方米" in text or text.endswith("方"):
        unit = "m³"

    if scale == "ratio01":
        # 契约声明存储形态为 0-1 比值（SQL 原始值，生成端禁止 *100）：
        # 换算到业务单位是评测端职责。SQL 侧无条件 ×100（完成率等可 >1，不能用量级猜测）；
        # 智能体文本已带单位词时不放大；裸数沿用 |v|≤1 量级启发式（0.125→12.5%，12.5 视为已是百分数）。
        if source == "sql" or (abs(value) <= 1 and not unit_word):
            value *= 100
        unit = (spec.unit if spec and spec.unit in {"%", "pp"} else "%")
    elif scale == "percent":
        unit = unit or "%"
    elif scale == "count":
        if not float(value).is_integer():
            # 个数必须能落到整数
            if abs(value - round(value)) > 1e-9:
                return value, "count", None
        value = float(round(value))
        unit = "count"
    if spec and spec.unit and unit and spec.unit not in {unit, "%", "pp"} and {spec.unit, unit} != {"m³", "m3"}:
        if spec.unit == "%" and unit == "pp":
            return value, unit, "unit_conflict"
    return value, unit or (spec.unit if spec else ""), None


def value_scale_rule(spec: Optional[MeasureSpec]) -> str:
    if spec is None:
        return "按原数值比较"
    if spec.value_scale == "ratio01":
        # ratio01 是存储形态声明：×100 后落到契约业务单位（pp 差值即百分点）
        target = "百分点（pp）" if spec.unit == "pp" else "百分数（%）"
        return f"0-1比例×100后统一为{target}"
    return {
        "percent": "统一为百分数（%）",
        "pp": "统一为百分点（pp），保留升降方向",
        "volume": "亿方/万方/立方米统一为m³",
        "count": "统一为整数计数",
        "text": "按文本原值比较",
    }.get(spec.value_scale, "按契约单位比较")


def tolerance_rule(spec: Optional[MeasureSpec]) -> str:
    if spec is None:
        return "完全一致"
    if spec.tolerance.kind == "exact" or spec.value_scale == "count":
        return "完全一致"
    if spec.tolerance.kind == "rel":
        floor = f"，绝对下限{spec.tolerance.abs_floor:g}" if spec.tolerance.abs_floor else ""
        return f"相对误差≤{spec.tolerance.eps:g}{floor}"
    return f"绝对差≤{spec.tolerance.eps:g}"


def values_close(expected: Optional[float], actual: Optional[float], spec: Optional[MeasureSpec]) -> Tuple[bool, Optional[float], str]:
    if expected is None or actual is None:
        return expected is None and actual is None, None, "exact"
    delta = actual - expected
    if spec is None:
        ok = abs(delta) <= 1e-9
        return ok, delta, "exact"
    kind = spec.tolerance.kind
    eps = spec.tolerance.eps
    if kind == "exact" or spec.value_scale == "count":
        return abs(delta) <= 1e-9, delta, "exact"
    if abs(delta) <= 1e-9:
        return True, delta, "exact"
    if kind == "rel":
        floor = spec.tolerance.abs_floor or 0.0
        if abs(expected) <= 1e-12:
            ok = abs(delta) <= max(floor, 1e-6)
        else:
            ok = abs(delta) / abs(expected) <= eps or abs(delta) <= floor
        return ok, delta, "rel"
    ok = abs(delta) <= eps
    return ok, delta, "abs"
