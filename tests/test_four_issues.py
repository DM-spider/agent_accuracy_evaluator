# -*- coding: utf-8 -*-
from evaluator.answer_context import align_answer
from evaluator.comparator import compare_claims
from evaluator.extractor import extract_claims, claims_from_sql_rows
from evaluator.models import CaseContract, ClaimStatus, MeasureSpec, RunContext, SqlSnapshot, Tolerance
from evaluator.orchestrator import Orchestrator
from evaluator.repository import Repository
from evaluator.run_context import build_run_context
from evaluator.sql_executor import SqlExecutor

from tests.test_stream_live import client_for, done


def _cx005():
    return CaseContract(
        case_id="CX-005",
        question="本月环水集团年累计产销差率的年度目标是多少，目前与目标差距是多少？",
        result_type="stat",
        numeric_evaluable=True,
        realtime_ready=True,
        sql_template="SELECT rate, target_rate, gap FROM t WHERE businessyearmonth=:period",
        parameter_resolver="year_month",
        measures={
            "年累计产销差率": MeasureSpec(
                label="年累计产销差率", unit="%", value_scale="percent", aggregation="ytd",
                aliases=["累计产销差率", "当前产销差率"], required=False,
            ),
            "产销差率年度目标": MeasureSpec(
                label="产销差率年度目标", unit="%", value_scale="percent", aggregation="annual_target",
                aliases=["年度目标", "年度考核目标", "目标值"], sql_column="target_rate",
            ),
            "产销差率目标差距": MeasureSpec(
                label="产销差率目标差距", unit="pp", value_scale="pp", aggregation="actual_minus_target",
                aliases=["与目标差距", "目标差距", "低于目标", "高于目标", "差距"], sql_column="gap",
            ),
        },
    )


def _cx003():
    return CaseContract(
        case_id="CX-003",
        question="本月环水集团的供水量和售水量分别是多少万立方米？",
        result_type="stat",
        numeric_evaluable=True,
        realtime_ready=True,
        sql_template="SELECT supply, sales FROM t WHERE businessyearmonth=:period",
        parameter_resolver="single_month",
        measures={
            "单月供水量": MeasureSpec(
                label="单月供水量", unit="m³", value_scale="volume", aggregation="single_month",
                aliases=["供水量", "年累计供水量", "累计供水量"], sql_column="supply",
                tolerance=Tolerance(kind="rel", eps=0.001),
            ),
            "单月售水量": MeasureSpec(
                label="单月售水量", unit="m³", value_scale="volume", aggregation="single_month",
                aliases=["售水量", "年累计售水量", "累计售水量"], sql_column="sales",
                tolerance=Tolerance(kind="rel", eps=0.001),
            ),
        },
    )


def _cx007():
    return CaseContract(
        case_id="CX-007",
        question="本月环水集团单月产销差率较上月环比变动了多少？",
        result_type="stat",
        numeric_evaluable=True,
        realtime_ready=True,
        sql_template="SELECT rate, prev_rate, mom_change FROM t WHERE businessyearmonth=:period AND prev=:period_prev",
        parameter_resolver="single_mom",
        measures={
            "本期单月产销差率": MeasureSpec(
                label="本期单月产销差率", unit="%", value_scale="percent", aggregation="single_month",
                period_role="current", aliases=["单月产销差率", "本月产销差率", "产销差率"], sql_column="rate",
            ),
            "上期单月产销差率": MeasureSpec(
                label="上期单月产销差率", unit="%", value_scale="percent", aggregation="single_month",
                period_role="previous", aliases=["上月值", "上月产销差率"], sql_column="prev_rate",
            ),
            "单月产销差率环比变化": MeasureSpec(
                label="单月产销差率环比变化", unit="pp", value_scale="pp", aggregation="mom",
                period_role="comparison", aliases=["环比变化", "环比变动"], sql_column="mom_change",
            ),
        },
    )


CX005_TEXT = """2026年9月无数据，以下为采用2026年8月底数据。
| 指标 | 数值 |
|---|---|
| 年累计产销差率 | 7.91% |
| 年度考核目标 | 12.0% |
| 与目标差距 | 低于目标4.09个百分点 |
"""

