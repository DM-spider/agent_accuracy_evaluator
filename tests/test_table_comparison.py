from copy import deepcopy

from evaluator.contract_loader import CONTRACTS_PATH, load_contracts
from evaluator.table_comparison import table_comparison

V1_CONTRACTS = load_contracts(CONTRACTS_PATH)


def fixture_detail():
    return {"result": {"status": "REVIEW", "comparison_items": []},
            "agent_answer": {"text": "| 分公司 | 完成率 |\n|---|---|\n| **盐田分公司** | 68% |\n\n完成率：999%"},
            "sql_rechecks": [{"params": {"period": 202607}, "rows": [{"二级部门": "盐田分公司", "完成率(%)": 147.64}]}]}


def test_review_compares_table_not_prose_without_changing_score():
    contract = next(c for c in V1_CONTRACTS if c.case_id == "CX07")
    detail = fixture_detail()
    before = deepcopy(detail)
    reference = table_comparison(contract, detail)
    assert detail == before
    assert reference["reference_only"] and reference["source"] == "recheck"
    assert len(reference["items"]) == 1
    row = reference["items"][0]
    assert row["actual_value"] == 68 and row["expected_value"] == 147.64
    assert row["status"] == "WRONG_VALUE"


def test_duplicate_coordinates_are_not_silently_compared():
    contract = next(c for c in V1_CONTRACTS if c.case_id == "CX07")
    detail = fixture_detail()
    detail["agent_answer"]["text"] = "| 分公司 | 完成率 |\n|---|---|\n| 盐田分公司 | 68% |\n| 盐田分公司 | 88% |"
    reference = table_comparison(contract, detail)
    assert not reference["items"]
    assert any("重复" in issue for issue in reference["issues"])


def test_failed_recheck_is_not_empty_benchmark():
    contract = next(c for c in V1_CONTRACTS if c.case_id == "CX07")
    detail = fixture_detail()
    detail["sql_rechecks"][0]["error"] = "timeout"
    assert not table_comparison(contract, detail)["items"]


def target_fixture(intro="", header="年累计(截至7月)"):
    return {"agent_answer": {"text": intro + "\n| 分公司 | 目标值 | 本月实际(8月) | 月差距 | " + header + " | 累计差距 |\n|---|---|---|---|---|---|\n| **盐田分公司** | 7.2% | 17.39% | +10.19pp | 10.63% | +3.43pp |"},
            "sql_rechecks": [{"params": {"period": 202607}, "sql_template": "SELECT * FROM rates WHERE periodtype='SzwgBusinessYear'",
                              "rows": [{"二级部门": "盐田分公司", "目标产销差率(%)": 7.2, "实际产销差率(%)": 10.63, "差距(pp)": -3.43}]}]}


def test_target_fields_and_periods_are_not_conflated():
    contract = next(c for c in V1_CONTRACTS if c.case_id == "CX04")
    data = target_fixture()
    before = deepcopy(data)
    result = table_comparison(contract, data)
    rows = {r["metric"]: r for r in result["items"]}
    assert rows["累计产销差率"]["status"] == "VALUE_MATCH_REVIEW"
    assert rows["年度目标"]["status"] == "VALUE_MATCH_REVIEW"
    assert rows["单月产销差率"]["status"] == "NO_BENCHMARK"
    assert rows["单月产销差率"]["expected_value"] is None
    assert rows["累计差距"]["status"] == "CALIBER_REVIEW"
    assert data == before


def test_explicit_year_and_period_conflicts():
    contract = next(c for c in V1_CONTRACTS if c.case_id == "CX04")
    for year, month, expected in [(2026, 7, "MATCH"), (2025, 7, "PERIOD_REVIEW"), (2026, 8, "PERIOD_REVIEW")]:
        rows = table_comparison(contract, target_fixture(f"{year}年", f"年累计(截至{month}月)"))["items"]
        assert next(r for r in rows if r["metric"] == "累计产销差率")["status"] == expected


