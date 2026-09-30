# -*- coding: utf-8 -*-
from evaluator.contract_loader import load_contracts, validate_contracts
from evaluator.golden_loader import business_verified_ids, case_by_id, load_golden_json, merge_golden


def test_golden_json_is_the_single_source():
    payload = load_golden_json()
    golden = merge_golden()
    total = payload["meta"]["total_cases"]
    assert golden["json_count"] == total
    assert golden["numeric_count"] == total
    assert not golden["meta"].get("failed_cases")
    assert golden["meta"]["time_anchor_base"]


def test_cx001_has_question_sql_and_rate():
    case = case_by_id(merge_golden(), "CX-001")
    assert "产销差率" in case["question"]
    assert "dwd_lsxt_fqcxfx" in (case.get("sql") or "")
    fields = (case.get("expected") or {}).get("fields") or []
    assert [f["key"] for f in fields] == ["产销差率"]
    assert all("tolerance" in f for f in fields)


def test_golden_marks_business_verified_subset():
    verified = business_verified_ids(merge_golden())
    assert len(verified) == 16
    assert {"CX-036", "JL-016", "BJ-012", "DMA-002"} <= verified


def test_contracts_cover_all_cases():
    contracts = load_contracts()
    errors = validate_contracts(contracts)
    assert not any("缺少 SQL" in e or "重复" in e or "不能为空" in e for e in errors)
    assert {c.case_id for c in contracts} == {c["case_id"] for c in load_golden_json()["cases"]}
    cx = next(c for c in contracts if c.case_id == "CX-001")
    assert cx.numeric_evaluable
    assert "产销差率" in cx.measures
    assert ":period" in cx.sql_template
    assert "202606" not in cx.sql_template
    assert cx.realtime_ready
