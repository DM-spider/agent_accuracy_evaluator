# -*- coding: utf-8 -*-
from evaluator.contract_loader import load_contracts
from evaluator.evaluation_metrics import run_evaluation_metrics
from evaluator.golden_loader import merge_golden
from evaluator.orchestrator import Orchestrator
from evaluator.repository import Repository
from evaluator.run_context import build_run_context

from tests._fakes import FixtureAgentClient, StubLlmEvaluator, StubSqlExecutor, expected_table, markdown_table


def _answer(case, prose):
    columns, rows = expected_table(case)
    return prose + "\n\n" + markdown_table(columns, rows)


def test_batch_persists_llm_evaluation(tmp_path):
    golden = merge_golden()
    golden_map = {c["case_id"]: c for c in golden["cases"]}
    contracts_by_id = {c.case_id: c for c in load_contracts()}
    contracts = [contracts_by_id[cid] for cid in ("CX-001", "BJ-016")]
    answers = {
        "CX-001": _answer(golden_map["CX-001"], "本月集团单月产销差率如下。"),
        "BJ-016": _answer(golden_map["BJ-016"], "上月漏损预警统计如下。"),
    }
    repo = Repository(db_path=tmp_path / "e.db", runs_dir=tmp_path / "runs")
    orch = Orchestrator(
        repo,
        contracts,
        agent_client=FixtureAgentClient(answers),
        sql_executor=StubSqlExecutor(),
        llm_evaluator=StubLlmEvaluator({}, default_verdict="QUALIFIED"),
        enable_watermark=False,
    )
    ctx = build_run_context("2026-08-17")
    summary = orch.start_run(ctx, ["CX-001", "BJ-016"], include_non_numeric=True)
    by = {c["case_id"]: c for c in repo.list_cases(ctx.run_id)}
    assert by["CX-001"]["status"] == "PASS"
    assert by["BJ-016"]["status"] == "PASS"
    assert summary.pass_cases == 2
    detail = repo.load_case_detail(ctx.run_id, "CX-001")
    assert detail["llm_evaluation"]["evaluation"]["overall_verdict"] == "QUALIFIED"
    from evaluator.verdict import case_judgment
    assert case_judgment(detail["llm_evaluation"])["final_verdict"] == "QUALIFIED"
    assert "table_comparison" not in detail
    metrics = run_evaluation_metrics(repo, ctx.run_id, contracts)
    assert metrics["numeric"]["qualified_questions"] == 2
    assert metrics["case_judgments"]["CX-001"]["auto_source"] == "llm"
