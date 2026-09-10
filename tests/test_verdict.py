from evaluator.models import CaseContract, CoveragePolicy, MeasureSpec
from evaluator.verdict import (
    PARTIAL,
    QUALIFIED,
    UNEVALUABLE,
    UNQUALIFIED,
    case_judgment,
    classify_comparison,
    consistency_from_items,
    parse_threshold,
)


def detail_contract():
    return CaseContract(
        case_id="CX-013",
        question="列出各单位产销差率",
        result_type="detail",
        numeric_evaluable=True,
        row_key=["组织"],
        measures={"产销差率": MeasureSpec(label="产销差率", sql_column="rate")},
        coverage_policy=CoveragePolicy(mode="all_rows", minimum=1.0),
    )


def test_parse_threshold_percent_and_ratio():
    assert parse_threshold(None) == 1.0
    assert parse_threshold(0.6) == 0.6
    assert parse_threshold(60) == 0.6
    assert parse_threshold("60%") == 0.6
    assert parse_threshold(1) == 1.0


def test_consistency_counts_match_wrong_and_gaps():
    items = [
        {"status": "MATCH"},
        {"status": "MATCH_WITH_TOLERANCE"},
        {"status": "WRONG_VALUE"},
        {"status": "MISSING"},
        {"status": "NO_BENCHMARK"},
    ]
    assert consistency_from_items(items) == 0.5


def test_wrong_values_are_unqualified_without_threshold():
    items = [{"status": "MATCH"}] * 3 + [{"status": "WRONG_VALUE"}] * 2
    judgment = case_judgment(items, 0.6)
    assert judgment["consistency"] == 0.6
    assert judgment["auto_verdict"] == UNQUALIFIED
    assert judgment["final_verdict"] == UNQUALIFIED
    assert judgment["reason"] == "WRONG_VALUE"


def test_full_match_with_extra_summary_is_qualified():
    items = [
        {"status": "MATCH", "coordinates": {"组织": "盐田分公司"}},
        {"status": "MATCH", "coordinates": {"组织": "罗湖分公司"}},
        {"status": "NO_BENCHMARK", "coordinates": {}, "metric": "平均产销差率"},
    ]
    judgment = case_judgment(items, contract=detail_contract())
    assert judgment["auto_verdict"] == QUALIFIED
    assert judgment["qualified"] is True
    assert judgment["grain_mismatch"] is False


def test_same_grain_missing_rows_are_partial():
    items = [
        {"status": "MATCH", "coordinates": {"组织": "盐田分公司"}},
        {"status": "MISSING", "coordinates": {"组织": "罗湖分公司"}},
        {"status": "MISSING", "coordinates": {"组织": "福田分公司"}},
    ]
    judgment = case_judgment(items, contract=detail_contract())
    assert judgment["auto_verdict"] == PARTIAL
    assert judgment["qualified"] is False
    assert judgment["evaluable"] is True
    assert judgment["reason"] == "MISSING"


def test_detail_only_summary_is_grain_mismatch():
    items = [
        {"status": "MISSING", "coordinates": {"组织": "盐田分公司"}},
        {"status": "MISSING", "coordinates": {"组织": "罗湖分公司"}},
        {"status": "NO_BENCHMARK", "coordinates": {}, "metric": "平均产销差率"},
    ]
    classified = classify_comparison(items, detail_contract())
    assert classified["grain_mismatch"] is True
    judgment = case_judgment(items, contract=detail_contract())
    assert judgment["auto_verdict"] == UNQUALIFIED
    assert judgment["reason"] == "GRAIN_MISMATCH"


def test_no_extracted_numbers_are_unevaluable():
    items = [
        {"status": "FIELD_UNRECOGNIZED", "coordinates": {"组织": "盐田分公司"}},
        {"status": "MISSING", "coordinates": {"组织": "罗湖分公司"}},
    ]
    judgment = case_judgment(items, contract=detail_contract())
    assert judgment["auto_verdict"] == UNEVALUABLE
    assert judgment["reason"] == "EXTRACT_FAIL"


def test_manual_override_changes_unevaluable():
    judgment = case_judgment([], 0.6, "合格")
    assert judgment["auto_verdict"] == UNEVALUABLE
    assert judgment["manual_verdict"] == QUALIFIED
    assert judgment["final_verdict"] == QUALIFIED
    assert judgment["evaluable"] is True


def test_manual_unqualified_overrides_auto_pass():
    items = [{"status": "MATCH"}]
    judgment = case_judgment(items, 0.6, "UNQUALIFIED")
    assert judgment["auto_verdict"] == QUALIFIED
    assert judgment["final_verdict"] == UNQUALIFIED


def test_manual_unevaluable_overrides_auto_pass():
    items = [{"status": "MATCH"}]
    judgment = case_judgment(items, 0.6, "无法评估")
    assert judgment["final_verdict"] == UNEVALUABLE
    assert judgment["evaluable"] is False
