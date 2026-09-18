# -*- coding: utf-8 -*-
import json

from evaluator.agent_client import FixtureAgentClient
from evaluator.llm_evaluation import artifact_from_evaluation, unevaluable_artifact
from evaluator.models import (
    REQUIRED_DIMENSIONS,
    CaseContract,
    DimensionEvaluation,
    EvaluationDifference,
    LlmEvaluationResult,
    MeasureSpec,
    RunStatus,
    SqlSnapshot,
)
from evaluator.orchestrator import Orchestrator
from evaluator.repository import Repository
from evaluator.run_context import build_run_context
from evaluator.sql_executor import SqlExecutor


class FakeLlmEvaluator:
    """测试替身：按预设结论返回结构化评估，并记录调用。"""

    model = "fake-llm"
    prompt_version = "fake-v1"
    max_input_chars = 500000

    def __init__(self, verdicts=None, default="QUALIFIED"):
        self.verdicts = verdicts or {}
        self.default = default
        self.calls = []

    def evaluate(self, evidence, contract=None):
        case_id = evidence.payload["case_id"]
        self.calls.append(case_id)
        spec = self.verdicts.get(case_id) or {"verdict": self.default}
        verdict = spec.get("verdict", self.default)
        codes = list(spec.get("issue_codes") or ([] if verdict == "QUALIFIED" else ["WRONG_VALUE"]))
        dimensions = {key: DimensionEvaluation(status="MATCH") for key in REQUIRED_DIMENSIONS}
        status = "MISMATCH" if verdict == "UNQUALIFIED" else "PARTIAL" if verdict == "PARTIAL" else "MATCH"
        if codes:
            dimensions["numeric_accuracy"] = DimensionEvaluation(status=status, reason=spec.get("summary") or "")
        result = LlmEvaluationResult(
            overall_verdict=verdict,
            confidence=0.99 if verdict != "UNEVALUABLE" else 0.1,
            summary=spec.get("summary") or "fake summary",
            primary_issue_code=codes[0] if codes else None,
            issue_codes=codes,
            dimensions=dimensions,
            differences=[
                EvaluationDifference(type=code, severity="ERROR", field="产销差率", agent_value="7.51", sql_value="5.51")
                for code in codes
            ] if verdict == "UNQUALIFIED" else [],
            needs_human_review=False,
        )
        return artifact_from_evaluation(
            result, provider="fake", model=self.model, prompt_version=self.prompt_version,
            input_hash=evidence.input_hash,
        )


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


class BoomSql(SqlExecutor):
    def __init__(self, boom=False):
        super().__init__(connect=lambda: None)
        self.boom = boom

    def query(self, sql_template, params=None):
        if self.boom:
            return SqlSnapshot(error="SQL_ERROR")
        return SqlSnapshot(rows=[{"产销差率": 5.51}], columns=["产销差率"], row_count=1)


def _golden():
    return {
        case_id: {"expected": {"fields": [{"key": "产销差率", "value": 5.51, "unit": "%"}]}}
        for case_id in ["PASS1", "FAIL1", "SQL1", "AGENT1", "WM1"]
    }


def _answers():
    return {
        "PASS1": "| 指标 | 数值 |\n|---|---|\n| 产销差率 | 5.51% |",
        "FAIL1": "| 指标 | 数值 |\n|---|---|\n| 产销差率 | 7.51% |",
    }


class MixedClient:
    def ask(self, case, run_context):
        if case.case_id == "AGENT1":
            from evaluator.models import AgentAnswer
            return AgentAnswer(case_id=case.case_id, question=case.question, error="timeout")
        return FixtureAgentClient(_answers()).ask(case, run_context)


def test_success_calls_llm_once_and_maps_verdict(tmp_path):
    repo = Repository(db_path=tmp_path / "e.db", runs_dir=tmp_path / "runs")
    contracts = [_contract("PASS1"), _contract("FAIL1")]
    evaluator = FakeLlmEvaluator({"FAIL1": {"verdict": "UNQUALIFIED", "issue_codes": ["WRONG_VALUE"]}})
    orch = Orchestrator(
        repo, contracts, golden_cases=_golden(), agent_client=FixtureAgentClient(_answers()),
        sql_executor=SqlExecutor(connect=None), llm_evaluator=evaluator,
        mode="mock_perfect", enable_watermark=False, concurrency=1,
    )
    ctx = build_run_context("2026-08-17")
    summary = orch.start_run(ctx, ["PASS1", "FAIL1"], include_non_numeric=True)
    assert summary.status is RunStatus.COMPLETED
    by = {c["case_id"]: c for c in repo.list_cases(ctx.run_id)}
    assert by["PASS1"]["status"] == "PASS"
    assert by["FAIL1"]["status"] == "FAIL"
    assert by["FAIL1"]["primary_failure"] == "WRONG_VALUE"
    assert sorted(evaluator.calls) == ["FAIL1", "PASS1"]
    run_dir = repo.run_dir(ctx.run_id) / "cases"
    assert (run_dir / "PASS1" / "llm_evaluation.json").exists()
    assert json.loads((run_dir / "PASS1" / "llm_evaluation.json").read_text(encoding="utf-8"))["evaluation"]["overall_verdict"] == "QUALIFIED"
    assert not (run_dir / "PASS1" / "agent_claims.json").exists()
    assert not (run_dir / "PASS1" / "sql_claims.json").exists()
    assert summary.timing_summary["completed_agent_count"] == 2
    progress = orch.get_progress(ctx.run_id)
    assert progress["done"] == 2 and progress["status"] == "COMPLETED"


