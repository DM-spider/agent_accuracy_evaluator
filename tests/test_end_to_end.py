# -*- coding: utf-8 -*-
from evaluator.agent_client import FixtureAgentClient
from evaluator.contract_builder import build_contract
from evaluator.extractor import extract_claims, claims_from_expected
from evaluator.golden_loader import load_agent_answers, merge_v1_golden
from evaluator.comparator import compare_claims
from evaluator.models import CaseContract, CaseStatus, ClaimStatus, MeasureSpec, NumericClaim
from evaluator.orchestrator import Orchestrator
from evaluator.repository import Repository
from evaluator.run_context import build_run_context
from evaluator.scorer import score_case
from evaluator.sql_executor import SqlExecutor


def test_perfect_and_errors_regression():
    golden = merge_v1_golden()
    perfect = {c["case_id"]: c.get("final_answer_text") or "" for c in load_agent_answers("agent_answers_perfect.json")["cases"]}
    errors = {c["case_id"]: c.get("final_answer_text") or "" for c in load_agent_answers("agent_answers_errors.json")["cases"]}
    cases = {c["case_id"]: c for c in golden["cases"]}
    sample_ids = [cid for cid in ["CX01", "CX02", "CX04", "CX05"] if cid in perfect]
    perfect_wrong = []
    error_missed = []
    for cid in sample_ids:
        contract = build_contract(cases[cid])
        sql_claims = claims_from_expected(contract, cases[cid])
        p_items = compare_claims(contract, sql_claims, extract_claims(contract, perfect[cid]))
        if any(i.status is ClaimStatus.WRONG_VALUE for i in p_items):
            perfect_wrong.append(cid)
        e_items = compare_claims(contract, sql_claims, extract_claims(contract, errors[cid]))
        planted = any(i.status in {ClaimStatus.WRONG_VALUE, ClaimStatus.UNEXPECTED} for i in e_items)
        if not planted and errors[cid] != perfect[cid]:
            error_missed.append(cid)
        p_result = score_case(contract, p_items)
        assert p_result.status is not CaseStatus.FAIL
    assert not perfect_wrong
    assert not error_missed


def test_mock_batch_persists(tmp_path):
    golden = merge_v1_golden()
    contracts = [build_contract(c) for c in golden["cases"] if c["case_id"] in {"CX01", "BJ3"}]
    answers = {c["case_id"]: c.get("final_answer_text") or "" for c in load_agent_answers("agent_answers_perfect.json")["cases"]}
    repo = Repository(db_path=tmp_path / "e.db", runs_dir=tmp_path / "runs")
    orch = Orchestrator(
        repo,
        contracts,
        golden_cases={c["case_id"]: c for c in golden["cases"]},
        agent_client=FixtureAgentClient(answers),
        sql_executor=SqlExecutor(connect=None),
        mode="mock_perfect",
        enable_watermark=False,
    )
    ctx = build_run_context("2026-08-17")
    summary = orch.start_run(ctx, ["CX01", "BJ3"], include_non_numeric=True)
    by = {c["case_id"]: c for c in repo.list_cases(ctx.run_id)}
    assert by["BJ3"]["status"] == "NOT_SCORED"
    assert by["CX01"]["status"] in {"PASS", "PARTIAL", "FAIL", "REVIEW"}
    assert summary.not_scored_cases >= 1


def test_v3_stat_ratio01_scale_matches():
    from evaluator.normalizer import parse_number
    contract = CaseContract(
        case_id="CX-001",
        question="本月环水集团的单月产销差率是多少？",
        result_type="stat",
        numeric_evaluable=True,
        measures={"产销差率": MeasureSpec(label="产销差率", sql_column="rate", value_scale="ratio01", unit="%")},
    )
    spec = contract.measures["产销差率"]
    sql_claims = [NumericClaim(case_id="CX-001", source="sql", metric="产销差率",
                               raw_value="0.0755", value=parse_number("0.0755", spec, "sql")[0])]
    agent_claims = [NumericClaim(case_id="CX-001", source="agent", metric="产销差率",
                                 raw_value="7.55%", value=parse_number("7.55%", spec, "agent")[0], unit="%")]
    items = compare_claims(contract, sql_claims, agent_claims)
    assert items
    assert items[0].status in {ClaimStatus.MATCH, ClaimStatus.MATCH_WITH_TOLERANCE}
