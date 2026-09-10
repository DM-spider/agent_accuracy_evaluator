# -*- coding: utf-8 -*-
"""同一 RunContext 下并发执行智能体、SQL、抽取、比较、评分。"""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Sequence

from evaluator.comparator import compare_claims
from evaluator.answer_context import align_answer, params_for_period
from evaluator.extractor import claims_from_expected, claims_from_sql_rows, expected_table, extract_claims
from evaluator.models import (
    AgentAnswer,
    CaseContract,
    CaseResult,
    CaseStatus,
    RunContext,
    RunStatus,
    RunSummary,
    SqlSnapshot,
)
from evaluator.repository import Repository
from evaluator.run_context import params_for_resolver, template_params, ym_shift
from evaluator.scorer import score_case, score_run
from evaluator.sql_executor import SqlExecutor
from evaluator.watermark import capture_watermark, tables_in_sql, watermark_changed


def _now() -> str:
    return datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds")


def _agent_raw(answer: Optional[AgentAnswer]) -> Optional[Dict[str, Any]]:
    if answer is None:
        return None
    return {
        "http": {
            "method": "POST",
            "url": answer.http_url,
            "status": answer.http_status,
        },
        "request": answer.request_body,
        "response": answer.response_body,
    }


def _empty_sql(contract: CaseContract, snapshot: SqlSnapshot) -> bool:
    if snapshot.error or not snapshot.rows:
        return True
    claims = claims_from_sql_rows(contract, snapshot.rows)
    required = {metric for metric, spec in contract.measures.items() if spec.required}
    if not required:
        return False
    returned = {claim.metric for claim in claims if claim.value is not None}
    return not required.issubset(returned)


def _fallback_sql(executor, contract, run_context, params, snapshot, alignment):
    if snapshot.error or not _empty_sql(contract, snapshot) or not isinstance(params.get("period"), int):
        return snapshot, params
    original = snapshot
    for offset in range(1, 4):
        candidate_period = ym_shift(params["period"], -offset)
        candidate = template_params(contract.sql_template, params_for_period(contract, run_context, candidate_period))
        probe = executor.query(contract.sql_template, candidate)
        alignment.setdefault("sql_attempts", []).append({
            "params": candidate,
            "status": "error" if probe.error else "empty" if _empty_sql(contract, probe) else "ok",
            "row_count": probe.row_count,
        })
        if not probe.error and not _empty_sql(contract, probe):
            alignment["sql_params"] = candidate
            alignment["period_source"] = "database_fallback"
            alignment["period_confidence"] = "inferred"
            alignment["period_changed"] = True
            alignment.setdefault("issues", []).append("DATABASE_PERIOD_FALLBACK")
            return probe, candidate
    return original, params