def test_unrecognized_header_is_not_missing_answer():
    contract = next(c for c in V1_CONTRACTS if c.case_id == "CX04")
    rows = table_comparison(contract, target_fixture(header="业务数值"))["items"]
    assert next(r for r in rows if r["metric"] == "累计产销差率")["status"] == "FIELD_UNRECOGNIZED"


def test_single_contract_metric_accepts_safe_aggregation_decoration():
    contract = next(c for c in V1_CONTRACTS if c.case_id == "CX02")
    detail = {
        "result": {"alignment": {"reported_periods": [202603, 202604]}},
        "agent_answer": {"text": "| 月份 | 产销差率 |\n|---|---|\n| 3月 | 8.1% |\n| 4月 | 8.2% |"},
        "sql_rechecks": [{"params": {"period_0": 202603, "period_1": 202604}, "rows": [
            {"ym": 202603, "cum_rate": 8.1}, {"ym": 202604, "cum_rate": 8.2},
        ]}],
    }
    result = table_comparison(contract, detail)
    assert result["extraction"] == {
        "agent_claims": 2, "sql_claims": 2, "paired": 2,
        "agent_unmatched": 0, "sql_unmatched": 0,
    }
    assert {row["status"] for row in result["items"]} == {"MATCH"}
    assert {row["agent_period"] for row in result["items"]} == {"202603", "202604"}


def test_unmatched_rows_explain_which_side_failed():
    contract = next(c for c in V1_CONTRACTS if c.case_id == "CX07")
    detail = fixture_detail()
    detail["sql_rechecks"][0]["rows"].append({"二级部门": "罗湖分公司", "完成率(%)": 88})
    result = table_comparison(contract, detail)
    unmatched = next(row for row in result["items"] if row["coordinates"]["organization"] == "罗湖分公司")
    assert unmatched["mapping_state"] == "AGENT_UNMATCHED"
    assert "智能体未提取" in unmatched["mapping_reason"]


def test_empty_saved_claims_still_compare_cx02():
    contract = next(c for c in V1_CONTRACTS if c.case_id == "CX02")
    detail = {
        "agent_claims": [],
        "sql_claims": [],
        "agent_answer": {"text": "| 月份 | 产销差率 |\n|---|---|\n| 2026-03 | 35.34% |\n| 2026-04 | 6.30% |"},
        "sql_snapshot": {"columns": ["ym", "cum_rate"], "rows": [
            {"ym": "202603.000000", "cum_rate": 6.85}, {"ym": "202604.000000", "cum_rate": 6.59},
        ]},
    }
    result = table_comparison(contract, detail)
    assert result["extraction"]["paired"] == 2
    assert {row["status"] for row in result["items"]} == {"WRONG_VALUE"}


def test_period_decimal_and_decorated_organization_are_normalized():
    cx02 = next(c for c in V1_CONTRACTS if c.case_id == "CX02")
    period_detail = {
        "agent_answer": {"text": "| 月份 | 产销差率 |\n|---|---|\n| 3月 | 8.1% |"},
        "sql_rechecks": [{"params": {}, "rows": [{"ym": "202603.000000", "cum_rate": 8.1}]}],
    }
    period_result = table_comparison(cx02, period_detail)
    assert period_result["extraction"]["paired"] == 1
    period_alignment = next(row for row in period_result["caliber_alignment"]["rows"] if row["kind"] == "dimension")
    assert period_alignment["agent_raw"] == "3月"
    assert period_alignment["sql_raw"] == "202603.000000"
    assert period_alignment["agent_normalized"] == period_alignment["sql_normalized"] == "202603"
    assert period_alignment["canonical_unit"] == "YYYYMM"
    assert "统一为YYYYMM" in period_alignment["normalization_rule"]

    cx07 = next(c for c in V1_CONTRACTS if c.case_id == "CX07")
    org_detail = {
        "agent_answer": {"text": "| 分公司 | 完成率 |\n|---|---|\n| 🔴 **深汕水务集团** ⚠️ | 88% |"},
        "sql_rechecks": [{"rows": [{"二级部门": "深汕水务", "完成率(%)": 88}]}],
    }
    assert table_comparison(cx07, org_detail)["extraction"]["paired"] == 1
