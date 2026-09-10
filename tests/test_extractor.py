# -*- coding: utf-8 -*-
import json
from pathlib import Path

from evaluator.extractor import extract_claims
from evaluator.models import CaseContract, CoveragePolicy, MeasureSpec, Tolerance

FIXTURES = Path(__file__).parent / "fixtures" / "answer_samples.json"


def _stat_contract():
    tol = Tolerance(kind="abs", eps=0.01)
    return CaseContract(
        case_id="CX01",
        question="集团本月产销差率是多少？供水量、售水量各多少？",
        result_type="stat",
        numeric_evaluable=True,
        measures={
            "供水量": MeasureSpec(label="供水量", unit="m³", value_scale="volume", aliases=["供水量"], tolerance=Tolerance(kind="rel", eps=0.001)),
            "售水量": MeasureSpec(label="售水量", unit="m³", value_scale="volume", aliases=["售水量"], tolerance=Tolerance(kind="rel", eps=0.001)),
            "产销差率": MeasureSpec(label="产销差率", unit="%", value_scale="percent", aliases=["产销差率"], tolerance=tol),
        },
        coverage_policy=CoveragePolicy(mode="all_fields", minimum=1.0),
    )


def _detail_contract():
    return CaseContract(
        case_id="CX04",
        question="各分公司产销差目标完成情况如何？",
        result_type="detail",
        numeric_evaluable=True,
        row_key=["organization"],
        dimensions=["organization"],
        dimension_columns={"organization": ["组织", "二级部门"]},
        measures={
            "实际产销差率": MeasureSpec(
                label="实际产销差率",
                aliases=["实际产销差率(%)", "产销差率"],
                unit="%",
                value_scale="percent",
                tolerance=Tolerance(kind="abs", eps=0.01),
            )
        },
    )


def test_extract_stat_and_detail_tables():
    samples = json.loads(FIXTURES.read_text(encoding="utf-8"))
    claims = extract_claims(_stat_contract(), samples["stat_table"])
    by_metric = {c.metric: c for c in claims}
    assert abs(by_metric["供水量"].value - 150559670.20) < 0.1
    assert abs(by_metric["产销差率"].value - 5.51) < 1e-9
    assert by_metric["产销差率"].extractor == "markdown_table"
    assert "5.5100" in by_metric["产销差率"].evidence
    detail = extract_claims(_detail_contract(), samples["detail_table"])
    nanshan = next(c for c in detail if c.coordinates.get("organization") == "南山分公司")
    assert abs(nanshan.value - 11.84) < 1e-9


def test_plain_text_and_negative_entities():
    samples = json.loads(FIXTURES.read_text(encoding="utf-8"))
    claims = extract_claims(_detail_contract(), samples["plain_text"])
    assert claims
    assert abs(claims[0].value - 11.40) < 1e-9
    skipped = extract_claims(_stat_contract(), samples["negative"])
    metrics = {c.metric for c in skipped}
    assert "产销差率" not in metrics or all(c.value not in {15, 3, 100} for c in skipped)


def test_sql_and_agent_tables_pair_by_row_key_not_layout():
    from evaluator.comparator import compare_claims
    from evaluator.extractor import claims_from_sql_rows
    from evaluator.models import ClaimStatus

    contract = CaseContract(
        case_id="CX-013",
        question="列出本月各单位的单月产销差率。",
        result_type="detail",
        numeric_evaluable=True,
        row_key=["组织"],
        measures={"产销差率": MeasureSpec(label="产销差率", sql_column="rate", unit="%", value_scale="ratio01")},
    )
    agent = extract_claims(contract, "| 分公司 | 单月产销差率(%) |\n|---|---|\n| 罗湖 | 12% |\n| 南山 | 7.55% |")
    sql = claims_from_sql_rows(contract, [{"组织": "南山分公司", "rate": 0.0755}, {"组织": "罗湖分公司", "rate": 0.12}])
    items = compare_claims(contract, sql, agent)
    by_org = {tuple(i.coordinates.items())[0][1]: i.status for i in items if i.status is not ClaimStatus.UNEXPECTED}
    assert by_org["南山分公司"] in {ClaimStatus.MATCH, ClaimStatus.MATCH_WITH_TOLERANCE}
    assert by_org["罗湖分公司"] in {ClaimStatus.MATCH, ClaimStatus.MATCH_WITH_TOLERANCE}


def test_projects_agent_headers_onto_sql_schema():
    contract = CaseContract(
        case_id="CX-013",
        question="列出本月各单位的单月产销差率。",
        result_type="detail",
        numeric_evaluable=True,
        row_key=["组织"],
        measures={"产销差率": MeasureSpec(label="产销差率", sql_column="rate", unit="%", value_scale="percent")},
    )
    text = "| 分公司 | 单月产销差率(%) |\n|---|---|\n| 南山 | 7.55% |\n| 罗湖分公司 | 12.00% |"
    claims = extract_claims(contract, text)
    by_org = {c.coordinates.get("组织"): c for c in claims}
    assert set(by_org) == {"南山分公司", "罗湖分公司"}
    assert abs(by_org["南山分公司"].value - 7.55) < 1e-9


def test_llm_requires_evidence():
    contract = _stat_contract()
    text = "供水量看起来不错。"

    def llm(_c, _t):
        return [{"metric": "供水量", "raw_value": "999", "value": 999, "evidence": "999"}]

    claims = extract_claims(contract, text, llm_extractor=llm)
    assert all(c.raw_value != "999" for c in claims)
