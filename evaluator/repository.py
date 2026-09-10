# -*- coding: utf-8 -*-
"""SQLite 索引 + runtime/runs 大对象落盘。"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from evaluator.models import CaseResult, RunSummary
from evaluator.paths import db_path as default_db_path, ensure_runtime, runs_dir as default_runs_dir

SCHEMA = """
CREATE TABLE IF NOT EXISTS evaluation_runs (
    run_id TEXT PRIMARY KEY,
    status TEXT,
    started_at TEXT,
    finished_at TEXT,
    anchor_time TEXT,
    timezone TEXT,
    agent_name TEXT,
    contract_version TEXT,
    mode TEXT,
    summary_json TEXT,
    created_at TEXT,
    consistency_threshold REAL DEFAULT 0.6
);
CREATE TABLE IF NOT EXISTS case_runs (
    run_id TEXT,
    case_id TEXT,
    status TEXT,
    not_scored_reason TEXT,
    primary_failure TEXT,
    metrics_json TEXT,
    error_types TEXT,
    agent_latency_ms INTEGER,
    sql_latency_ms INTEGER,
    retries INTEGER,
    reviewed INTEGER DEFAULT 0,
    PRIMARY KEY (run_id, case_id)
);
CREATE TABLE IF NOT EXISTS agent_answers (
    run_id TEXT,
    case_id TEXT,
    text_path TEXT,
    latency_ms INTEGER,
    model TEXT,
    error TEXT,
    PRIMARY KEY (run_id, case_id)
);
CREATE TABLE IF NOT EXISTS sql_snapshots (
    run_id TEXT,
    case_id TEXT,
    result_path TEXT,
    latency_ms INTEGER,
    row_count INTEGER,
    error TEXT,
    watermark_changed INTEGER,
    PRIMARY KEY (run_id, case_id)
);
CREATE TABLE IF NOT EXISTS numeric_claims (
    run_id TEXT,
    case_id TEXT,
    claim_id TEXT,
    source TEXT,
    metric TEXT,
    value REAL,
    payload_json TEXT,
    PRIMARY KEY (run_id, case_id, claim_id)
);
CREATE TABLE IF NOT EXISTS comparison_items (
    run_id TEXT,
    case_id TEXT,
    item_index INTEGER,
    metric TEXT,
    status TEXT,
    payload_json TEXT,
    PRIMARY KEY (run_id, case_id, item_index)
);
CREATE TABLE IF NOT EXISTS review_notes (
    run_id TEXT,
    case_id TEXT,
    reviewed INTEGER,
    note TEXT,
    updated_at TEXT,
    verdict TEXT,
    PRIMARY KEY (run_id, case_id)
);
"""


def _now() -> str:
    return datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds")


class Repository:
    def __init__(self, db_path: Optional[Path] = None, runs_dir: Optional[Path] = None):
        ensure_runtime()
        self.db_path = Path(db_path or default_db_path())
        self.runs_dir = Path(runs_dir or default_runs_dir())
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init(self) -> None:
        with self._connect() as conn:
            conn.executescript(SCHEMA)
            self._migrate(conn)

    def _migrate(self, conn: sqlite3.Connection) -> None:
        run_cols = {row[1] for row in conn.execute("PRAGMA table_info(evaluation_runs)")}
        if "consistency_threshold" not in run_cols:
            conn.execute("ALTER TABLE evaluation_runs ADD COLUMN consistency_threshold REAL DEFAULT 0.6")
        note_cols = {row[1] for row in conn.execute("PRAGMA table_info(review_notes)")}
        if "verdict" not in note_cols:
            conn.execute("ALTER TABLE review_notes ADD COLUMN verdict TEXT")

    def run_dir(self, run_id: str) -> Path:
        path = self.runs_dir / run_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def write_json(self, run_id: str, name: str, payload: Any) -> str:
        path = self.run_dir(run_id) / name
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        return str(path)

    def create_run(self, summary: RunSummary) -> None:
        with self._connect() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO evaluation_runs
                (run_id, status, started_at, finished_at, anchor_time, timezone, agent_name,
                 contract_version, mode, summary_json, created_at, consistency_threshold)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    summary.run_id,
                    summary.status.value,
                    summary.started_at,
                    summary.finished_at,
                    summary.anchor_time,
                    summary.timezone,
                    summary.agent_name,
                    summary.contract_version,
                    summary.mode,
                    summary.model_dump_json(),
                    _now(),
                    summary.consistency_threshold,
                ),
            )

    def update_run(self, summary: RunSummary) -> None:
        self.create_run(summary)

    def list_runs(self) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT run_id, status, started_at, finished_at, agent_name, mode, summary_json, consistency_threshold FROM evaluation_runs ORDER BY created_at DESC"
            ).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            if item.get("summary_json"):
                item["summary"] = json.loads(item["summary_json"])
            out.append(item)
        return out

    def get_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM evaluation_runs WHERE run_id=?", (run_id,)).fetchone()
        if not row:
            return None
        data = dict(row)
        data["summary"] = json.loads(data["summary_json"]) if data.get("summary_json") else {}
        return data

    def save_case(
        self,
        run_id: str,
        result: CaseResult,
        *,
        agent_text: str = "",
        agent_raw: Any = None,
        sql_payload: Any = None,
        agent_claims: Any = None,
        sql_claims: Any = None,
        logs: Any = None,
    ) -> None:
        case_dir = self.run_dir(run_id) / "cases" / result.case_id
        case_dir.mkdir(parents=True, exist_ok=True)
        text_path = ""
        if agent_text or agent_raw is not None:
            payload = {"text": agent_text, "raw": agent_raw}
            (case_dir / "agent_answer.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
            text_path = str(case_dir / "agent_answer.json")
        result_path = ""
        if sql_payload is not None:
            (case_dir / "sql_snapshot.json").write_text(json.dumps(sql_payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
            result_path = str(case_dir / "sql_snapshot.json")
        if agent_claims is not None:
            (case_dir / "agent_claims.json").write_text(json.dumps(agent_claims, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        if sql_claims is not None:
            (case_dir / "sql_claims.json").write_text(json.dumps(sql_claims, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        if logs is not None:
            (case_dir / "logs.json").write_text(json.dumps(logs, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        (case_dir / "result.json").write_text(result.model_dump_json(indent=2), encoding="utf-8")

        with self._connect() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO case_runs
                (run_id, case_id, status, not_scored_reason, primary_failure, metrics_json, error_types,
                 agent_latency_ms, sql_latency_ms, retries, reviewed)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    run_id,
                    result.case_id,
                    result.status.value,
                    result.not_scored_reason,
                    result.primary_failure,
                    result.metrics.model_dump_json(),
                    json.dumps(result.error_types, ensure_ascii=False),
                    result.agent_latency_ms,
                    result.sql_latency_ms,
                    result.retries,
                    1 if result.reviewed else 0,
                ),
            )
            conn.execute(
                """INSERT OR REPLACE INTO agent_answers (run_id, case_id, text_path, latency_ms, model, error)
                VALUES (?, ?, ?, ?, ?, ?)""",
                (run_id, result.case_id, text_path, result.agent_latency_ms, "", result.not_scored_reason if result.not_scored_reason == "AGENT_FAIL" else None),
            )
            conn.execute(
                """INSERT OR REPLACE INTO sql_snapshots (run_id, case_id, result_path, latency_ms, row_count, error, watermark_changed)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    run_id,
                    result.case_id,
                    result_path,
                    result.sql_latency_ms,
                    0,
                    result.not_scored_reason if result.not_scored_reason in {"SQL_FAIL", "WATERMARK_CHANGED"} else None,
                    1 if result.not_scored_reason == "WATERMARK_CHANGED" else 0,
                ),
            )
            conn.execute("DELETE FROM comparison_items WHERE run_id=? AND case_id=?", (run_id, result.case_id))
            for idx, item in enumerate(result.comparison_items):
                conn.execute(
                    """INSERT INTO comparison_items (run_id, case_id, item_index, metric, status, payload_json)
                    VALUES (?, ?, ?, ?, ?, ?)""",
                    (run_id, result.case_id, idx, item.metric, item.status.value, item.model_dump_json()),
                )

    def list_cases(self, run_id: str) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM case_runs WHERE run_id=? ORDER BY case_id",
                (run_id,),
            ).fetchall()
            notes = {
                r["case_id"]: r
                for r in conn.execute("SELECT * FROM review_notes WHERE run_id=?", (run_id,)).fetchall()
            }
        out = []
        for row in rows:
            item = dict(row)
            item["metrics"] = json.loads(item["metrics_json"]) if item.get("metrics_json") else {}
            item["error_types"] = json.loads(item["error_types"]) if item.get("error_types") else []
            result_path = self.runs_dir / run_id / "cases" / item["case_id"] / "result.json"
            if result_path.exists():
                result = json.loads(result_path.read_text(encoding="utf-8"))
                for key in ("turn_index", "completion_status", "agent_timings", "alignment"):
                    item[key] = result.get(key)
            note = notes.get(item["case_id"])
            if note:
                item["reviewed"] = bool(note["reviewed"])
                item["review_note"] = note["note"]
                item["manual_verdict"] = note["verdict"]
            out.append(item)
        snapshot = self.runs_dir / run_id / "contracts_snapshot.json"
        if snapshot.exists():
            order = {c["case_id"]: i for i, c in enumerate(json.loads(snapshot.read_text(encoding="utf-8")))}
            out.sort(key=lambda item: order.get(item["case_id"], 999))
        return out

    def load_case_detail(self, run_id: str, case_id: str) -> Optional[Dict[str, Any]]:
        cases = {c["case_id"]: c for c in self.list_cases(run_id)}
        if case_id not in cases:
            return None
        case_dir = self.run_dir(run_id) / "cases" / case_id
        detail = dict(cases[case_id])
        detail["sql_rechecks"] = [json.loads(p.read_text(encoding="utf-8")) for p in sorted((case_dir / "sql_rechecks").glob("*.json"))]
        for name in ("result.json", "agent_answer.json", "sql_snapshot.json", "agent_claims.json", "sql_claims.json", "logs.json"):
            path = case_dir / name
            key = name.replace(".json", "")
            if path.exists():
                detail[key] = json.loads(path.read_text(encoding="utf-8"))
        with self._connect() as conn:
            note = conn.execute(
                "SELECT * FROM review_notes WHERE run_id=? AND case_id=?",
                (run_id, case_id),
            ).fetchone()
        if note:
            payload = dict(note)
            detail["review"] = payload
            detail["manual_verdict"] = payload.get("verdict")
        return detail

    def save_verdict(self, run_id: str, case_id: str, verdict: Optional[str], note: str = "") -> None:
        with self._connect() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO review_notes (run_id, case_id, reviewed, note, updated_at, verdict)
                VALUES (?, ?, ?, ?, ?, ?)""",
                (run_id, case_id, 1 if verdict else 0, note, _now(), verdict),
            )
            conn.execute(
                "UPDATE case_runs SET reviewed=? WHERE run_id=? AND case_id=?",
                (1 if verdict else 0, run_id, case_id),
            )

    def save_headline_metrics(self, run_id: str, headline: Dict[str, Any]) -> None:
        run = self.get_run(run_id)
        if not run:
            return
        summary = run.get("summary") or {}
        summary["headline_metrics"] = headline
        summary["case_pass_rate"] = headline.get("pass_rate")
        summary["numeric_accuracy"] = headline.get("accuracy")
        summary["assessability_rate"] = headline.get("assessability")
        with self._connect() as conn:
            conn.execute(
                "UPDATE evaluation_runs SET summary_json=? WHERE run_id=?",
                (json.dumps(summary, ensure_ascii=False, default=str), run_id),
            )

    def mark_interrupted(self, run_id: str, pending_ids: List[str]) -> None:
        with self._connect() as conn:
            for case_id in pending_ids:
                conn.execute(
                    """INSERT OR REPLACE INTO case_runs
                    (run_id, case_id, status, not_scored_reason, primary_failure, metrics_json, error_types,
                     agent_latency_ms, sql_latency_ms, retries, reviewed)
                    VALUES (?, ?, 'NOT_SCORED', 'INTERRUPTED', 'INTERRUPTED', '{}', '[]', 0, 0, 0, 0)""",
                    (run_id, case_id),
                )
