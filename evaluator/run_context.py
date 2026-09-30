# -*- coding: utf-8 -*-
"""统一时间上下文与 SQL 参数解析。

只实现当前 141 题契约实际使用的参数解析器；相对时间只在批次开始时解析一次。
"""
from __future__ import annotations

import hashlib
import re
import uuid
from datetime import date, datetime, timedelta
from typing import Any, Dict, Optional, Tuple
from zoneinfo import ZoneInfo

from evaluator.models import RunContext

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


def build_run_context(
    anchor_time: datetime | date | str,
    *,
    timezone_name: str = "Asia/Shanghai",
    agent_name: str = "water-loss-agent",
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
    effective_year = calendar_month if year_latest is None else min(calendar_month, year_latest)
    effective_single = calendar_month if single_latest is None else min(calendar_month, single_latest)
    last6_end = effective_year if year_latest is not None else ym_shift(calendar_month, -1)
    last6 = [ym_str(ym_shift(last6_end, i)) for i in range(-5, 1)]

    return RunContext(
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
        year_start=iso(date(day.year, 1, 1)),
        today=iso(day),
        yesterday=iso(day - timedelta(days=1)),
        yoy_month=ym_str(ym_shift(effective_year, -12)),
        last_6_months=last6,
        effective_single_month=ym_str(effective_single),
        effective_year_month=ym_str(effective_year),
        agent_name=agent_name,
        latest_periods=latest,
    )


def params_for_resolver(ctx: RunContext, resolver: str) -> Dict[str, Any]:
    """按契约声明的解析器生成参数；未知解析器直接报错，禁止静默兜底。"""
    if resolver in {"none", "unresolved"}:
        return {}
    if resolver == "single_month":
        return {"period": int(ctx.effective_single_month)}
    if resolver == "year_month":
        period = int(ctx.effective_year_month)
        return {
            "period": period,
            "year_start": ctx.year_start,
            "as_of_date": iso(ym_last_day(period)),
            "start_date": iso(ym_first_day(period)),
            "end_date": iso(ym_last_day(period)),
        }
    if resolver == "last_6_months":
        return {f"period_{i}": int(ym) for i, ym in enumerate(ctx.last_6_months)}
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
            "year_start": ctx.year_start,
        }
    if resolver == "prev_month_range":
        return {
            "period": int(ctx.previous_month),
            "start_date": ctx.previous_month_start,
            "end_date": ctx.previous_month_end,
            "next_month_start": ctx.current_month_start,
        }
    if resolver == "as_of_date":
        return {"as_of_date": ctx.today}
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
    raise ValueError(f"未知参数解析器: {resolver}")




def template_params(template: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """只取出模板里出现过的参数，禁止把无关值拼进评测 SQL。"""
    names = [name for _, name, _ in parameter_tokens(template)]
    return {name: params[name] for name in names if name in params}


def preview_sql(template: str, params: Dict[str, Any]) -> str:
    """把命名参数替换为字面量，仅用于日志预览。"""
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