class Orchestrator:
    def __init__(
        self,
        repo: Repository,
        contracts: Sequence[CaseContract],
        golden_cases: Optional[Dict[str, Dict]] = None,
        agent_client: Any = None,
        sql_executor: Optional[SqlExecutor] = None,
        *,
        concurrency: int = 3,
        mode: str = "live",
        enable_watermark: bool = True,
        timestamp_columns: Optional[List[str]] = None,
    ):
        self.repo = repo
        self.contracts = list(contracts)
        self.contracts_by_id = {c.case_id: c for c in self.contracts}
        self.golden_cases = golden_cases or {}
        self.agent_client = agent_client
        self.sql_executor = sql_executor
        self.concurrency = max(1, concurrency)
        self.mode = mode
        self.enable_watermark = enable_watermark
        self.timestamp_columns = timestamp_columns
        self._lock = threading.Lock()
        self.progress: Dict[str, Dict[str, Any]] = {}
        self.session_blocked = False

    def start_run(
        self,
        run_context: RunContext,
        case_ids: Optional[Sequence[str]] = None,
        *,
        include_non_numeric: bool = True,
    ) -> RunSummary:
        selected = self._select_cases(case_ids, include_non_numeric)
        summary = RunSummary(
            run_id=run_context.run_id,
            status=RunStatus.RUNNING,
            started_at=_now(),
            anchor_time=run_context.anchor_time.isoformat(),
            timezone=run_context.timezone,
            agent_name=run_context.agent_name,
            contract_version=run_context.contract_version,
            total_cases=len(selected),
            progress_done=0,
            progress_total=len(selected),
            mode=self.mode,
            consistency_threshold=run_context.consistency_threshold,
        )
        self.repo.create_run(summary)
        self.repo.write_json(run_context.run_id, "run_context.json", run_context.model_dump(mode="json"))
        self.repo.write_json(
            run_context.run_id,
            "contracts_snapshot.json",
            [c.model_dump(mode="json") for c in selected],
        )
        with self._lock:
            self.progress[run_context.run_id] = {
                "done": 0,
                "total": len(selected),
                "status": "RUNNING",
                "current": None,
                "current_index": None,
                "current_question": None,
                "queue": [c.case_id for c in selected],
                "finished": [],
                "sequential": bool(getattr(self.agent_client, "sequential", False)),
            }
        results: List[CaseResult] = []
        started_at = summary.started_at
        pending = [c.case_id for c in selected]
        try:
            if getattr(self.agent_client, "sequential", False):
                for turn, contract in enumerate(selected, 1):
                    with self._lock:
                        prog = self.progress[run_context.run_id]
                        prog["current"] = contract.case_id
                        prog["current_index"] = turn
                        prog["current_question"] = contract.question
                    if self.session_blocked:
                        result = score_case(contract, [], not_scored_reason="NOT_SENT_SESSION_BLOCKED")
                        result.turn_index = turn
                        self.repo.save_case(run_context.run_id, result)
                    else:
                        result = self._run_one(run_context, contract, turn)
                        if result.completion_status not in {"completed", "not_sent"}:
                            self.session_blocked = True
                        if getattr(self.agent_client, "halted", False):
                            self.session_blocked = True
                    results.append(result)
                    pending.remove(contract.case_id)
                    with self._lock:
                        prog = self.progress[run_context.run_id]
                        prog["done"] = len(results)
                        prog["finished"] = [item.case_id for item in results]
            else:
                with ThreadPoolExecutor(max_workers=self.concurrency) as pool:
                    futures = {pool.submit(self._run_one, run_context, contract): contract.case_id for contract in selected}
                    for future in as_completed(futures):
                        result = future.result()
                        results.append(result)
                        pending = [cid for cid in pending if cid != result.case_id]
                        with self._lock:
                            prog = self.progress[run_context.run_id]
                            prog["done"] += 1
                            prog["current"] = result.case_id
                            prog["finished"] = [item.case_id for item in results]
        except Exception:
            self.repo.mark_interrupted(run_context.run_id, pending)
            summary.status = RunStatus.INTERRUPTED
            summary.finished_at = _now()
            self.repo.update_run(summary)
            raise
        order = {c.case_id: i for i, c in enumerate(selected)}
        results.sort(key=lambda r: order.get(r.case_id, 999))
        summary = score_run(
            run_context.run_id,
            results,
            agent_name=run_context.agent_name,
            anchor_time=run_context.anchor_time.isoformat(),
            timezone_name=run_context.timezone,
            watermark_stable=all("WATERMARK_CHANGED" not in r.error_types for r in results),
            status=RunStatus.INTERRUPTED if self.session_blocked else RunStatus.COMPLETED,
            mode=self.mode,
            consistency_threshold=run_context.consistency_threshold,
        )
        summary.started_at = started_at
        summary.finished_at = _now()
        summary.progress_done = len(results)
        summary.progress_total = len(selected)
        self.repo.update_run(summary)
        from evaluator.evaluation_metrics import run_evaluation_metrics
        from evaluator.verdict import headline_from_metrics
        self.repo.save_headline_metrics(
            run_context.run_id, headline_from_metrics(run_evaluation_metrics(self.repo, run_context.run_id, selected)))
        with self._lock:
            self.progress[run_context.run_id] = {
                "done": len(results),
                "total": len(selected),
                "status": summary.status.value,
                "current": None,
                "current_index": None,
                "current_question": None,
                "queue": [c.case_id for c in selected],
                "finished": [item.case_id for item in results],
                "sequential": bool(getattr(self.agent_client, "sequential", False)),
            }
        return summary

    def get_progress(self, run_id: str) -> Dict[str, Any]:
        with self._lock:
            payload = dict(self.progress.get(run_id) or {})
        queue = list(payload.get("queue") or [])
        finished = list(payload.get("finished") or [])
        current = payload.get("current")
        payload["pending"] = [case_id for case_id in queue if case_id not in finished and case_id != current]
        if current and payload.get("current_question") is None:
            contract = self.contracts_by_id.get(current)
            payload["current_question"] = contract.question if contract else None
        return payload

    def _select_cases(self, case_ids: Optional[Sequence[str]], include_non_numeric: bool) -> List[CaseContract]:
        if case_ids:
            missing = [cid for cid in case_ids if cid not in self.contracts_by_id]
            if missing:
                raise KeyError(f"unknown case_id: {missing}")
            return [self.contracts_by_id[cid] for cid in case_ids]
        if include_non_numeric:
            return list(self.contracts)
        return [c for c in self.contracts if c.numeric_evaluable]

    def _run_one(self, run_context: RunContext, contract: CaseContract, turn: int = 0) -> CaseResult:
        logs: Dict[str, Any] = {"retries": 0, "exceptions": []}
        if not contract.numeric_evaluable:
            result = score_case(contract, [], not_scored_reason="NON_NUMERIC")
            result.turn_index = turn
            self.repo.save_case(run_context.run_id, result, logs=logs)
            return result
        if not contract.realtime_ready:
            result = score_case(contract, [], not_scored_reason="SQL_NOT_REALTIME_READY")
            result.turn_index = turn
            self.repo.save_case(run_context.run_id, result, logs=logs)
            return result

        sql_snapshot: Optional[SqlSnapshot] = None
        agent_answer: Optional[AgentAnswer] = None
        agent_claims, sql_claims = [], []
        alignment = {}
        watermark_flag = False
        result = None
        extract_text = ""
        try:
            params = template_params(
                contract.sql_template,
                params_for_resolver(run_context, contract.parameter_resolver),
            )
            tables = tables_in_sql(contract.sql_template or "")
            before = after = None
            if self.enable_watermark and self.sql_executor and self.mode == "live":
                before = capture_watermark(self.sql_executor, tables, self.timestamp_columns)

            agent_answer = self.agent_client.ask(contract, run_context) if self.agent_client else AgentAnswer(
                case_id=contract.case_id, question=contract.question, error="agent client missing"
            )
            agent_failed = bool(agent_answer.error or agent_answer.completion_status != "completed")
            extract_text = agent_answer.text or ""
            if agent_failed:
                logs["agent_error"] = agent_answer.error
            elif self.mode == "live":
                alignment, extract_text = align_answer(
                    contract, run_context, agent_answer.text,
                    (agent_answer.response_body or {}).get("time_hints"),
                )
                if alignment.get("sql_params"):
                    params = alignment["sql_params"]
                else:
                    alignment["executed_params"] = params
            if self.mode.startswith("mock"):
                golden = self.golden_cases.get(contract.case_id) or {}
                sql_claims = claims_from_expected(contract, golden)
                columns, rows = expected_table(golden)
                sql_snapshot = SqlSnapshot(
                    sql_template=contract.sql_template,
                    params=params,
                    executed_sql="",
                    columns=columns,
                    rows=rows,
                    row_count=len(rows),
                    source="golden_expected",
                )
            elif self.sql_executor is not None and self.sql_executor.connect is not None:
                sql_snapshot = self.sql_executor.query(contract.sql_template, params)
                alignment["sql_attempts"] = [{"params": params, "status": "error" if sql_snapshot.error else "empty" if _empty_sql(contract, sql_snapshot) else "ok", "row_count": sql_snapshot.row_count}]
                if self.mode == "live":
                    sql_snapshot, params = _fallback_sql(
                        self.sql_executor, contract, run_context, params, sql_snapshot, alignment
                    )
                sql_claims = claims_from_sql_rows(contract, sql_snapshot.rows)
            else:
                sql_snapshot = SqlSnapshot(sql_template=contract.sql_template, params=params,
                                           error="database connection is not configured")

            if self.enable_watermark and self.sql_executor and self.mode == "live":
                after = capture_watermark(self.sql_executor, tables, self.timestamp_columns)
                watermark_flag = watermark_changed(before, after)
                if sql_snapshot:
                    sql_snapshot.watermark_before = before
                    sql_snapshot.watermark_after = after
                    sql_snapshot.watermark_changed = watermark_flag
        except Exception as exc:
            logs["exceptions"].append(type(exc).__name__)
            result = score_case(contract, [], not_scored_reason="SQL_FAIL")
            return self._save_result(run_context, result, agent_answer, sql_snapshot, logs, alignment, turn)
        if agent_answer and (agent_answer.error or agent_answer.completion_status != "completed"):
            result = score_case(contract, [], not_scored_reason="AGENT_FAIL")
            return self._save_result(run_context, result, agent_answer, sql_snapshot, logs, alignment, turn)
        if sql_snapshot and sql_snapshot.error:
            result = score_case(contract, [], not_scored_reason="SQL_FAIL")
            logs["sql_error"] = sql_snapshot.error
            return self._save_result(run_context, result, agent_answer, sql_snapshot, logs, alignment, turn)
        if watermark_flag:
            result = score_case(contract, [], not_scored_reason="WATERMARK_CHANGED")
            return self._save_result(run_context, result, agent_answer, sql_snapshot, logs, alignment, turn)

        if alignment.get("period_source") == "database_fallback":
            alignment["sql_period"] = (alignment.get("sql_params") or {}).get("period")
            alignment["alignment_status"] = "ALIGNED_WITH_FALLBACK"
            alignment["fallback_reason"] = alignment.get("fallback_reason") or "requested_period_no_data"
            alignment["effective_period"] = alignment.get("effective_period") or alignment.get("sql_period")

        agent_claims = extract_claims(contract, extract_text)
        items = compare_claims(contract, sql_claims, agent_claims)
        result = score_case(contract, items, extract_failed=not agent_claims and bool(contract.measures))
        if self.mode == "live":
            absent = set(contract.measures) - {c.metric for c in sql_claims}
            if absent or (sql_snapshot and sql_snapshot.truncated):
                alignment.setdefault("issues", []).append("SQL_BENCHMARK_INCOMPLETE")
                alignment["status"] = "REVIEW"
            duplicates = {}
            for claim in agent_claims + sql_claims:
                key = (claim.source, tuple(sorted(claim.coordinates.items())), claim.metric)
                if key in duplicates:
                    alignment.setdefault("issues", []).append("DUPLICATE_COORDINATES")
                    alignment["status"] = "REVIEW"
                duplicates[key] = claim.value
            if alignment.get("status") == "REVIEW":
                alignment["conditional_numeric_status"] = result.status.value
                result.status = CaseStatus.REVIEW
                result.not_scored_reason = "CONTEXT_REVIEW"
                result.primary_failure = "CONTEXT_REVIEW"
                result.error_types = list(dict.fromkeys(result.error_types + alignment.get("issues", [])))
        return self._save_result(run_context, result, agent_answer, sql_snapshot, logs, alignment, turn,
                                 agent_claims, sql_claims)

    def _save_result(self, run_context, result, agent_answer, sql_snapshot, logs, alignment, turn,
                     agent_claims=(), sql_claims=()):
        result.agent_latency_ms = agent_answer.latency_ms if agent_answer else 0
        result.sql_latency_ms = sql_snapshot.latency_ms if sql_snapshot else 0
        result.retries = agent_answer.retries if agent_answer else 0
        result.turn_index = turn
        result.completion_status = agent_answer.completion_status if agent_answer else "not_sent"
        result.agent_timings = agent_answer.timings if agent_answer else {}
        result.alignment = alignment
        self.repo.save_case(
            run_context.run_id,
            result,
            agent_text=agent_answer.text if agent_answer else "",
            agent_raw=_agent_raw(agent_answer),
            sql_payload=sql_snapshot.model_dump(mode="json") if sql_snapshot else None,
            # 空提取也要落盘：[] 与"未提取"有区别，事后诊断需知道当时确实没映射上
            agent_claims=[c.model_dump(mode="json") for c in agent_claims],
            sql_claims=[c.model_dump(mode="json") for c in sql_claims],
            logs=logs,
        )
        return result
