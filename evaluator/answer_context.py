"""Conservative period alignment, using answer evidence rather than numeric guesses."""
from __future__ import annotations

import calendar
import re
from datetime import date

from evaluator.models import CaseContract, RunContext
from evaluator.run_context import params_for_resolver, template_params, ym_shift

MONTH = re.compile(r"(?<!\d)(20\d{2})\s*年\s*(0?[1-9]|1[0-2])\s*月")
ISO_MONTH = re.compile(r"(?<!\d)(20\d{2})[-/](0?[1-9]|1[0-2])(?!\d)")
COMPACT_MONTH = re.compile(r"(?<!\d)(20\d{2})(0[1-9]|1[0-2])(?!\d)")
MONTH_ONLY = re.compile(r"(?<!\d)(1[0-2]|0?[1-9])\s*月")
ADOPTED_RE = re.compile(r"(?:以下为|如下为|最新完整月|改用|采用|数据截至|统计截至|(?<!累计)截至)[^\d]{0,20}(?:(20\d{2})\s*年\s*)?(0?[1-9]|1[0-2])\s*月")
NO_DATA_RE = re.compile(r"(20\d{2})\s*年\s*(0?[1-9]|1[0-2])\s*月[^\n。]{0,16}无数据")
ARROW_RE = re.compile(r"(?:(20\d{2})\s*年\s*)?(0?[1-9]|1[0-2])\s*月\s*(?:→|->)\s*(?:(20\d{2})\s*年\s*)?(0?[1-9]|1[0-2])\s*月")


def primary_block(text: str) -> str:
    """Keep the first answer table and its introduction, excluding later comparisons."""
    lines = text.splitlines()
    start = next((i for i, line in enumerate(lines) if line.strip().startswith("|")), None)
    if start is None:
        return re.split(r"\n\s*(?:#{1,6}\s*)?(?:\*\*)?(?:环比|同比|补充|历史对比)", text, maxsplit=1)[0]
    end = start
    while end < len(lines) and lines[end].strip().startswith("|"):
        end += 1
    return "\n".join(lines[:end])


def _month_delta(source: int, target: int) -> int:
    sy, sm = divmod(source, 100)
    ty, tm = divmod(target, 100)
    return (ty - sy) * 12 + tm - sm


