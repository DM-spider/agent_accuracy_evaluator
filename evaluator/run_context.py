# -*- coding: utf-8 -*-
"""统一时间上下文。所有相对时间只在批次开始时解析一次。"""
from __future__ import annotations

import hashlib
import re
import uuid
from datetime import date, datetime, timedelta
from typing import Any, Dict, Optional, Tuple
from zoneinfo import ZoneInfo

from evaluator.models import RunContext

SHANGHAI = ZoneInfo("Asia/Shanghai")
# Skip SQL literals, identifiers and comments before finding bind parameters.
SQL_TOKEN_RE = re.compile(r"--[^\n]*|/\*.*?\*/|'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"|\$(?:[A-Za-z_]\w*)?\$.*?\$(?:[A-Za-z_]\w*)?\$|(?<!:):[A-Za-z_]\w*", re.S)


def parameter_tokens(template: str):
    for match in SQL_TOKEN_RE.finditer(template or ""):
        token = match.group()
        quoted = re.fullmatch(r"':([A-Za-z_]\w*)'", token)
        if quoted:
            yield match, quoted.group(1), True
        elif token.startswith(":"):
            yield match, token[1:], False


def substitute_params(template: str, render):
    chunks, end = [], 0
    for match, name, quoted in parameter_tokens(template):
        chunks.extend((template[end:match.start()], render(name, quoted)))
        end = match.end()
    chunks.append(template[end:])
    return "".join(chunks)


def ym_of(d: date) -> int:
    return d.year * 100 + d.month


def ym_shift(ym: int, delta: int) -> int:
    year, month = ym // 100, ym % 100
    month += delta
    year += (month - 1) // 12
    month = (month - 1) % 12 + 1
    return year * 100 + month


def ym_first_day(ym: int) -> date:
    return date(ym // 100, ym % 100, 1)


def ym_last_day(ym: int) -> date:
    return ym_first_day(ym_shift(ym, 1)) - timedelta(days=1)


def ym_str(ym: int) -> str:
    return f"{ym:06d}"


def iso(d: date) -> str:
    return d.isoformat()


def parse_anchor(anchor: datetime | date | str, tz_name: str = "Asia/Shanghai") -> datetime:
    tz = ZoneInfo(tz_name)
    if isinstance(anchor, datetime):
        if anchor.tzinfo is None:
            return anchor.replace(tzinfo=tz)
        return anchor.astimezone(tz)
    if isinstance(anchor, date):
        return datetime(anchor.year, anchor.month, anchor.day, 10, 0, tzinfo=tz)
    text = str(anchor).strip()
    if "T" in text or " " in text:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=tz)
        return dt.astimezone(tz)
    d = date.fromisoformat(text[:10])
    return datetime(d.year, d.month, d.day, 10, 0, tzinfo=tz)


def make_run_id(anchor: datetime) -> str:
    stamp = anchor.strftime("%Y%m%d_%H%M%S")
    suffix = hashlib.sha1(uuid.uuid4().bytes).hexdigest()[:4]
    return f"{stamp}_{suffix}"


def monday_of(d: date) -> date:
    return d - timedelta(days=d.weekday())


def quarter_start(d: date) -> date:
    return date(d.year, ((d.month - 1) // 3) * 3 + 1, 1)


def prev_quarter_range(d: date) -> Tuple[date, date]:
    qs = quarter_start(d)
    prev_last = qs - timedelta(days=1)
    prev_start = date(prev_last.year, ((prev_last.month - 1) // 3) * 3 + 1, 1)
    return prev_start, prev_last


def build_run_context(
    anchor_time: datetime | date | str,
    *,
    timezone_name: str = "Asia/Shanghai",
    agent_name: str = "water-loss-agent",
    contract_version: str = "v3",
    latest_periods: Optional[Dict[str, int]] = None,
    run_id: Optional[str] = None,
) -> RunContext:
    """禁止各模块自行 datetime.now()。统一从这里取相对时间。"""
    anchor = parse_anchor(anchor_time, timezone_name)
    day = anchor.date()
    latest = dict(latest_periods or {})
    calendar_month = ym_of(day)
    year_latest = latest.get("SzwgBusinessYear")
    single_latest = latest.get("SzwgBusiness")
    effective_year = year_latest if year_latest is not None and calendar_month > year_latest else calendar_month
    if year_latest is not None:
        effective_year = min(calendar_month, year_latest)
    effective_single = calendar_month
    if single_latest is not None:
        effective_single = min(calendar_month, single_latest)

    last6_end = effective_year if year_latest is not None else ym_shift(calendar_month, -1)
    last6 = [ym_str(ym_shift(last6_end, i)) for i in range(-5, 1)]
    last3_end = ym_shift(calendar_month, -1)
    last3 = [ym_str(ym_shift(last3_end, i)) for i in range(-2, 1)]
    last12 = [ym_str(ym_shift(calendar_month, i)) for i in range(-11, 1)]
    pqs, pqe = prev_quarter_range(day)

    ctx = RunContext(
        run_id=run_id or make_run_id(anchor),
        anchor_time=anchor,
        timezone=timezone_name,
        current_month=ym_str(calendar_month),
        previous_month=ym_str(ym_shift(calendar_month, -1)),
        current_month_start=iso(day.replace(day=1)),
        next_month_start=iso(ym_first_day(ym_shift(calendar_month, 1))),
        current_month_end=iso(ym_last_day(calendar_month)),
        previous_month_start=iso(ym_first_day(ym_shift(calendar_month, -1))),
        previous_month_end=iso(ym_last_day(ym_shift(calendar_month, -1))),
        last_7_start=iso(day - timedelta(days=6)),
        last_7_end=iso(day),
        last_30_start=iso(day - timedelta(days=29)),
        last_30_end=iso(day),
        year_start=iso(date(day.year, 1, 1)),
        today=iso(day),
        yesterday=iso(day - timedelta(days=1)),
        week_start=iso(monday_of(day)),
        jan_may_period=ym_str(day.year * 100 + 5),
        yoy_month=ym_str(ym_shift(effective_year, -12)),
        last_6_months=last6,
        last_3_months=last3,
        last_12_months=last12,
        quarter_start=iso(quarter_start(day)),
        prev_quarter_start=iso(pqs),
        prev_quarter_end=iso(pqe),
        effective_single_month=ym_str(effective_single),
        effective_year_month=ym_str(effective_year),
        agent_name=agent_name,
        contract_version=contract_version,
        latest_periods=latest,
        bind_params={},
    )
    ctx.bind_params = flatten_bind_params(ctx)
    return ctx


def flatten_bind_params(ctx: RunContext) -> Dict[str, Any]:
    params: Dict[str, Any] = {
        "period": int(ctx.effective_single_month),
        "period_year": int(ctx.effective_year_month),
        "period_prev": int(ctx.previous_month),
        "period_yoy": int(ctx.yoy_month),
        "period_jan_may": int(ctx.jan_may_period),
        "current_month": int(ctx.current_month),
        "start_date": ctx.current_month_start,
        "end_date": ctx.current_month_end,
        "month_start": ctx.current_month_start,
        "month_end": ctx.current_month_end,
        "next_month_start": ctx.next_month_start,
        "prev_month_start": ctx.previous_month_start,
        "prev_month_end": ctx.previous_month_end,
        "last_7_start": ctx.last_7_start,
        "last_7_end": ctx.last_7_end,
        "last_30_start": ctx.last_30_start,
        "last_30_end": ctx.last_30_end,
        "year_start": ctx.year_start,
        "today": ctx.today,
        "yesterday": ctx.yesterday,
        "week_start": ctx.week_start,
        "quarter_start": ctx.quarter_start,
        "prev_quarter_start": ctx.prev_quarter_start,
        "prev_quarter_end": ctx.prev_quarter_end,
        "as_of_date": ctx.today,
        "date_start": ctx.current_month_start,
        "date_end": ctx.next_month_start,
    }
    for index, ym in enumerate(ctx.last_6_months):
        params[f"period_{index}"] = int(ym)
    for index, ym in enumerate(ctx.last_3_months):
        start = ym_first_day(int(ym))
        end = ym_last_day(int(ym))
        params[f"m{index}_start"] = iso(start)
        params[f"m{index}_end"] = iso(end)
        params[f"m{index}_period"] = int(ym)
    if ctx.last_12_months:
        first = int(ctx.last_12_months[0])
        last = int(ctx.last_12_months[-1])
        params["y12_start"] = iso(ym_first_day(first))
        params["y12_end"] = iso(ym_last_day(last))
    return params


RESOLVER_PARAM_KEYS = {
    "none": [],
    "single_month": ["period"],
    "previous_month": ["period"],
    "year_month": ["period"],
    "jan_may": ["period"],
    "last_6_months": [f"period_{i}" for i in range(6)],
    "yoy_mom": ["period", "period_yoy", "period_prev"],
    "month_range": ["period", "start_date", "end_date", "next_month_start"],
    "prev_month_range": ["period", "start_date", "end_date"],
    "last_7_days": ["start_date", "end_date"],
    "last_30_days": ["start_date", "end_date"],
    "date_range": ["start_date", "end_date"],
    "as_of_date": ["as_of_date"],
    "current_prev_month": ["period", "period_prev", "start_date", "end_date", "next_month_start"],
    "quarter_vs_prev": ["start_date", "end_date", "prev_quarter_start", "prev_quarter_end"],
    "last_3_months": ["m0_start", "m0_end", "m1_start", "m1_end", "m2_start", "m2_end"],
    "last_12_months": ["y12_start", "y12_end"],
    "today_week_month": ["today", "week_start", "start_date", "end_date"],
    "month_to_cutoff": ["start_date", "end_date", "as_of_date"],
    "ytd_and_last_month": ["period", "period_prev", "year_start"],
    "h1": ["period"],
    "jan_jun": [f"period_{i}" for i in range(6)],
    "jan_may_series": [f"period_{i}" for i in range(5)],
    "may_jul_range": ["start_date", "end_date"],
    "ytd_range": ["year_start", "start_date", "end_date", "as_of_date"],
    "yesterday": ["start_date", "end_date", "as_of_date"],
    "fixed_202606": ["period", "start_date", "end_date", "as_of_date"],
    "single_mom": ["period", "period_prev"],
}


def params_for_resolver(ctx: RunContext, resolver: str) -> Dict[str, Any]:
    """只返回白名单解析器生成的参数，禁止把模型文本拼进 SQL。"""
    if resolver in {"none", "unresolved"}:
        return {}
    bind = ctx.bind_params or flatten_bind_params(ctx)
    if resolver == "single_month":
        return {"period": int(ctx.effective_single_month)}
    if resolver == "previous_month":
        return {"period": int(ctx.previous_month), "start_date": ctx.previous_month_start, "end_date": ctx.previous_month_end}
    if resolver == "year_month":
        period = int(ctx.effective_year_month)
        return {
            "period": period,
            "year_start": ctx.year_start,
            "as_of_date": iso(ym_last_day(period)),
            "start_date": iso(ym_first_day(period)),
            "end_date": iso(ym_last_day(period)),
        }
    if resolver == "jan_may":
        return {"period": int(ctx.jan_may_period)}
    if resolver == "last_6_months":
        out = {f"period_{i}": int(ym) for i, ym in enumerate(ctx.last_6_months)}
        if ctx.last_6_months:
            first = int(ctx.last_6_months[0])
            last = int(ctx.last_6_months[-1])
            out["start_date"] = iso(ym_first_day(first))
            out["end_date"] = iso(ym_first_day(ym_shift(last, 1)))
            for i, ym in enumerate(ctx.last_6_months):
                out[f"date_{i}"] = iso(ym_last_day(int(ym)))
        return out
    if resolver == "yoy_mom":
        period = int(ctx.effective_year_month)
        return {
            "period": period,
            "period_yoy": int(ctx.yoy_month),
            "period_prev": int(ctx.previous_month),
            "year_start": ctx.year_start,
            "as_of_date": iso(ym_last_day(period)),
            "date_yoy": iso(ym_last_day(int(ctx.yoy_month))),
            "date_prev": iso(ym_last_day(int(ctx.previous_month))),
        }
    if resolver == "month_range":
        return {
            "period": int(ctx.effective_single_month),
            "period_prev": int(ctx.previous_month),
            "start_date": ctx.current_month_start,
            "end_date": ctx.current_month_end,
            "next_month_start": ctx.next_month_start,
            "prev_month_start": ctx.previous_month_start,
            "prev_month_end": ctx.previous_month_end,
            "year_start": ctx.year_start,
        }
    if resolver == "prev_month_range":
        return {
            "period": int(ctx.previous_month),
            "start_date": ctx.previous_month_start,
            "end_date": ctx.previous_month_end,
            "next_month_start": ctx.current_month_start,
        }
    if resolver == "last_7_days":
        return {"start_date": ctx.last_7_start, "end_date": ctx.last_7_end}
    if resolver == "last_30_days":
        return {"start_date": ctx.last_30_start, "end_date": ctx.last_30_end}
    if resolver == "date_range":
        return {"start_date": ctx.current_month_start, "end_date": ctx.next_month_start}
    if resolver == "as_of_date":
        return {"as_of_date": ctx.today}
    if resolver == "current_prev_month":
        prev_prev = ym_shift(int(ctx.previous_month), -1)
        return {
            "period": int(ctx.previous_month),
            "period_prev": prev_prev,
            "start_date": ctx.previous_month_start,
            "end_date": ctx.current_month_start,
            "next_month_start": ctx.current_month_start,
            "prev_month_start": iso(ym_first_day(prev_prev)),
            "prev_month_end": iso(ym_last_day(prev_prev)),
            "prev_next_start": ctx.previous_month_start,
        }
    if resolver == "quarter_vs_prev":
        return {
            "start_date": ctx.quarter_start,
            "end_date": ctx.today,
            "prev_quarter_start": ctx.prev_quarter_start,
            "prev_quarter_end": ctx.prev_quarter_end,
        }
    if resolver == "last_3_months":
        out = {}
        for i, ym in enumerate(ctx.last_3_months):
            out[f"m{i}_start"] = iso(ym_first_day(int(ym)))
            out[f"m{i}_end"] = iso(ym_last_day(int(ym)))
        return out
    if resolver == "last_12_months":
        first = int(ctx.last_12_months[0])
        last = int(ctx.last_12_months[-1])
        return {"y12_start": iso(ym_first_day(first)), "y12_end": iso(ym_last_day(last))}
    if resolver == "today_week_month":
        return {
            "today": ctx.today,
            "week_start": ctx.week_start,
            "start_date": ctx.previous_month_start,
            "end_date": ctx.previous_month_end,
        }
    if resolver == "month_to_cutoff":
        return {
            "start_date": ctx.previous_month_start,
            "end_date": ctx.current_month_start,
            "as_of_date": iso(date.fromisoformat(ctx.today) - timedelta(days=10)),
        }
    if resolver == "ytd_and_last_month":
        return {
            "period": int(ctx.effective_year_month),
            "period_prev": int(ctx.previous_month),
            "year_start": ctx.year_start,
        }
    day = date.fromisoformat(ctx.today)
    if resolver == "h1":
        return {"period": day.year * 100 + 6}
    if resolver == "jan_jun":
        return {f"period_{i}": day.year * 100 + (i + 1) for i in range(6)}
    if resolver == "jan_may_series":
        return {f"period_{i}": day.year * 100 + (i + 1) for i in range(5)}
    if resolver == "may_jul_range":
        return {
            "start_date": f"{day.year:04d}-05-01",
            "end_date": f"{day.year:04d}-08-01",
            "next_month_start": f"{day.year:04d}-08-01",
        }
    if resolver == "ytd_range":
        end = iso(day + timedelta(days=1))
        return {
            "year_start": ctx.year_start,
            "start_date": ctx.year_start,
            "end_date": end,
            "date_end": end,
            "next_month_start": end,
            "as_of_date": ctx.today,
        }
    if resolver == "yesterday":
        return {
            "start_date": ctx.yesterday,
            "end_date": ctx.today,
            "next_month_start": ctx.today,
            "as_of_date": ctx.yesterday,
        }
    if resolver == "fixed_202606":
        return {
            "period": 202606,
            "start_date": "2026-06-01",
            "end_date": "2026-06-30",
            "as_of_date": "2026-06-30",
        }
    if resolver == "single_mom":
        period = int(ctx.effective_single_month)
        return {"period": period, "period_prev": ym_shift(period, -1)}
    keys = RESOLVER_PARAM_KEYS.get(resolver, [])
    return {k: bind[k] for k in keys if k in bind}


def normalize_sql(sql: str) -> str:
    text = re.sub(r"'(\d{4}-\d{2}-\d{2})[ T]\d{2}:\d{2}:\d{2}'", r"'\1'", sql or "")
    text = re.sub(r"\s+", " ", text).strip()
    text = text.replace("( ", "(").replace(" )", ")")
    text = re.sub(r",\s*", ",", text)
    return text.lower()


def replace_literal(sql: str, old: str, placeholder: str) -> str:
    if not old or old in placeholder:
        return sql
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", old):
        sql = re.sub(rf"'{re.escape(old)}(?:[ T]\d{{2}}:\d{{2}}:\d{{2}})?'", placeholder, sql)
        sql = re.sub(rf"(?<![:\d]){re.escape(old)}(?![:\d])", placeholder, sql)
        return sql
    if re.fullmatch(r"\d{8}", old) or re.fullmatch(r"\d{6}", old):
        return re.sub(rf"(?<!\d){re.escape(old)}(?!\d)", placeholder, sql)
    return sql.replace(old, placeholder)


def template_params(template: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """只取出模板里出现过的参数，禁止把无关值拼进评测 SQL。"""
    names = [name for _, name, _ in parameter_tokens(template)]
    return {name: params[name] for name in names if name in params}


def preview_sql(template: str, params: Dict[str, Any]) -> str:
    """把命名参数替换为字面量，仅用于日志预览和历史等价回归。"""
    def render(name, quoted):
        if name not in params:
            return f"':{name}'" if quoted else f":{name}"
        value = params[name]
        if value is None:
            return "NULL"
        if quoted:
            value = str(value)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            rendered = str(int(value) if float(value).is_integer() else value)
        else:
            rendered = "'" + str(value).replace("'", "''") + "'"
        return rendered
    return substitute_params(template, render)


def to_pg_params(template: str, params: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    tokens = list(parameter_tokens(template))
    names = [name for _, name, _ in tokens]
    used = {name: params[name] for name in names if name in params}
    missing = [name for name in names if name not in params]
    if missing:
        raise ValueError(f"SQL 缺少参数: {missing}")
    def render(name, quoted):
        if quoted:
            alias = f"__text_{name}"
            if alias in params:
                raise ValueError("reserved parameter name")
            used[alias] = str(params[name]) if params[name] is not None else None
            return f"%({alias})s"
        return f"%({name})s"
    pg_sql = substitute_params(template, render)
    return pg_sql, used
