# -*- coding: utf-8 -*-
"""把 catalog + builder 编成 sql_template 与契约字典。

外层统一：度量列 ROUND(..., 4) + 简体中文别名；期内层英文别名保持可组合。
比率仍是小数比值，禁止 *100。
"""
import re

from golden_builder.builders import BUILDERS
from golden_builder.catalog import CASES
from golden_builder.fragments import COLUMN_CN, FUZZY_CN, chinese_col


DIM_ALIASES = {
    "组织": ["组织", "单位", "分公司", "部门", "二级部门", "水司", "organization"],
    "月份(期数)": ["月份(期数)", "月份", "期间", "年月", "period", "ym", "业务月"],
    "DMA编码": ["DMA编码", "DMA", "dma", "dmaid", "小区编码"],
    "设施编码": ["设施编码", "设施"],
    "区间": ["区间", "分档", "漏损率区间"],
    "工单编号": ["工单编号", "工单号", "工单"],
    "管径分段": ["管径分段", "管径", "分段"],
    "三级部门": ["三级部门", "水务所", "运营中心", "level3_name"],
    "管道编码": ["管道编码", "管道"],
}

PERIOD_NAMES = {
    "月份(期数)", "最高月份", "最低月份", "本期月份", "上期月份", "同期月份",
    "ym", "period", "period_prev", "period_yoy", "max_ym", "min_ym",
}
CONTEXT_CN = {
    "组织", "月份(期数)", "本期月份", "上期月份", "同期月份",
    "DMA编码", "DMA名称", "工单编号", "标题", "创建时间",
    "管道编码", "设施编码", "区间", "管径分段", "地址", "三级部门",
}
COUNT_NAMES = {
    "cnt", "total_cnt", "invalid_cnt", "prev_cnt", "fin_cnt", "handled_cnt", "open_cnt",
    "yoy_cnt", "sample_cnt", "met", "left_repair", "left_detect", "right_repair", "right_detect",
    "数量", "总数", "无效数", "上月数量", "完成数", "已处置数", "未处置数", "同期工单数",
    "样本数", "是否达标", "南山维修单数", "南山检漏单数", "宝安维修单数", "宝安检漏单数",
}
TEXT_NAMES = {
    "org", "dwmc", "bz_bm", "组织", "dmaid", "dmaname", "id", "title", "create_time",
    "pipe_number", "pipe_code", "facility_code", "valve_number", "fire_hydrant_number",
    "DMA编码", "DMA名称", "工单编号", "标题", "创建时间", "管道编码", "设施编码", "区间", "管径分段",
    "地址", "三级部门",
}
ROUND_SCALES = {"percent", "pp", "volume", "ratio01"}
_AS_TAIL = re.compile(r'(?is)\s+as\s+("(?:[^"]|"")+"|[A-Za-z_][\w$]*)\s*$')
_IDENT_TAIL = re.compile(r'(?is)("(?:[^"]|"")+"|[A-Za-z_][\w$]*)\s*$')
_BARE_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
# 已显式指定类型的参数（CAST(:p AS int)），从裸参数检查中剔除；::x 双冒号强转天然带类型
_CAST_TYPED_PARAM = re.compile(r"(?i)CAST\(\s*:[a-z_][a-z_0-9]*\s+AS\s+[a-z0-9_]+(?:\s*\([^)]*\))?\s*\)")
# SELECT 列表中裸参数后紧跟别名（:p AS "列"）：pg8000 服务端 $1 绑定无类型上下文会推导为 text，
# 与 WHERE 中 numeric 列的推导冲突（42P08 inconsistent types）；psycopg2 客户端字面量替换会掩盖该问题
_BARE_SELECT_PARAM = re.compile(r"(?<![:\w]):[a-z_][a-z_0-9]*\s+AS\b")


def ensure_pg8000_safe(sql, case_id=""):
    """SELECT 列表中的命名参数必须显式 CAST（如 CAST(:period AS int)），编译期拦截 42P08 类错误。"""
    stripped = _CAST_TYPED_PARAM.sub(" ", sql or "")
    match = _BARE_SELECT_PARAM.search(stripped)
    if match:
        raise ValueError(
            "%s: SELECT 列表存在裸命名参数（%r），需显式 CAST(... AS int/date) 以兼容评测端服务端参数绑定"
            % (case_id, match.group(0))
        )


def _tolerance(scale):
    # ratio01 存 0-1 小数，容差定义在换算后的业务空间（%/pp），与原 percent/pp 一致
    if scale in {"percent", "pp", "ratio01"}:
        return {"kind": "abs", "eps": 0.01, "abs_floor": 0.0}
    if scale == "count":
        return {"kind": "exact", "eps": 0.0, "abs_floor": 0.0}
    if scale == "text":
        return {"kind": "exact", "eps": 0.0, "abs_floor": 0.0}
    return {"kind": "rel", "eps": 0.001, "abs_floor": 0.01}


