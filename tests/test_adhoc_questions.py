# -*- coding: utf-8 -*-
"""临时题：题面匹配、占位基准与正常评测链路。"""
from __future__ import annotations

import json

from tests._fakes import FixtureAgentClient, StubLlmEvaluator, StubSqlExecutor
from evaluator.adhoc import FALLBACK_CASE_ID, FALLBACK_SQL, build_adhoc_contracts, fallback_contract
from evaluator.models import CaseContract, MeasureSpec
from evaluator.orchestrator import Orchestrator
from evaluator.repository import Repository
from evaluator.run_context import build_run_context


def _golden_contract() -> CaseContract:
    return CaseContract(
        case_id="CX-001",
        question="本月环水集团的单月产销差率是多少？",
        result_type="stat",
        numeric_evaluable=True,
        realtime_ready=True,
        sql_template='SELECT 0.0827 AS "产销差率"',
        parameter_resolver="none",
        measures={"产销差率": MeasureSpec(label="产销差率", unit="%", value_scale="ratio01", sql_column="产销差率")},
        scene_big="产销差率与供水售水量",
        category="CX",
    )


def test_matched_question_keeps_golden_sql_and_unmatched_uses_fallback():
    contracts = build_adhoc_contracts(
        ["本月环水集团的单月产销差率是多少？", "一个黄金集里没有的问题"],
        [_golden_contract(), fallback_contract()],
    )
    assert [c.case_id for c in contracts] == ["TMP-001", "TMP-002"]
    assert all(c.scene_big == "临时题" and c.category == "TMP" for c in contracts)
    assert contracts[0].sql_template == 'SELECT 0.0827 AS "产销差率"'
    assert contracts[0].question == "本月环水集团的单月产销差率是多少？"
    assert contracts[1].sql_template == FALLBACK_SQL
    assert contracts[1].measures.keys() == {"未收录占位值"}
    assert contracts[1].parameter_resolver == "none"


def test_duplicate_questions_collapse_and_empty_rejected():
    contracts = build_adhoc_contracts([" 同一个问题 ", "同一个问题"], [fallback_contract()])
    assert len(contracts) == 1
    try:
        build_adhoc_contracts(["   "], [fallback_contract()])
    except ValueError as exc:
        assert "至少" in str(exc)
    else:
        raise AssertionError("空题目应被拒绝")


def test_fallback_question_runs_normal_pipeline(tmp_path):
    repo = Repository(db_path=tmp_path / "e.db", runs_dir=tmp_path / "runs")
    contract = fallback_contract().model_copy(update={"case_id": "TMP-001", "question": "本分公司现在高漏耗小区有哪些？"})
    agent = FixtureAgentClient({"TMP-001": "宝安高漏耗小区共 293 个，其中新增 135 个。"})
    sql = StubSqlExecutor(rows=[{"未收录占位值": -999999}])
    evaluator = StubLlmEvaluator({"TMP-001": {"verdict": "UNQUALIFIED", "issue_code": "WRONG_VALUE"}})
    orchestrator = Orchestrator(
        repo,
        [contract],
        agent_client=agent,
        sql_executor=sql,
        llm_evaluator=evaluator,
        enable_watermark=False,
        concurrency=1,
    )
    ctx = build_run_context("2026-11-11")
    summary = orchestrator.start_run(ctx, ["TMP-001"], include_non_numeric=True)

    cases = {item["case_id"]: item for item in repo.list_cases(ctx.run_id)}
    assert cases["TMP-001"]["status"] == "FAIL"
    assert cases["TMP-001"]["primary_failure"] == "WRONG_VALUE"
    assert summary.total_cases == 1
    detail = repo.load_case_detail(ctx.run_id, "TMP-001")
    snapshot = json.loads((repo.run_dir(ctx.run_id) / "contracts_snapshot.json").read_text(encoding="utf-8"))
    assert snapshot[0]["question"] == "本分公司现在高漏耗小区有哪些？"
    assert detail["agent_answer"]["text"].startswith("宝安高漏耗小区")
    assert detail["sql_snapshot"]["rows"] == [{"未收录占位值": -999999}]
    assert detail["llm_evaluation"]["evaluation"]["overall_verdict"] == "UNQUALIFIED"


def test_fallback_case_is_not_in_default_pool(tmp_path):
    repo = Repository(db_path=tmp_path / "e.db", runs_dir=tmp_path / "runs")
    orchestrator = Orchestrator(
        repo,
        [_golden_contract(), fallback_contract()],
        agent_client=FixtureAgentClient({}),
        sql_executor=StubSqlExecutor(),
        llm_evaluator=None,
        enable_watermark=False,
        concurrency=1,
    )
    assert [c.case_id for c in orchestrator._select_cases(None, True)] == ["CX-001"]
    assert [c.case_id for c in orchestrator._select_cases(None, False)] == ["CX-001"]
    assert [c.case_id for c in orchestrator._select_cases(["TMP-000"], True)] == [FALLBACK_CASE_ID]
