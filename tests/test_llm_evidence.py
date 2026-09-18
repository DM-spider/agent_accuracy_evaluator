# -*- coding: utf-8 -*-
import json

from evaluator.llm_evidence import build_evaluation_evidence, stable_json
from evaluator.models import CaseContract, MeasureSpec, SqlSnapshot


def contract():
    return CaseContract(
        case_id="CX-001",
        question="本月环水集团的单月产销差率是多少？",
        result_type="detail",
        numeric_evaluable=True,
        row_key=["组织"],
        measures={
            "产销差率": MeasureSpec(label="产销差率", unit="%", value_scale="ratio01", sql_column="rate"),
            "供水量": MeasureSpec(label="供水量", unit="m³", value_scale="volume", sql_column="supply"),
        },
        parameter_resolver="single_month",
    )


def snapshot():
    return SqlSnapshot(
        sql_template="SELECT org, rate, supply, updated_at FROM t WHERE period=:period",
        params={"period": 202608},
        executed_sql="SELECT org, rate, supply, updated_at FROM t WHERE period=202608",
        columns=["org", "rate", "supply", "updated_at"],
        rows=[
            {"org": "南山分公司", "rate": 0.053, "supply": 10000, "updated_at": "2026-09-01"},
            {"org": "罗湖分公司", "rate": 0.048, "supply": 9000, "updated_at": "2026-09-01"},
        ],
        row_count=2,
        source="live_sql",
    )


def test_evidence_keeps_contract_columns_and_all_rows():
    evidence = build_evaluation_evidence(
        contract(), answer_text="南山产销差率 5.3%", sql_snapshot=snapshot()
    )
    sql = evidence.payload["sql_result"]
    assert sql["columns"] == ["org", "rate", "supply"]
    assert len(sql["rows"]) == 2
    assert sql["rows"][0]["org"] == "南山分公司"


def test_evidence_does_not_leak_secrets_or_sql_text():
    snap = snapshot()
    snap.executed_sql = "postgresql://user:db-password@host/db SELECT token='agent-token'"
    snap.error = "api_key=sk-llm-secret Cookie=SESSION_ID=secret Authorization=Bearer secret"
    snap.watermark_before = None
    alignment = {
        "requested_params": {"period": 202608},
        "reported_periods": [202608],
        "sql_params": {"period": 202608},
        "issues": [],
        "evidence": "Authorization: Bearer secret",
    }
    evidence = build_evaluation_evidence(
        contract(), answer_text="产销差率 5.3%", sql_snapshot=snap, alignment=alignment
    )
    encoded = stable_json(evidence.payload)
    for secret in ("db-password", "agent-token", "sk-llm-secret", "SESSION_ID", "Authorization"):
        assert secret not in encoded
    assert "executed_sql" not in evidence.payload["sql_result"]
    assert "executed_sql" not in evidence.payload


def test_evidence_over_limit_is_flagged_not_truncated():
    evidence = build_evaluation_evidence(
        contract(), answer_text="x" * 100, sql_snapshot=snapshot(), max_input_chars=50
    )
    assert evidence.too_large is True
    assert evidence.input_chars > 50
    assert len(evidence.payload["agent_answer"]) == 100


def test_evidence_hash_is_stable():
    first = build_evaluation_evidence(contract(), answer_text="a", sql_snapshot=snapshot())
    second = build_evaluation_evidence(contract(), answer_text="a", sql_snapshot=snapshot())
    third = build_evaluation_evidence(contract(), answer_text="b", sql_snapshot=snapshot())
    assert first.input_hash == second.input_hash
    assert first.input_hash != third.input_hash
    assert json.loads(stable_json(first.payload)) == first.payload


def test_evidence_contains_contract_and_alignment_fields():
    alignment = {"requested_params": {"period": 202608}, "reported_periods": [202607], "issues": ["PERIOD_FALLBACK_REVIEW"]}
    evidence = build_evaluation_evidence(
        contract(), answer_text="产销差率 5.3%", sql_snapshot=snapshot(), alignment=alignment
    )
    payload = evidence.payload
    assert payload["case_id"] == "CX-001"
    assert payload["contract"]["row_key"] == ["组织"]
    assert payload["contract"]["measures"]["产销差率"]["value_scale"] == "ratio01"
    assert payload["requested_params"] == {"period": 202608}
    assert payload["reported_periods"] == [202607]
    assert payload["alignment_notes"] == ["PERIOD_FALLBACK_REVIEW"]
    assert "产销差率" in payload["aliases"]["metrics"]
