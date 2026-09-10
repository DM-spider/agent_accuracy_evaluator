# -*- coding: utf-8 -*-
from evaluator.models import CaseContract, MeasureSpec
from evaluator.contract_loader import resolve_contracts_path, validate_contracts


def _c(case_id, **kw):
    base = dict(
        case_id=case_id,
        question="q",
        result_type="stat",
        numeric_evaluable=True,
        sql_template="SELECT 1 AS rate WHERE 1=:period",
        parameter_resolver="single_month",
        realtime_ready=True,
        measures={"产销差率": MeasureSpec(label="产销差率", sql_column="rate", value_scale="percent")},
    )
    base.update(kw)
    return CaseContract(**base)


def test_validate_accepts_non_73_count():
    errors = validate_contracts([_c("CX-001")])
    assert not any("73" in e or "55" in e for e in errors)


def test_validate_requires_unique_ids():
    errors = validate_contracts([_c("CX-001"), _c("CX-001")])
    assert any("重复" in e for e in errors)


def test_detail_requires_row_key():
    c = _c("CX-013", result_type="detail", row_key=[])
    errors = validate_contracts([c])
    assert any("row_key" in e for e in errors)


def test_resolve_v3_path(monkeypatch):
    monkeypatch.setenv("EVAL_CONTRACT_VERSION", "v3")
    assert resolve_contracts_path().name == "contracts_v3.json"
    monkeypatch.setenv("EVAL_CONTRACT_VERSION", "v1")
    assert resolve_contracts_path().name == "contracts.json"


def test_resolve_default_is_v3(monkeypatch):
    monkeypatch.delenv("EVAL_CONTRACT_VERSION", raising=False)
    assert resolve_contracts_path({"app": {}}).name == "contracts_v3.json"
    assert resolve_contracts_path({"app": {"contract_version": "v1"}}).name == "contracts.json"
