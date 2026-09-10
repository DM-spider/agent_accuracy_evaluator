# -*- coding: utf-8 -*-
from evaluator.contract_loader import CONTRACTS_PATH, generate_contracts, load_contracts, validate_contracts
from evaluator.golden_loader import case_by_id, load_excel_rows, load_golden_json, merge_golden, merge_v1_golden


def test_excel_reads_v3_questions():
    rows = load_excel_rows()
    assert len(rows) == 125
    ids = [r["case_id"] for r in rows]
    assert "CX-001" in ids
    assert "BJ-015" in ids


def test_json_numeric_count_matches_metadata():
    payload = load_golden_json()
    assert payload["meta"]["total_cases"] == 125
    golden = merge_golden()
    assert golden["excel_count"] == 125
    assert golden["json_count"] == 125
    assert golden["numeric_count"] == 125


def test_flat_v1_golden_dataset_is_available():
    golden = merge_v1_golden()
    assert golden["excel_count"] == 73
    assert golden["json_count"] == 73
    assert any(case["case_id"] == "CX01" for case in golden["cases"])


def test_cx001_has_question_sql_and_rate():
    case = case_by_id(merge_golden(), "CX-001")
    assert "产销差率" in case["question"]
    assert "dwd_lsxt_fqcxfx" in (case.get("sql") or "")
    fields = (case.get("expected") or {}).get("fields") or []
    assert [f["key"] for f in fields] == ["产销差率"]
    assert all("tolerance" in f for f in fields)


def test_contracts_cover_numeric_cases(tmp_path):
    dest = tmp_path / "contracts.json"
    contracts = generate_contracts(dest=dest)
    errors = validate_contracts(contracts)
    assert not any("重复" in e for e in errors)
    loaded = load_contracts(dest)
    v1 = load_contracts(CONTRACTS_PATH)
    assert any(c.case_id == "CX01" for c in v1)
    cx = next(c for c in loaded if c.case_id == "CX-001")
    assert cx.numeric_evaluable
    assert "产销差率" in cx.measures
    assert ":period" in cx.sql_template
    assert "202606" not in cx.sql_template
    assert cx.realtime_ready