def params_for_period(contract: CaseContract, context: RunContext, period: int):
    """Move all period parameters as one window instead of replacing one field."""
    requested = template_params(contract.sql_template, params_for_resolver(context, contract.parameter_resolver))
    base = requested.get("period")
    if not isinstance(base, int) or not re.fullmatch(r"20\d{2}(0[1-9]|1[0-2])", str(period)):
        return requested
    offset = _month_delta(base, period)
    params = {}
    for key, value in requested.items():
        if isinstance(value, int) and re.fullmatch(r"20\d{2}(0[1-9]|1[0-2])", str(value)):
            params[key] = ym_shift(value, offset)
            continue
        if isinstance(value, str) and re.fullmatch(r"20\d{2}-\d{2}-\d{2}", value):
            original = date.fromisoformat(value)
            shifted_ym = ym_shift(original.year * 100 + original.month, offset)
            year, month = divmod(shifted_ym, 100)
            last = calendar.monthrange(year, month)[1]
            source_last = calendar.monthrange(original.year, original.month)[1]
            day = last if original.day == source_last else min(original.day, last)
            params[key] = date(year, month, day).isoformat()
            continue
        params[key] = value
    if "year_start" in params:
        params["year_start"] = date(period // 100, 1, 1).isoformat()
    return params


MULTI_PERIOD_RESOLVERS = {"last_6_months", "last_3_months", "last_12_months"}
MOM_RESOLVERS = {"single_mom", "yoy_mom"}


def _ym_from_parts(year, month, context: RunContext) -> int:
    month_n = int(month)
    if year:
        return int(year) * 100 + month_n
    current = int(context.current_month)
    year_n = current // 100
    if month_n > current % 100:
        year_n -= 1
    return year_n * 100 + month_n


def _adopted_period(text: str, context: RunContext):
    adopted = ADOPTED_RE.search(text or "")
    if adopted:
        return _ym_from_parts(adopted.group(1), adopted.group(2), context)
    empty = NO_DATA_RE.search(text or "")
    if empty:
        skipped = int(empty.group(1)) * 100 + int(empty.group(2))
        rest = [int(y) * 100 + int(m) for y, m in MONTH.findall(text or "") if int(y) * 100 + int(m) != skipped]
        if len(rest) == 1:
            return rest[0]
    return None


def _arrow_periods(text: str, context: RunContext):
    arrow = ARROW_RE.search(text or "")
    if not arrow:
        return None, None
    prev = _ym_from_parts(arrow.group(1), arrow.group(2), context)
    current = _ym_from_parts(arrow.group(3), arrow.group(4), context)
    return prev, current


def _requested_periods(requested):
    values = []
    for key, value in requested.items():
        if key == "period" or re.fullmatch(r"period_\d+", str(key)):
            if isinstance(value, int) and re.fullmatch(r"20\d{2}(0[1-9]|1[0-2])", str(value)):
                values.append(value)
    return sorted(values)


def _hint_periods(time_hints):
    periods = set()
    for hint in time_hints or []:
        value = str(hint.get("value", "")).replace("-", "").replace("/", "")
        if len(value) >= 6:
            value = value[:6]
        if re.fullmatch(r"20\d{2}(0[1-9]|1[0-2])", value):
            periods.add(int(value))
    return sorted(periods)


def align_answer(contract: CaseContract, context: RunContext, text: str, time_hints=None):
    requested = template_params(contract.sql_template, params_for_resolver(context, contract.parameter_resolver))
    block = primary_block(text)
    clean = block.replace("**", "").replace("`", "")
    periods = sorted({int(y) * 100 + int(m) for y, m in MONTH.findall(clean) + ISO_MONTH.findall(clean) + COMPACT_MONTH.findall(clean)})
    month_only_ambiguous = False
    if not periods:
        months = sorted({int(month) for month in MONTH_ONLY.findall(clean)})
        latest = re.search(r"(?:最新完整月|最新数据月份|数据截至|统计截至)[^\d]{0,10}(1[0-2]|0?[1-9])\s*月", clean)
        is_range = bool(re.search(r"20\d{2}\s*年\s*\d{1,2}\s*[-至到]\s*\d{1,2}\s*月", clean))
        if latest:
            months = [int(latest.group(1))]
        elif len(months) > 1 or is_range:
            month_only_ambiguous = True
        if len(months) == 1 and not is_range:
            month = months[0]
            year = int(context.current_month[:4]) - (1 if month > int(context.current_month[-2:]) else 0)
            periods = [year * 100 + month]
    tool_periods = _hint_periods(time_hints)
    requested_period = requested.get("period") if isinstance(requested.get("period"), int) else None
    info = {"status": "REVIEW", "requested_params": requested, "reported_periods": periods,
            "sql_params": {}, "evidence": block, "issues": [], "period_changed": False,
            "benchmark_caliber": contract.caliber, "scope": "primary_answer_block",
            "period_source": "unresolved", "period_confidence": "unknown", "tool_periods": tool_periods,
            "requested_period": requested_period, "effective_period": None, "sql_period": None,
            "fallback_reason": None, "alignment_status": "UNRESOLVED"}
    issues = info["issues"]
    sql = contract.sql_template
    expected_kind = "ytd" if "'SzwgBusinessYear'" in sql else "month" if "'SzwgBusiness'" in sql else "unknown"
    has_ytd = bool(re.search(r"年累计|累计|1\s*[-至到]\s*\d{1,2}\s*月", clean))
    has_month = bool(re.search(r"当月|单月|月度", clean))
    reported_kind = "mixed" if has_ytd and has_month else "ytd" if has_ytd else "month" if has_month else "unknown"
    info.update(expected_aggregation=expected_kind, reported_aggregation=reported_kind)
    if expected_kind != "unknown" and reported_kind != expected_kind:
        issues.append("AGGREGATION_UNCONFIRMED" if reported_kind == "unknown" else "AGGREGATION_MISMATCH")
    if "集团本部" in clean and "集团" in contract.question and "zoneid IN" in sql:
        issues.append("ORGANIZATION_MISMATCH")
    elif "集团" in contract.question and "zoneid IN" in sql and not contract.row_key:
        # Group names alone cannot prove the benchmark's explicit subsidiary scope.
        if not ("剔除布吉" in clean or "不含布吉" in clean):
            issues.append("ORGANIZATION_UNCONFIRMED")
    info["reported_organization"] = "集团本部" if "集团本部" in clean else "见回答证据"
    allowed = {"single_month", "previous_month", "year_month", "jan_may", "month_range", "prev_month_range",
               "single_mom", "yoy_mom"}
    requested_window = _requested_periods(requested)
    if contract.parameter_resolver in MULTI_PERIOD_RESOLVERS and periods and sorted(periods) == requested_window:
        info["sql_params"] = requested
        info["period_source"] = "agent_answer"
        info["period_confidence"] = "confirmed"
        info["effective_period"] = requested.get("period")
        info["sql_period"] = requested.get("period")
        info["alignment_status"] = "REVIEW" if issues else "ALIGNED"
        info["status"] = "REVIEW" if issues else "ALIGNED"
        return info, block
    if contract.parameter_resolver not in allowed:
        issues.append("PERIOD_RESOLVER_REVIEW")
    adopted = _adopted_period(clean, context)
    prev_period, arrow_current = (None, None)
    if contract.parameter_resolver in MOM_RESOLVERS or "环比" in contract.question:
        prev_period, arrow_current = _arrow_periods(clean, context)
    if arrow_current:
        info["previous_period"] = prev_period
    if adopted:
        period = adopted
        info["period_source"] = "agent_answer"
        info["period_confidence"] = "confirmed"
        info["fallback_reason"] = "requested_period_no_data" if NO_DATA_RE.search(clean) else "adopted_complete_month"
    elif arrow_current:
        period = arrow_current
        info["period_source"] = "agent_answer"
        info["period_confidence"] = "confirmed"
    elif month_only_ambiguous and not adopted:
        issues.append("PERIOD_AMBIGUOUS")
        return info, block
    elif periods and tool_periods and sorted(periods) != sorted(tool_periods) and not adopted:
        issues.append("PERIOD_CONFLICT")
        return info, block
    else:
        if not periods and len(tool_periods) == 1:
            periods = tool_periods
            info["reported_periods"] = periods
            info["period_source"] = "agent_tool"
            info["period_confidence"] = "confirmed"
        elif len(periods) == 1:
            info["period_source"] = "agent_answer"
            info["period_confidence"] = "confirmed"
        if len(periods) != 1:
            if not periods and requested:
                info["sql_params"] = requested
                info["sql_period"] = requested_period
                info["period_source"] = "question_contract"
                info["period_confidence"] = "inferred"
                issues.append("PERIOD_INFERRED")
                return info, block
            if len(periods) > 1 and requested_period in periods:
                others = [p for p in periods if p != requested_period]
                if len(others) == 1 and (NO_DATA_RE.search(clean) or ADOPTED_RE.search(clean)):
                    period = others[0]
                    info["period_source"] = "agent_answer"
                    info["period_confidence"] = "confirmed"
                    info["fallback_reason"] = "requested_period_no_data"
                else:
                    issues.append("PERIOD_AMBIGUOUS")
                    return info, block
            else:
                issues.append("PERIOD_AMBIGUOUS" if periods else "PERIOD_MISSING")
                return info, block
        else:
            period = periods[0]
    info["effective_period"] = period
    info["reported_periods"] = sorted(set((info.get("reported_periods") or periods or []) + [period]))
    if period > int(context.current_month):
        issues.append("FUTURE_PERIOD")
        return info, block
    if contract.parameter_resolver not in allowed:
        return info, block
    params = params_for_period(contract, context, period)
    info["sql_params"] = params
    info["sql_period"] = params.get("period", period)
    info["period_changed"] = params != requested
    if info["period_changed"]:
        # A conditional number check is not proof that the fallback month was appropriate.
        issues.append("PERIOD_FALLBACK_REVIEW")
        info["alignment_status"] = "ALIGNED_WITH_FALLBACK"
    else:
        info["alignment_status"] = "REVIEW" if issues else "ALIGNED"
    info["status"] = "REVIEW" if issues else "ALIGNED"
    return info, block
