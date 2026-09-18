# -*- coding: utf-8 -*-
"""四类根因（期间/范围/粒度/口径）在答案期间对齐与 LLM 结论映射中的行为。"""
import pytest

from evaluator.answer_context import align_answer
from evaluator.llm_evaluation import artifact_from_evaluation
from evaluator.models import (
    REQUIRED_DIMENSIONS,
    CaseContract,
    CaseStatus,
    DimensionEvaluation,
    LlmEvaluationResult,
    MeasureSpec,
    Tolerance,
)
from evaluator.run_context import build_run_context
from evaluator.scorer import result_from_llm_evaluation


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


def test_cx005_adopts_complete_month_and_moves_all_period_params():
    ctx = build_run_context("2026-09-07")
    info, block = align_answer(_cx005(), ctx, CX005_TEXT)
    assert info["requested_period"] == 202609
    assert info["effective_period"] == 202608
    assert info["sql_params"]["period"] == 202608
    assert info["period_source"] == "agent_answer"
    assert "年累计产销差率" in block


def test_cx007_parses_arrow_months_and_previous_period():
    ctx = build_run_context("2026-09-07")
    info, _ = align_answer(_cx007(), ctx, CX007_TEXT)
    assert info["effective_period"] == 202608
    assert info["previous_period"] == 202607
    assert info["sql_params"]["period"] == 202608
    assert info["sql_params"]["period_prev"] == 202607


def _llm_artifact(code, dimension, status="MISMATCH"):
    dimensions = {key: DimensionEvaluation(status="MATCH") for key in REQUIRED_DIMENSIONS}
    dimensions[dimension] = DimensionEvaluation(status=status)
    result = LlmEvaluationResult(
        overall_verdict="UNQUALIFIED",
        confidence=0.9,
        summary=f"{code} 结论",
        primary_issue_code=code,
        issue_codes=[code],
        dimensions=dimensions,
        differences=[],
        needs_human_review=False,
    )
    return artifact_from_evaluation(result, model="m", input_hash="h")


@pytest.mark.parametrize(
    "code,dimension",
    [
        ("PERIOD_MISMATCH", "period"),
        ("SCOPE_MISMATCH", "scope"),
        ("GRAIN_MISMATCH", "grain"),
        ("CALIBER_MISMATCH", "unit_caliber"),
    ],
)
def test_four_root_causes_map_to_unqualified(code, dimension):
    case = result_from_llm_evaluation(_cx005(), _llm_artifact(code, dimension))
    assert case.status is CaseStatus.FAIL
    assert case.primary_failure == code
    assert case.error_types == [code]


def test_target_measure_tolerance_is_kept_in_contract():
    spec = _cx005().measures["产销差率目标差距"]
    assert spec.aggregation == "actual_minus_target"
    assert isinstance(spec.tolerance, Tolerance)
