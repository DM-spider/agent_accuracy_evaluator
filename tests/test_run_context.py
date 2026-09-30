# -*- coding: utf-8 -*-
from evaluator.run_context import build_run_context, params_for_resolver


def test_fixed_clock_relative_periods():
    ctx = build_run_context("2026-08-31T10:00:00+08:00", latest_periods={"SzwgBusinessYear": 202607, "SzwgBusiness": 202607})
    assert ctx.timezone == "Asia/Shanghai"
    assert ctx.current_month == "202608"
    assert ctx.previous_month == "202607"
    assert ctx.current_month_start == "2026-08-01"
    assert ctx.next_month_start == "2026-09-01"
    assert ctx.year_start == "2026-01-01"
    assert ctx.yoy_month == "202507"
    assert ctx.last_6_months[0] == "202602"
    assert ctx.last_6_months[-1] == "202607"
    yoy = params_for_resolver(ctx, "yoy_mom")
    assert yoy["period_yoy"] == 202507


def test_resolvers_use_explicit_anchor_only():
    ctx = build_run_context("2026-08-17")
    assert ctx.today == "2026-08-17"
    assert ctx.yesterday == "2026-08-16"
    assert ctx.current_month == "202608"


def test_period_resolvers():
    ctx = build_run_context(
        "2026-08-17",
        latest_periods={"SzwgBusinessYear": 202607, "SzwgBusiness": 202606},
    )
    assert params_for_resolver(ctx, "h1")["period"] == 202606
    jan_jun = params_for_resolver(ctx, "jan_jun")
    assert jan_jun["period_0"] == 202601 and jan_jun["period_5"] == 202606
    jan_may = params_for_resolver(ctx, "jan_may_series")
    assert jan_may["period_0"] == 202601 and jan_may["period_4"] == 202605
    mj = params_for_resolver(ctx, "may_jul_range")
    assert mj["start_date"] == "2026-05-01" and mj["end_date"] == "2026-08-01"
    ytd = params_for_resolver(ctx, "ytd_range")
    assert ytd["year_start"] == "2026-01-01"
    assert ytd["start_date"] == "2026-01-01"
    yest = params_for_resolver(ctx, "yesterday")
    assert yest["start_date"] == "2026-08-16"
    fx = params_for_resolver(ctx, "fixed_202606")
    assert fx["period"] == 202606
    mom = params_for_resolver(ctx, "single_mom")
    assert mom["period"] == 202606
    assert mom["period_prev"] == 202605
