# -*- coding: utf-8 -*-
from evaluator.agent_client import FixtureAgentClient
from evaluator.models import CaseContract, MeasureSpec, RunStatus
from evaluator.orchestrator import Orchestrator
from evaluator.repository import Repository
from evaluator.run_context import build_run_context
from evaluator.sql_executor import SqlExecutor


class BoomSql(SqlExecutor):
    def __init__(self, boom_for):
        super().__init__(connect=lambda: None)
        self.boom_for = boom_for

    def query(self, sql_template, params=None):
        from evaluator.models import SqlSnapshot
        if self.boom_for == "sql":
            return SqlSnapshot(error="SQL_ERROR")
        return SqlSnapshot(rows=[{"产销差率": 5.51}], columns=["产销差率"], row_count=1)


def _contract(case_id, ready=True):
    return CaseContract(
        case_id=case_id,
        question="q",
        result_type="stat",
        numeric_evaluable=True,
        realtime_ready=ready,
        sql_template="SELECT 1",
        sql_source="SELECT 1",
        parameter_resolver="none",
        measures={"产销差率": MeasureSpec(label="产销差率", unit="%", value_scale="percent", sql_column="产销差率")},
    )


def test_mixed_batch_isolation(tmp_path):
    repo = Repository(db_path=tmp_path / "e.db", runs_dir=tmp_path / "runs")
    contracts = [
        _contract("PASS1"),
        _contract("FAIL1"),
        _contract("SQL1"),
        _contract("AGENT1"),
        _contract("NS1", ready=False),
    ]
    golden = {
        "PASS1": {"expected": {"fields": [{"key": "产销差率", "value": 5.51, "unit": "%"}]}},
        "FAIL1": {"expected": {"fields": [{"key": "产销差率", "value": 5.51, "unit": "%"}]}},
        "SQL1": {"expected": {"fields": [{"key": "产销差率", "value": 5.51, "unit": "%"}]}},
        "AGENT1": {"expected": {"fields": [{"key": "产销差率", "value": 5.51, "unit": "%"}]}},
    }
    answers = {"PASS1": "| 指标 | 数值 |\n|---|---|\n| 产销差率 | 5.51% |", "FAIL1": "| 指标 | 数值 |\n|---|---|\n| 产销差率 | 7.51% |"}

    class MixedClient:
        def ask(self, case, run_context):
            if case.case_id == "AGENT1":
                from evaluator.models import AgentAnswer
                return AgentAnswer(case_id=case.case_id, question=case.question, error="timeout")
            return FixtureAgentClient(answers).ask(case, run_context)

    client = MixedClient()
    # AGENT1 missing -> empty answer
    orch = Orchestrator(
        repo,
        contracts,
        golden_cases=golden,
        agent_client=client,
        sql_executor=SqlExecutor(connect=None),
        mode="mock_perfect",
        enable_watermark=False,
        concurrency=2,
    )
    ctx = build_run_context("2026-08-17")
    summary = orch.start_run(ctx, [c.case_id for c in contracts], include_non_numeric=True)
    assert summary.status is RunStatus.COMPLETED
    by = {c["case_id"]: c for c in repo.list_cases(ctx.run_id)}
    assert by["PASS1"]["status"] in {"PASS", "PARTIAL"}
    assert by["FAIL1"]["status"] == "FAIL"
    assert by["NS1"]["status"] == "NOT_SCORED"
    assert by["AGENT1"]["status"] == "NOT_SCORED"
