# -*- coding: utf-8 -*-
from evaluator.comparator import compare_claims, normalize_coordinate_value
from evaluator.models import CaseContract, ClaimStatus, CoveragePolicy, MeasureSpec, NumericClaim, Tolerance


def _claim(source, metric, value, **coords):
    return NumericClaim(
        case_id="T",
        source=source,
        metric=metric,
        value=value,
        coordinates=coords,
        raw_value=str(value),
    )


def test_scalar_and_org_and_partial_rows():
    contract = CaseContract(
        case_id="T",
        question="q",
        result_type="detail",
        numeric_evaluable=True,
        row_key=["organization"],
        measures={"rate": MeasureSpec(label="rate", unit="%", value_scale="percent", tolerance=Tolerance(kind="abs", eps=0.01))},
        coverage_policy=CoveragePolicy(mode="all_rows", minimum=1.0),
    )
    sql = [_claim("sql", "rate", 11.84, organization="南山分公司")] + [
        _claim("sql", "rate", 1.0, organization=f"组织{i}") for i in range(12)
    ]
    agent = [_claim("agent", "rate", 11.84, organization="南山分公司")] + [
        _claim("agent", "rate", 1.0, organization=f"组织{i}") for i in range(9)
    ]
    items = compare_claims(contract, sql, agent)
    missing = [i for i in items if i.status is ClaimStatus.MISSING]
    matched = [i for i in items if i.status in {ClaimStatus.MATCH, ClaimStatus.MATCH_WITH_TOLERANCE}]
    assert matched
    assert len(missing) == 3


def test_wrong_value_detected():
    contract = CaseContract(
        case_id="T",
        question="q",
        result_type="stat",
        numeric_evaluable=True,
        measures={"rate": MeasureSpec(label="rate", unit="%", value_scale="percent", tolerance=Tolerance(kind="abs", eps=0.01))},
    )
    items = compare_claims(
        contract,
        [_claim("sql", "rate", 5.51)],
        [_claim("agent", "rate", 7.51)],
    )
    assert items[0].status is ClaimStatus.WRONG_VALUE


def test_period_date_and_percent_calibers_are_normalized():
    assert normalize_coordinate_value("period", "2026-03") == "202603"
    assert normalize_coordinate_value("period", "202603.000000000000000000") == "202603"
    assert normalize_coordinate_value("period", "2026年3月") == "202603"
    assert normalize_coordinate_value("date", "2026-03-01 00:00:00") == "20260301"
    assert normalize_coordinate_value("date", "20260301.000000") == "20260301"

    contract = CaseContract(
        case_id="T", question="q", result_type="detail", numeric_evaluable=True,
        row_key=["period"],
        measures={"rate": MeasureSpec(label="rate", unit="%", value_scale="ratio01")},
    )
    # SQL 存 0-1 比值、智能体答百分数：两侧各自经 parse_number 按契约规范化到同一空间
    from evaluator.normalizer import parse_number
    spec = contract.measures["rate"]
    sql = NumericClaim(case_id="T", source="sql", metric="rate", value=parse_number("0.053", spec, "sql")[0],
                       raw_value="0.053", coordinates={"period": "202603.000000000000000000"})
    agent = NumericClaim(case_id="T", source="agent", metric="rate", value=parse_number("5.3%", spec, "agent")[0],
                         raw_value="5.3%", coordinates={"period": "2026-03"})
    item = compare_claims(contract, [sql], [agent])[0]
    assert item.status is ClaimStatus.MATCH
    assert item.expected_value == item.actual_value == 5.3
