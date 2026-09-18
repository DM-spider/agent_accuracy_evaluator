# -*- coding: utf-8 -*-
from evaluator.agent_client import FixtureAgentClient
from evaluator.contract_builder import build_contract
from evaluator.evaluation_metrics import run_evaluation_metrics
from evaluator.golden_loader import load_agent_answers, merge_v1_golden
from evaluator.mock_demo import FixtureLlmEvaluator
from evaluator.orchestrator import Orchestrator
from evaluator.repository import Repository
from evaluator.run_context import build_run_context
from evaluator.sql_executor import SqlExecutor


def test_mock_batch_persists_llm_evaluation(tmp_path):
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
        llm_evaluator=FixtureLlmEvaluator({}, default_verdict="QUALIFIED"),
        mode="mock_perfect",
        enable_watermark=False,
    )
    ctx = build_run_context("2026-08-17")
    summary = orch.start_run(ctx, ["CX01", "BJ3"], include_non_numeric=True)
    by = {c["case_id"]: c for c in repo.list_cases(ctx.run_id)}
    assert by["BJ3"]["status"] == "NOT_SCORED"
    assert by["CX01"]["status"] == "PASS"
    assert summary.not_scored_cases >= 1
    detail = repo.load_case_detail(ctx.run_id, "CX01")
    assert detail["llm_evaluation"]["evaluation"]["overall_verdict"] == "QUALIFIED"
    from evaluator.verdict import case_judgment
    assert case_judgment(detail["llm_evaluation"])["final_verdict"] == "QUALIFIED"
    assert "table_comparison" not in detail
    metrics = run_evaluation_metrics(repo, ctx.run_id, contracts)
    assert metrics["numeric"]["qualified_questions"] == 1
    assert metrics["case_judgments"]["CX01"]["auto_source"] == "llm"
