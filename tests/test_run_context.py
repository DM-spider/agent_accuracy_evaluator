# -*- coding: utf-8 -*-
from evaluator.contract_builder import build_contract, historical_params_for, template_matches_source
from evaluator.golden_loader import merge_v1_golden
from evaluator.run_context import build_run_context, params_for_resolver, preview_sql


def test_fixed_clock_relative_periods():
    ctx = build_run_context("2026-08-31T10:00:00+08:00", latest_periods={"SzwgBusinessYear": 202607, "SzwgBusiness": 202607})
    assert ctx.timezone == "Asia/Shanghai"
    assert ctx.current_month == "202608"
    assert ctx.previous_month == "202607"
    assert ctx.current_month_start == "2026-08-01"
    assert ctx.next_month_start == "2026-09-01"
    assert ctx.last_7_start == "2026-08-25"
    assert ctx.last_30_start == "2026-08-02"
    assert ctx.year_start == "2026-01-01"
    assert ctx.jan_may_period == "202605"
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


def test_v3_period_resolvers():
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


def test_historical_sql_templates_equivalent():
    golden = merge_v1_golden()
    mismatches = []
    blocked = []
    for case in golden["cases"]:
        contract = build_contract(case)
        if not contract.numeric_evaluable:
            continue
        if not contract.realtime_ready:
            blocked.append(contract.case_id)
            continue
        if not template_matches_source(case, contract):
            params = historical_params_for(case, contract)
            mismatches.append(
                (
                    contract.case_id,
                    preview_sql(contract.sql_template, params)[:180],
                    (contract.sql_source or "")[:180],
                )
            )
    assert not mismatches, mismatches[:5]
    # 未完成迁移的题目不得被当成可评分
    for case_id in blocked:
        contract = build_contract(next(c for c in golden["cases"] if c["case_id"] == case_id))
        assert contract.not_scored_reason == "SQL_NOT_REALTIME_READY"