def test_sql_fail_and_agent_fail_skip_llm(tmp_path):
    repo = Repository(db_path=tmp_path / "e.db", runs_dir=tmp_path / "runs")
    contracts = [_contract("SQL1"), _contract("AGENT1")]
    evaluator = FakeLlmEvaluator()
    orch = Orchestrator(
        repo, contracts, golden_cases=_golden(), agent_client=MixedClient(),
        sql_executor=BoomSql(boom=True), llm_evaluator=evaluator,
        mode="live", enable_watermark=False, concurrency=1,
    )
    ctx = build_run_context("2026-08-17")
    orch.start_run(ctx, ["SQL1", "AGENT1"], include_non_numeric=True)
    by = {c["case_id"]: c for c in repo.list_cases(ctx.run_id)}
    assert by["SQL1"]["status"] == "NOT_SCORED"
    assert by["AGENT1"]["status"] == "NOT_SCORED"
    assert evaluator.calls == []
    case_dir = repo.run_dir(ctx.run_id) / "cases"
    assert not (case_dir / "SQL1" / "llm_evaluation.json").exists()
    assert not (case_dir / "AGENT1" / "llm_evaluation.json").exists()


def test_watermark_change_skips_llm(tmp_path, monkeypatch):
    import evaluator.orchestrator as orchestrator_mod
    monkeypatch.setattr(orchestrator_mod, "watermark_changed", lambda before, after: True)
    repo = Repository(db_path=tmp_path / "e.db", runs_dir=tmp_path / "runs")
    contract = _contract("WM1")
    evaluator = FakeLlmEvaluator()
    orch = Orchestrator(
        repo, [contract], golden_cases=_golden(), agent_client=FixtureAgentClient(_answers()),
        sql_executor=BoomSql(boom=False), llm_evaluator=evaluator,
        mode="live", enable_watermark=True, concurrency=1,
    )
    ctx = build_run_context("2026-08-17")
    orch.start_run(ctx, ["WM1"], include_non_numeric=True)
    by = {c["case_id"]: c for c in repo.list_cases(ctx.run_id)}
    assert by["WM1"]["status"] == "NOT_SCORED"
    assert by["WM1"]["not_scored_reason"] == "WATERMARK_CHANGED"
    assert evaluator.calls == []


def test_llm_failure_saves_unevaluable_without_rule_fallback(tmp_path):
    class FailingEvaluator(FakeLlmEvaluator):
        def evaluate(self, evidence, contract=None):
            self.calls.append(evidence.payload["case_id"])
            return unevaluable_artifact(
                "LLM_CALL_FAILED", "调用失败", input_hash=evidence.input_hash, error="timeout; retries=2"
            )

    repo = Repository(db_path=tmp_path / "e.db", runs_dir=tmp_path / "runs")
    contract = _contract("PASS1")
    orch = Orchestrator(
        repo, [contract], golden_cases=_golden(), agent_client=FixtureAgentClient(_answers()),
        sql_executor=SqlExecutor(connect=None), llm_evaluator=FailingEvaluator(),
        mode="mock_perfect", enable_watermark=False, concurrency=1,
    )
    ctx = build_run_context("2026-08-17")
    orch.start_run(ctx, ["PASS1"], include_non_numeric=True)
    by = {c["case_id"]: c for c in repo.list_cases(ctx.run_id)}
    assert by["PASS1"]["status"] == "REVIEW"
    assert by["PASS1"]["primary_failure"] == "LLM_CALL_FAILED"
    detail = repo.load_case_detail(ctx.run_id, "PASS1")
    assert detail["llm_evaluation"]["evaluation"]["overall_verdict"] == "UNEVALUABLE"
    assert detail["llm_evaluation"]["error"] == "timeout; retries=2"


def test_missing_evaluator_is_unevaluable(tmp_path):
    repo = Repository(db_path=tmp_path / "e.db", runs_dir=tmp_path / "runs")
    contract = _contract("PASS1")
    orch = Orchestrator(
        repo, [contract], golden_cases=_golden(), agent_client=FixtureAgentClient(_answers()),
        sql_executor=SqlExecutor(connect=None), llm_evaluator=None,
        mode="mock_perfect", enable_watermark=False, concurrency=1,
    )
    ctx = build_run_context("2026-08-17")
    orch.start_run(ctx, ["PASS1"], include_non_numeric=True)
    assert repo.list_cases(ctx.run_id)[0]["status"] == "REVIEW"


def test_same_input_hash_reuses_saved_evaluation(tmp_path):
    repo = Repository(db_path=tmp_path / "e.db", runs_dir=tmp_path / "runs")
    contract = _contract("PASS1")
    first = FakeLlmEvaluator()
    ctx = build_run_context("2026-08-17")
    Orchestrator(
        repo, [contract], golden_cases=_golden(), agent_client=FixtureAgentClient(_answers()),
        sql_executor=SqlExecutor(connect=None), llm_evaluator=first,
        mode="mock_perfect", enable_watermark=False, concurrency=1,
    ).start_run(ctx, ["PASS1"], include_non_numeric=True)
    assert first.calls == ["PASS1"]
    second = FakeLlmEvaluator()
    Orchestrator(
        repo, [contract], golden_cases=_golden(), agent_client=FixtureAgentClient(_answers()),
        sql_executor=SqlExecutor(connect=None), llm_evaluator=second,
        mode="mock_perfect", enable_watermark=False, concurrency=1,
    ).start_run(ctx, ["PASS1"], include_non_numeric=True)
    assert second.calls == []