CX007_TEXT = """最新完整月份为2026年8月，7月→8月单月产销差率环比如下。
| 指标 | 数值 |
|---|---|
| 本期单月产销差率 | 8.27% |
| 上月产销差率 | 7.98% |
| 环比变化 | 0.29个百分点 |
"""

CX003_TEXT = """| 指标 | 数值 |
|---|---|
| 年累计供水量 | 102,857 万立方米 |
| 年累计售水量 | 94,725 万立方米 |
"""


def test_cx005_adopts_complete_month_and_extracts_aliases():
    ctx = build_run_context("2026-09-07")
    info, _ = align_answer(_cx005(), ctx, CX005_TEXT)
    assert info["requested_period"] == 202609
    assert info["effective_period"] == 202608
    assert info["sql_params"]["period"] == 202608
    claims = {c.metric: c for c in extract_claims(_cx005(), CX005_TEXT)}
    assert abs(claims["年累计产销差率"].value - 7.91) < 1e-9
    assert abs(claims["产销差率年度目标"].value - 12.0) < 1e-9
    assert abs(claims["产销差率目标差距"].value + 4.09) < 1e-9


def test_cx005_fallback_still_compares(tmp_path):
    ctx = build_run_context("2026-09-07")
    contract = _cx005()

    class DB(SqlExecutor):
        def query(self, sql_template, params=None):
            if params["period"] == 202609:
                return SqlSnapshot(params=params, columns=["年累计产销差率", "产销差率年度目标", "产销差率目标差距"],
                                   rows=[{"年累计产销差率": None, "产销差率年度目标": None, "产销差率目标差距": None}],
                                   row_count=1)
            return SqlSnapshot(params=params, columns=["年累计产销差率", "产销差率年度目标", "产销差率目标差距"],
                               rows=[{"年累计产销差率": 0.0790, "产销差率年度目标": 0.0790, "产销差率目标差距": 0}],
                               row_count=1)

    repo = Repository(tmp_path / "e.db", tmp_path / "runs")
    orch = Orchestrator(repo, [contract], agent_client=client_for(done(CX005_TEXT)),
                        sql_executor=DB(connect=lambda: None), mode="live", enable_watermark=False)
    orch.start_run(ctx)
    detail = repo.load_case_detail(ctx.run_id, "CX-005")
    metrics = {item["metric"]: item for item in detail["result"]["comparison_items"]}
    assert "产销差率年度目标" in metrics
    assert "产销差率目标差距" in metrics
    assert detail["result"]["not_scored_reason"] != "CONTEXT_UNCONFIRMED"
    assert metrics["产销差率年度目标"]["status"] == "WRONG_VALUE"


def test_cx007_parses_arrow_months_and_compares_items():
    ctx = build_run_context("2026-09-07")
    info, _ = align_answer(_cx007(), ctx, CX007_TEXT)
    assert info["effective_period"] == 202608
    assert info["previous_period"] == 202607
    assert info["sql_params"]["period"] == 202608
    assert info["sql_params"]["period_prev"] == 202607
    sql = claims_from_sql_rows(_cx007(), [{"rate": 0.0827, "prev_rate": 0.0798, "mom_change": 0.0029}])
    agent = extract_claims(_cx007(), CX007_TEXT)
    items = compare_claims(_cx007(), sql, agent)
    by_metric = {item.metric: item for item in items if item.status is not ClaimStatus.UNEXPECTED}
    assert set(by_metric) >= {"上期单月产销差率", "单月产销差率环比变化"}
    assert all(item.status is not ClaimStatus.UNPARSEABLE for item in items)


def test_cx003_wan_scale_and_caliber_mismatch():
    agent = extract_claims(_cx003(), CX003_TEXT)
    by_metric = {c.metric: c for c in agent}
    assert abs(by_metric["单月供水量"].value - 1028570000) < 1e-6
    assert abs(by_metric["单月售水量"].value - 947250000) < 1e-6
    assert by_metric["单月供水量"].aggregation == "ytd"
    sql = claims_from_sql_rows(_cx003(), [{"supply": 157439172.49, "sales": 144412263}])
    items = compare_claims(_cx003(), sql, agent)
    assert {item.status for item in items} == {ClaimStatus.CALIBER_MISMATCH}
    assert all(item.note == "单月/累计口径不一致" for item in items)