def _unquote(token):
    token = (token or "").strip()
    if len(token) >= 2 and token[0] == '"' and token[-1] == '"':
        return token[1:-1].replace('""', '"')
    return token


def _quote_ident(name):
    return '"%s"' % str(name).replace('"', '""')


def _src_ref(alias):
    if _BARE_IDENT.match(alias):
        return "_g." + alias
    return "_g." + _quote_ident(alias)


def _split_outer_select(sql):
    text = (sql or "").strip()
    if text.endswith(";"):
        text = text[:-1].rstrip()
    match = re.match(r"(?is)\s*select\b", text)
    if not match:
        raise ValueError("not a SELECT")
    i = match.end()
    n = len(text)
    depth = 0
    in_single = in_double = False
    items = []
    start = i
    while i < n:
        ch = text[i]
        if in_single:
            if ch == "'" and i + 1 < n and text[i + 1] == "'":
                i += 2
                continue
            if ch == "'":
                in_single = False
            i += 1
            continue
        if in_double:
            if ch == '"':
                in_double = False
            i += 1
            continue
        if ch == "'":
            in_single = True
            i += 1
            continue
        if ch == '"':
            in_double = True
            i += 1
            continue
        if ch == "(":
            depth += 1
            i += 1
            continue
        if ch == ")":
            depth -= 1
            i += 1
            continue
        if depth == 0:
            if ch == ",":
                items.append(text[start:i].strip())
                start = i + 1
                i += 1
                continue
            if text[i:i + 4].lower() == "from" and (i + 4 == n or not (text[i + 4].isalnum() or text[i + 4] == "_")):
                prev = text[i - 1] if i else " "
                if not (prev.isalnum() or prev == "_"):
                    items.append(text[start:i].strip())
                    return [item for item in items if item], text
        i += 1
    items.append(text[start:].strip())
    return [item for item in items if item], text


def _alias_of(item):
    match = _AS_TAIL.search(item)
    if match:
        return item[:match.start()].strip(), _unquote(match.group(1))
    match = _IDENT_TAIL.search(item.strip())
    if match:
        return item.strip(), _unquote(match.group(1))
    return item.strip(), item.strip()


def _kind_for(alias, spec):
    for item in spec.get("measures") or []:
        if item.get("sql_column") == alias or item.get("key") == alias:
            if alias in PERIOD_NAMES or item.get("key") in PERIOD_NAMES:
                return "period"
            return item.get("value_scale") or "volume"
    if alias in PERIOD_NAMES or COLUMN_CN.get(alias) in PERIOD_NAMES:
        return "period"
    if alias in COUNT_NAMES or COLUMN_CN.get(alias) in COUNT_NAMES:
        return "count"
    if alias in TEXT_NAMES or COLUMN_CN.get(alias) in TEXT_NAMES or alias in DIM_ALIASES:
        return "text"
    if alias in COLUMN_CN:
        return "volume"
    if any("\u4e00" <= ch <= "\u9fff" for ch in alias):
        return "text"
    return "volume"


def _keyword_at(sql, i, word):
    n = len(word)
    if sql[i:i + n].lower() != word:
        return False
    prev = sql[i - 1] if i else " "
    nxt = sql[i + n] if i + n < len(sql) else " "
    return not (prev.isalnum() or prev == "_") and not (nxt.isalnum() or nxt == "_")


def _outer_order_by(sql):
    text = sql or ""
    depth = 0
    in_single = in_double = False
    i = 0
    n = len(text)
    start = None
    while i < n:
        ch = text[i]
        if in_single:
            if ch == "'" and i + 1 < n and text[i + 1] == "'":
                i += 2
                continue
            if ch == "'":
                in_single = False
            i += 1
            continue
        if in_double:
            if ch == '"':
                in_double = False
            i += 1
            continue
        if ch == "'":
            in_single = True
            i += 1
            continue
        if ch == '"':
            in_double = True
            i += 1
            continue
        if ch == "(":
            depth += 1
            i += 1
            continue
        if ch == ")":
            depth -= 1
            i += 1
            continue
        if depth == 0 and start is None and re.match(r"(?is)order\s+by\b", text[i:]):
            start = i
        if depth == 0 and start is not None and (_keyword_at(text, i, "limit") or _keyword_at(text, i, "offset")):
            return text[start:i].strip()
        i += 1
    if start is not None:
        return text[start:].strip()
    return ""


def _split_order_items(clause):
    body = re.sub(r"(?is)^order\s+by\s+", "", clause).strip()
    items = []
    depth = 0
    in_single = in_double = False
    start = 0
    i = 0
    n = len(body)
    while i < n:
        ch = body[i]
        if in_single:
            if ch == "'" and i + 1 < n and body[i + 1] == "'":
                i += 2
                continue
            if ch == "'":
                in_single = False
            i += 1
            continue
        if in_double:
            if ch == '"':
                in_double = False
            i += 1
            continue
        if ch == "'":
            in_single = True
        elif ch == '"':
            in_double = True
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == "," and depth == 0:
            items.append(body[start:i].strip())
            start = i + 1
        i += 1
    items.append(body[start:].strip())
    return [item for item in items if item]


