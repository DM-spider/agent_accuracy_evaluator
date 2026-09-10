"""Explicit CX04 field mapping; conditional checks never become official scores."""
import re
from collections import Counter

from evaluator.aliases import normalize_org
from evaluator.extractor import parse_md_tables
from evaluator.models import MeasureSpec
from evaluator.normalizer import parse_number, values_close


def clean(text):
    return re.sub(r"^[^\w\u4e00-\u9fff]+", "", str(text).replace("**", "").replace("`", "")).strip()


def target_comparison(text, snapshot):
    tables = parse_md_tables(text)
    if not tables:
        return {"items": [], "issues": ["未识别到主表，不能判定回答缺失"]}
    table = tables[0]
    headers = [clean(h) for h in table["header"]]
    org_index = next((i for i, h in enumerate(headers) if h in {"分公司", "单位", "二级部门", "组织"}), None)
    if org_index is None:
        return {"items": [], "issues": ["单位字段未识别，需补充映射"]}
    definitions = [
        ("年度目标", r"^(?:目标值|目标|年度目标)$", "目标产销差率(%)", "year"),
        ("单月产销差率", r"^(?:本月实际|单月实际)", "实际产销差率(%)", "month"),
        ("累计产销差率", r"^(?:年累计|累计实际)", "实际产销差率(%)", "ytd"),
        ("月差距", r"^月差距$", "差距(pp)", "month_gap"),
        ("累计差距", r"^累计差距$", "差距(pp)", "ytd_gap"),
    ]
    fields = []
    for label, pattern, column, kind in definitions:
        indexes = [i for i, h in enumerate(headers) if re.search(pattern, h)]
        fields.append((label, indexes, column, kind))
    months = {}
    for _, indexes, _, kind in fields:
        if len(indexes) == 1:
            found = re.findall(r"(?<!\d)(1[0-2]|0?[1-9])\s*月", headers[indexes[0]])
            months[kind] = int(found[0]) if len(set(found)) == 1 else None
    period = str(snapshot.get("params", {}).get("period", ""))
    valid_period = bool(re.fullmatch(r"20\d{2}(0[1-9]|1[0-2])", period))
    sql_year, sql_month = (int(period[:4]), int(period[4:])) if valid_period else (None, None)
    template = snapshot.get("sql_template", "")
    sql_kind = "ytd" if "'SzwgBusinessYear'" in template else "month" if "'SzwgBusiness'" in template else None
    # Only a year in the introduction is shared by all columns; avoid borrowing comparison years.
    intro = text.split("|", 1)[0]
    intro_years = set(re.findall(r"(20\d{2})\s*年", intro))
    sql_rows = snapshot.get("rows", [])
    sql_orgs = [normalize_org(clean(r.get("二级部门", ""))) for r in sql_rows]
    sql_counts = Counter(sql_orgs)
    indexed = dict(zip(sql_orgs, sql_rows))
    agent_orgs = [normalize_org(clean(r[org_index])) for r in table["rows"] if len(r) > org_index]
    agent_counts = Counter(agent_orgs)
    items = []
    for row in table["rows"]:
        if len(row) <= org_index:
            continue
        org = normalize_org(clean(row[org_index]))
        benchmark = indexed.get(org)
        for label, indexes, column, kind in fields:
            base_kind = kind.replace("_gap", "")
            header = headers[indexes[0]] if len(indexes) == 1 else ""
            month = months.get(base_kind)
            years = set(re.findall(r"(20\d{2})\s*年", header)) or intro_years
            year = int(next(iter(years))) if len(years) == 1 else None
            spec = MeasureSpec(label=label, unit="pp" if kind.endswith("gap") else "%", value_scale="pp" if kind.endswith("gap") else "percent")
            raw = row[indexes[0]] if len(indexes) == 1 and indexes[0] < len(row) else None
            actual, _, error = parse_number(raw, spec)
            compatible = base_kind == "year" or base_kind == sql_kind
            expected_raw = benchmark.get(column) if benchmark and compatible else None
            expected, _, sql_error = parse_number(expected_raw, spec, "sql")
            item = {"coordinates": {"organization": org}, "metric": label, "unit": spec.unit,
                    "actual_value": actual, "expected_value": expected, "actual_raw": raw, "expected_raw": expected_raw,
                    "delta": None, "tolerance": 0.01,
                    "agent_period": f"{year or '年份未声明'} · {str(month) + '月' if month else ''}{'累计' if base_kind == 'ytd' else '单月' if base_kind == 'month' else '年度'}",
                    "sql_period": f"{sql_year or '?'} · {str(sql_month) + '月' if base_kind != 'year' else ''}{'累计' if sql_kind == 'ytd' and base_kind != 'year' else '单月' if base_kind != 'year' else '年度目标'}" if compatible else "无对应基准",
                    "evidence": f"智能体：{header or '未识别字段'} = {raw}；SQL：{column} = {expected_raw}", "status": "PERIOD_REVIEW"}
            if len(indexes) != 1:
                item["status"] = "FIELD_UNRECOGNIZED"
            elif agent_counts[org] > 1 or sql_counts[org] > 1:
                item["status"] = "COORDINATE_REVIEW"
            elif not benchmark or not compatible or expected is None or sql_error:
                item["status"] = "NO_BENCHMARK"
            elif actual is None or error:
                item["status"] = "VALUE_UNPARSEABLE"
            elif kind.endswith("gap"):
                item["status"] = "CALIBER_REVIEW"
                item["evidence"] += "；需确认实际减目标或目标减实际，不自动反转符号"
            elif not valid_period or (base_kind != "year" and (month is None or month != sql_month)) or (year is not None and year != sql_year) or len(years) > 1:
                item["status"] = "PERIOD_REVIEW"
            else:
                ok, delta, _ = values_close(expected, actual, spec)
                item["delta"] = delta
                item["status"] = ("VALUE_MATCH_REVIEW" if ok else "VALUE_DIFF_REVIEW") if year is None else ("MATCH" if ok else "WRONG_VALUE")
            items.append(item)
    return {"items": items, "issues": ["按单位、字段、期间、粒度逐项对应；未声明年份的结果仅作条件数值比较。差距方向未确认时不判对错。"],
            "mapping": [{"agent_field": " / ".join(headers[i] for i in indexes) or "未识别", "metric": label, "sql_field": column, "aggregation": kind} for label, indexes, column, kind in fields]}
