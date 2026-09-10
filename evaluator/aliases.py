# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import re
from functools import lru_cache
from typing import Dict, List

from evaluator.paths import CONFIG_DIR


def _norm(text: str) -> str:
    return re.sub(r"\s+", "", str(text).strip()).replace("（", "(").replace("）", ")")


@lru_cache(maxsize=1)
def organization_aliases() -> Dict[str, str]:
    path = CONFIG_DIR / "organization_aliases.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    return { _norm(k): v for k, v in data.items() }


@lru_cache(maxsize=1)
def metric_aliases() -> Dict[str, str]:
    path = CONFIG_DIR / "metric_aliases.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    out: Dict[str, str] = {}
    for canonical, names in data.items():
        out[_norm(canonical)] = canonical
        for name in names:
            out[_norm(name)] = canonical
            out[_norm(re.sub(r"[（(][^）)]*[）)]$", "", name))] = canonical
    return out


@lru_cache(maxsize=1)
def organization_hierarchy() -> Dict[str, object]:
    """组织上下级关系，仅用于"疑似上下级合并"诊断（comparison_policy: independent）。

    比较本身不做上下级汇总合并：上级与下级仍按独立行计分。
    缺失或非法配置时返回空映射，诊断自动跳过。
    """
    path = CONFIG_DIR / "organization_hierarchy.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    parents = data.get("parents") if isinstance(data, dict) else None
    if not isinstance(parents, dict):
        return {}
    return {"comparison_policy": data.get("comparison_policy", "independent"), "parents": parents}


def normalize_org(value: str) -> str:
    text = str(value or "").replace("**", "").replace("`", "").strip()
    text = re.sub(r"^[^\w\u4e00-\u9fff]+|[^\w\u4e00-\u9fff]+$", "", text).strip()
    return organization_aliases().get(_norm(text), text)


ORG_DIMS = {"organization", "组织", "单位", "分公司", "部门", "二级部门", "水司", "org", "zone"}
PERIOD_DIMS = {"period", "月份", "月份(期数)", "期间", "ym", "业务月", "统计月", "年月"}
DATE_DIMS = {"date", "日期"}
DEFAULT_DIM_ALIASES = {
    "组织": ["组织", "单位", "分公司", "部门", "二级部门", "水司", "organization", "org"],
    "organization": ["organization", "组织", "单位", "分公司", "部门", "二级部门"],
    "月份(期数)": ["月份(期数)", "月份", "期间", "年月", "period", "ym", "业务月", "统计月"],
    "月份": ["月份", "月份(期数)", "期间", "period", "ym"],
    "period": ["period", "月份", "月份(期数)", "期间", "ym"],
    "DMA编码": ["DMA编码", "DMA", "dma", "dmaid", "小区编码"],
    "设施编码": ["设施编码", "设施"],
    "区间": ["区间", "分档", "漏损率区间"],
    "工单编号": ["工单编号", "工单号", "工单"],
    "管径分段": ["管径分段", "管径", "分段"],
    "管道编码": ["管道编码", "管道"],
}


def is_org_dim(dim: str) -> bool:
    return str(dim or "") in ORG_DIMS


def is_period_dim(dim: str) -> bool:
    return str(dim or "") in PERIOD_DIMS


def is_date_dim(dim: str) -> bool:
    return str(dim or "") in DATE_DIMS


def dimension_aliases(dim: str) -> List[str]:
    names = list(DEFAULT_DIM_ALIASES.get(dim, [dim]))
    if dim not in names:
        names.append(dim)
    return names


def resolved_dimension_columns(contract) -> Dict[str, List[str]]:
    declared = dict(getattr(contract, "dimension_columns", None) or {})
    out: Dict[str, List[str]] = {}
    for dim in list(getattr(contract, "row_key", None) or []):
        extras = list(declared.get(dim) or [])
        out[dim] = list(dict.fromkeys([*extras, *dimension_aliases(dim), dim]))
    for dim, aliases in declared.items():
        out.setdefault(dim, list(dict.fromkeys([*(aliases or []), dim])))
    return out


def canonical_metric(label: str, measures: Dict[str, object] | None = None) -> str | None:
    raw = str(label or "").replace("**", "").replace("`", "").strip()
    raw = re.sub(r"^[^\w\u4e00-\u9fff]+", "", raw).strip()
    if not raw:
        return None
    stripped = re.sub(r"[（(][^）)]*[）)]$", "", raw).strip()
    if measures:
        for key, spec in measures.items():
            aliases = [key, getattr(spec, "label", "")] + list(getattr(spec, "aliases", []) or [])
            aliases += [re.sub(r"[（(][^）)]*[）)]$", "", a) for a in aliases]
            normalized = {_norm(a) for a in aliases if a}
            if _norm(raw) in normalized or _norm(stripped) in normalized:
                return key
    mapped = metric_aliases().get(_norm(raw)) or metric_aliases().get(_norm(stripped))
    if mapped and measures and mapped not in measures:
        for key in measures:
            if _norm(key) == _norm(mapped) or mapped in getattr(measures[key], "aliases", []):
                return key
        return None
    return mapped