def _rewrite_order_by(sql, alias_map):
    clause = _outer_order_by(sql)
    if not clause:
        return ""
    kept = []
    for item in _split_order_items(clause):
        token_match = re.match(r'(?is)\s*("(?:[^"]|"")+"|[A-Za-z_][\w$]*|\d+)', item)
        if not token_match:
            continue
        token = _unquote(token_match.group(1))
        suffix = item[token_match.end():]
        if token.isdigit():
            kept.append(token + suffix)
        elif token in alias_map:
            kept.append(_quote_ident(alias_map[token]) + suffix)
    if not kept:
        return ""
    return "\nORDER BY " + ", ".join(kept)


def _keep_column(alias, name, spec):
    measures = spec.get("measures") or []
    sql_cols = {item.get("sql_column") for item in measures}
    keys = {item.get("key") for item in measures}
    if alias in sql_cols or alias in keys or name in keys:
        return True
    if name in CONTEXT_CN or alias in CONTEXT_CN:
        return True
    if alias in PERIOD_NAMES or name in PERIOD_NAMES:
        return True
    if alias in TEXT_NAMES or name in TEXT_NAMES or alias in DIM_ALIASES:
        return True
    if COLUMN_CN.get(alias) in CONTEXT_CN:
        return True
    return False


def finalize_select(sql, spec):
    """外层投影：只保留契约指标和上下文字段；中文列名；比率/水量 ROUND 4 位。"""
    items, _original = _split_outer_select(sql)
    if not items:
        return sql
    selects = []
    alias_map = {}
    case_id = spec.get("case_id") or ""
    for item in items:
        _expr, alias = _alias_of(item)
        name = chinese_col(alias, spec.get("measures") or [])
        if not _keep_column(alias, name, spec):
            continue
        if name in FUZZY_CN:
            raise ValueError("%s: 模糊列名 %r 未绑定业务指标" % (case_id, name))
        alias_map[alias] = name
        kind = _kind_for(alias, spec)
        src = _src_ref(alias)
        quoted = _quote_ident(name)
        if kind == "period":
            selects.append("CAST(%s AS int) AS %s" % (src, quoted))
        elif kind == "count":
            selects.append("CAST(%s AS bigint) AS %s" % (src, quoted))
        elif kind in ROUND_SCALES:
            selects.append("ROUND((%s)::numeric, 4) AS %s" % (src, quoted))
        else:
            selects.append("%s AS %s" % (src, quoted))
    if not selects:
        raise ValueError("%s: 外层投影为空" % case_id)
    order = _rewrite_order_by(sql, alias_map)
    compiled = "SELECT %s\nFROM (\n%s\n) _g%s" % (", ".join(selects), sql.strip(), order)
    ensure_pg8000_safe(compiled, case_id)
    return compiled


def compile_one(spec):
    fn = BUILDERS.get(spec["builder"])
    if fn is None:
        raise KeyError("unknown builder %s for %s" % (spec["builder"], spec["case_id"]))
    sql = finalize_select(fn(spec).strip(), spec)
    measures = {}
    for item in spec["measures"]:
        aliases = []
        for alias in [item["key"], item.get("sql_column") or "", *(item.get("aliases") or [])]:
            if alias and alias not in aliases:
                aliases.append(alias)
        measures[item["key"]] = {
            "label": item["key"],
            "aliases": aliases,
            "unit": item.get("unit", ""),
            "sql_column": item["key"],
            "value_scale": item.get("value_scale", "volume"),
            "required": item.get("required", True),
            "tolerance": _tolerance(item.get("value_scale", "volume")),
            "aggregation": item.get("aggregation", ""),
            "period_role": item.get("period_role", ""),
        }
    contract = {
        "case_id": spec["case_id"],
        "question": spec["question"],
        "result_type": spec["result_type"],
        "numeric_evaluable": spec.get("numeric_evaluable", True),
        "realtime_ready": True,
        "sql_template": sql,
        "sql_source": "",
        "parameter_resolver": spec["parameter_resolver"],
        "dimensions": list(spec.get("row_key") or []),
        "row_key": list(spec.get("row_key") or []),
        "dimension_columns": {dim: list(DIM_ALIASES.get(dim, [dim])) for dim in (spec.get("row_key") or [])},
        "measures": measures,
        "coverage_policy": {
            "mode": "all_rows" if spec["result_type"] == "detail" else "all_fields",
            "minimum": 1.0,
            "n": None,
            "score_order": False,
        },
        "scene_big": spec["scene_big"],
        "category": spec["category"],
        "indicator_type": spec["indicator_type"],
        "caliber": spec["caliber"],
        "notes": spec.get("caliber", ""),
        "roles": spec.get("roles") or [],
        "review_required": [],
        "not_scored_reason": None,
    }
    return spec, sql, contract


def compile_all():
    return [compile_one(spec) for spec in CASES]
