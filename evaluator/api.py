# -*- coding: utf-8 -*-
"""评测 REST API。"""
from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel

from evaluator.agent_client import FixtureAgentClient, client_from_settings
from evaluator.contract_loader import CONTRACTS_PATH, load_contracts, resolve_contracts_path
from evaluator.exporter import export_html, export_json, export_xlsx
from evaluator.golden_loader import load_agent_answers, merge_golden, merge_v1_golden
from evaluator.mock_demo import MIXED_ANSWERS_FILE, MIXED_CASE_IDS, demo_pack, load_script_answers, script_path
from evaluator.orchestrator import Orchestrator
from evaluator.paths import GOLDEN_DIR, ensure_runtime, runs_dir
from evaluator.repository import Repository
from evaluator.run_context import build_run_context
from evaluator.settings import agent_token, db_password, load_settings, platform_login_session
from evaluator.sql_executor import SqlExecutor, make_pg_connector
from evaluator.session_guard import SessionGuard
from evaluator.models import RunSummary, RunStatus
from evaluator.verdict import headline_from_metrics, normalize_manual_verdict, parse_threshold

router = APIRouter()
_STATE: Dict[str, Any] = {}
_LOCK = threading.Lock()
_LIVE_LOCK = threading.Lock()


class CreateRunBody(BaseModel):
    case_ids: Optional[List[str]] = None
    numeric_only: bool = True
    mode: str = "mock_mixed"
    anchor_time: Optional[str] = None
    agent_name: Optional[str] = None
    consistency_threshold: Optional[float] = None


def _load_v1_contracts():
    if not CONTRACTS_PATH.exists():
        return []
    return load_contracts(CONTRACTS_PATH, generate_if_missing=False)


def _contracts_by_id(st: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    st = st or state()
    by_id = {c.case_id: c for c in st.get("contracts_v1") or []}
    by_id.update({c.case_id: c for c in st["contracts"]})
    return by_id


def bootstrap(settings: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    ensure_runtime()
    settings = settings or load_settings()
    repo = Repository()
    contracts = load_contracts(resolve_contracts_path(settings))
    # data/ 不入 git（单独私发）：缺黄金集时降级启动（健康检查显示 golden: missing，
    # 模拟模式与 v1 历史题不可用），而不是让服务起不来。
    try:
        golden = merge_golden()
    except FileNotFoundError:
        golden = {"meta": {}, "cases": [], "excel_count": 0}
    golden_map = {c["case_id"]: c for c in golden["cases"]}
    # 模拟 20 题仍用历史题号；从 golden 根目录的独立 v1 文件补期望。
    try:
        for case in merge_v1_golden()["cases"]:
            golden_map.setdefault(case["case_id"], case)
    except FileNotFoundError:
        pass
    try:
        contracts_v1 = _load_v1_contracts()
    except FileNotFoundError:
        contracts_v1 = []
    _STATE.update(
        {
            "settings": settings,
            "repo": repo,
            "contracts": contracts,
            "contracts_v1": contracts_v1,
            "golden": golden,
            "golden_map": golden_map,
            "orchestrators": {},
        }
    )
    return _STATE


def state() -> Dict[str, Any]:
    if not _STATE:
        bootstrap()
    return _STATE


def _error(code: str, message: str, status: int = 400):
    raise HTTPException(status_code=status, detail={"code": code, "message": message})


@router.get("/api/health")
def health() -> Dict[str, Any]:
    st = state()
    settings = st["settings"]
    repo: Repository = st["repo"]
    golden_ok = (GOLDEN_DIR / "golden_dataset.json").exists()
    storage_ok = repo.db_path.parent.exists()
    db_cfg = settings.get("database") or {}
    db_status = "skipped"
    if db_cfg.get("user") and db_password(settings):
        try:
            conn = make_pg_connector(settings, db_password(settings))()
            conn.close()
            db_status = "ok"
        except Exception as exc:
            db_status = f"error: {type(exc).__name__}"
    agent_cfg = settings.get("agent") or {}
    agent_status = "stream_configured" if (settings.get("platform") or {}).get("enabled") else "configured" if agent_cfg.get("url") else "not_configured"
    return {
        "status": "ok",
        "golden": "ok" if golden_ok else "missing",
        "storage": "ok" if storage_ok else "missing",
        "database": db_status,
        "agent": agent_status,
        "contracts": len(st["contracts"]),
    }


@router.get("/api/mock-demo")
def mock_demo() -> Dict[str, Any]:
    pack = demo_pack()
    pack["script_exists"] = script_path().exists()
    return pack


@router.get("/api/mock-demo/script")
def mock_demo_script(download: bool = Query(False)):
    path = script_path()
    if not path.exists():
        _error("not_found", "模拟问答稿不存在", 404)
    if download:
        return FileResponse(path, media_type="text/markdown; charset=utf-8", filename=path.name)
    return PlainTextResponse(path.read_text(encoding="utf-8"), media_type="text/markdown; charset=utf-8")


@router.get("/api/catalog")
def catalog() -> Dict[str, Any]:
    contracts = state()["contracts"]
    return {
        "total": len(contracts),
        "numeric": sum(1 for c in contracts if c.numeric_evaluable),
        "cases": [
            {
                "case_id": c.case_id,
                "question": c.question,
                "scene_big": c.scene_big,
                "result_type": c.result_type,
                "numeric_evaluable": c.numeric_evaluable,
                "realtime_ready": c.realtime_ready,
            }
            for c in contracts
        ],
    }


def _refresh_headline(repo, run_id, contracts) -> Dict[str, Any]:
    from evaluator.evaluation_metrics import run_evaluation_metrics
    metrics = run_evaluation_metrics(repo, run_id, contracts)
    repo.save_headline_metrics(run_id, headline_from_metrics(metrics))
    return metrics


@router.get("/api/runs")
def list_runs() -> Dict[str, Any]:
    runs = state()["repo"].list_runs()
    for run in runs:
        summary = run.get("summary") or {}
        headline = summary.get("headline_metrics") or {}
        if headline:
            summary["case_pass_rate"] = headline.get("pass_rate")
            summary["numeric_accuracy"] = headline.get("accuracy")
            summary["assessability_rate"] = headline.get("assessability")
        run["summary"] = summary
    return {"runs": runs}


@router.post("/api/runs")
def create_run(body: CreateRunBody) -> Dict[str, Any]:
    st = state()
    settings = st["settings"]
    mode = body.mode or "mock_mixed"
    if mode not in {"live", "mock_perfect", "mock_errors", "mock_mixed"}:
        _error("invalid_mode", "mode 必须是 live / mock_mixed / mock_perfect / mock_errors")
    if mode == "live" and body.anchor_time:
        _error("live_anchor_managed", "真实接口不接收历史时钟参数，实时批次必须使用当前时间")
    agent_name = body.agent_name or "water-loss-agent"
    try:
        threshold = parse_threshold(body.consistency_threshold) if body.consistency_threshold is not None else 1.0
    except (TypeError, ValueError) as exc:
        _error("invalid_threshold", str(exc))
    is_mock = mode.startswith("mock")
    if is_mock:
        run_contracts = st.get("contracts_v1") or st["contracts"]
        contract_version = "v1"
    else:
        run_contracts = st["contracts"]
        contract_version = (settings.get("app") or {}).get("contract_version") or "v3"
    ctx = build_run_context(
        body.anchor_time or (datetime.now(timezone.utc) if mode == "live" else "2026-08-17T10:00:00+08:00"),
        timezone_name=(settings.get("app") or {}).get("timezone") or "Asia/Shanghai",
        agent_name=agent_name,
        contract_version=contract_version,
        latest_periods={} if mode == "live" else (st["golden"].get("meta") or {}).get("latest_periods") or {},
    )
    ctx = ctx.model_copy(update={"consistency_threshold": threshold})
    case_ids = body.case_ids
    if mode == "mock_mixed" and not case_ids:
        case_ids = list(MIXED_CASE_IDS)
    if mode == "live":
        client = client_from_settings(settings, agent_token(settings))
        password = db_password(settings)
        if not password or not (settings.get("database") or {}).get("user"):
            _error("database_not_configured", "实时测评需要配置数据库账号与密码，不会使用历史黄金结果代替")
        if getattr(client, "sequential", False):
            if not all((client.session_id, client.task_id, client.template_id, client.org_id, client.login_session)):
                _error("platform_not_configured", "请在 settings.toml 的 platform.curl 粘贴完整消息请求（Copy as cURL bash）")
            if not case_ids:
                case_ids = [c.case_id for c in st["contracts"] if c.numeric_evaluable and c.realtime_ready][:10]
        elif not client.url:
            _error("agent_not_configured", "智能体接口未配置")
        connector = make_pg_connector(settings, password) if password else None
        executor = SqlExecutor(
            connect=connector,
            timeout_seconds=int((settings.get("database") or {}).get("timeout_seconds") or 30),
            max_rows=int((settings.get("database") or {}).get("max_rows") or 5000),
        )
    else:
        if mode == "mock_mixed" and script_path().exists():
            answers = load_script_answers()
            client = FixtureAgentClient(answers, model="mock-mixed-agent")
        else:
            filename = {
                "mock_perfect": "agent_answers_perfect.json",
                "mock_errors": "agent_answers_errors.json",
                "mock_mixed": MIXED_ANSWERS_FILE,
            }[mode]
            payload = load_agent_answers(filename)
            answers = {c["case_id"]: c.get("final_answer_text") or "" for c in payload.get("cases", [])}
            client = FixtureAgentClient(answers, model="fixture")
        executor = SqlExecutor(connect=None)

    known_ids = {c.case_id for c in run_contracts}
    if case_ids and (len(set(case_ids)) != len(case_ids) or set(case_ids) - known_ids):
        _error("invalid_case_ids", "题号必须存在且不能重复")
    guard = None
    if mode == "live":
        if not _LIVE_LOCK.acquire(blocking=False):
            _error("live_run_active", "已有实时测评正在运行", 409)
        try:
            # Fail before sending the first question when SQL cannot connect.
            conn = connector()
            conn.close()
            if getattr(client, "sequential", False):
                guard = SessionGuard(st["repo"].runs_dir.parent / "session_guards", client.session_id)
                guard.acquire(ctx.run_id)
        except FileExistsError:
            _LIVE_LOCK.release()
            _error("session_unconfirmed", "上次会话执行状态未确认，请在网页确认任务结束后解除暂停", 409)
        except Exception as exc:
            _LIVE_LOCK.release()
            _error("database_preflight_failed", f"数据库连接预检失败：{type(exc).__name__}")

    orch = Orchestrator(
        repo=st["repo"],
        contracts=run_contracts,
        golden_cases=st["golden_map"],
        agent_client=client,
        sql_executor=executor,
        concurrency=int((settings.get("agent") or {}).get("concurrency") or 3),
        mode=mode,
        enable_watermark=mode == "live" and bool((settings.get("watermark") or {}).get("enabled", True)),
        timestamp_columns=(settings.get("watermark") or {}).get("timestamp_columns"),
    )
    with _LOCK:
        st["orchestrators"][ctx.run_id] = orch
    st["repo"].create_run(RunSummary(run_id=ctx.run_id, status=RunStatus.PENDING, mode=mode,
                                    anchor_time=ctx.anchor_time.isoformat(), agent_name=agent_name,
                                    consistency_threshold=threshold))

    def _job():
        completed = False
        try:
            orch.start_run(ctx, case_ids, include_non_numeric=not body.numeric_only)
            completed = not getattr(client, "uncertain", orch.session_blocked)
        except Exception:
            pass
        finally:
            if mode == "live":
                if guard and completed:
                    guard.release()
                _LIVE_LOCK.release()
            if hasattr(client, "close"):
                client.close()

    job = threading.Thread(target=_job, daemon=True)
    st.setdefault("jobs", {})[ctx.run_id] = job
    job.start()
    return {"run_id": ctx.run_id, "status": "RUNNING", "mode": mode, "anchor_time": ctx.anchor_time.isoformat()}


@router.get("/api/platform/status")
def platform_status():
    import os
    st = state()
    cfg = st["settings"].get("platform") or {}
    guard = SessionGuard(st["repo"].runs_dir.parent / "session_guards", cfg.get("session_id", ""))
    return {"enabled": bool(cfg.get("enabled")), "session_configured": bool(cfg.get("session_id")),
            "cookie_configured": bool(platform_login_session(st["settings"])),
            "database_credentials_configured": bool(db_password(st["settings"])),
            "active": _LIVE_LOCK.locked(), "blocked": guard.blocked()}


class ConfirmIdleBody(BaseModel):
    confirmed_idle: bool = False


@router.post("/api/platform/confirm-idle")
def confirm_session_idle(body: ConfirmIdleBody):
    if not body.confirmed_idle:
        _error("confirmation_required", "需要人工确认网页会话任务已结束")
    if not _LIVE_LOCK.acquire(blocking=False):
        _error("live_run_active", "测评仍在运行", 409)
    try:
        st = state()
        cfg = st["settings"].get("platform") or {}
        SessionGuard(st["repo"].runs_dir.parent / "session_guards", cfg.get("session_id", "")).release()
    finally:
        _LIVE_LOCK.release()
    return {"ok": True}


@router.get("/api/runs/{run_id}")
def get_run(run_id: str) -> Dict[str, Any]:
    st = state()
    run = st["repo"].get_run(run_id)
    if not run:
        _error("not_found", "run not found", 404)
    from evaluator.evaluation_metrics import run_evaluation_metrics
    metrics = run_evaluation_metrics(st["repo"], run_id, list(_contracts_by_id(st).values()))
    run["evaluation_metrics"] = metrics
    if run.get("status") in {"COMPLETED", "INTERRUPTED", "FAILED"}:
        st["repo"].save_headline_metrics(run_id, headline_from_metrics(metrics))
    return run


@router.get("/api/runs/{run_id}/progress")
def get_progress(run_id: str) -> Dict[str, Any]:
    st = state()
    orch = st["orchestrators"].get(run_id)
    if orch:
        return orch.get_progress(run_id)
    run = st["repo"].get_run(run_id)
    if not run:
        _error("not_found", "run not found", 404)
    summary = run.get("summary") or {}
    finished = [item["case_id"] for item in st["repo"].list_cases(run_id)]
    queue = []
    snapshot = st["repo"].run_dir(run_id) / "contracts_snapshot.json"
    if snapshot.exists():
        import json
        queue = [item["case_id"] for item in json.loads(snapshot.read_text(encoding="utf-8"))]
    pending = [case_id for case_id in queue if case_id not in finished]
    current = pending[0] if pending else None
    current_question = None
    if current:
        contract = _contracts_by_id(st).get(current)
        current_question = contract.question if contract else None
    return {
        "done": len(finished) if run.get("status") == "RUNNING" else summary.get("progress_done"),
        "total": summary.get("progress_total") or len(queue),
        "status": run.get("status"),
        "current": current,
        "current_index": len(finished) + 1 if pending else None,
        "current_question": current_question,
        "queue": queue,
        "finished": finished,
        "pending": pending[1:] if pending else [],
    }


@router.get("/api/runs/{run_id}/cases")
def list_cases(
    run_id: str,
    scene: Optional[str] = None,
    status: Optional[str] = None,
    result_type: Optional[str] = None,
    error_type: Optional[str] = None,
    q: Optional[str] = None,
) -> Dict[str, Any]:
    st = state()
    items = st["repo"].list_cases(run_id)
    contracts = _contracts_by_id(st)
    from evaluator.evaluation_metrics import run_evaluation_metrics
    judgments = (run_evaluation_metrics(st["repo"], run_id, list(contracts.values())).get("case_judgments") or {})
    out = []
    for item in items:
        contract = contracts.get(item["case_id"])
        row = dict(item)
        row["question"] = contract.question if contract else ""
        row["scene_big"] = contract.scene_big if contract else ""
        row["result_type"] = contract.result_type if contract else ""
        row.update(judgments.get(item["case_id"]) or {})
        if scene and row["scene_big"] != scene:
            continue
        if status and row.get("final_verdict") != status and row.get("status") != status:
            continue
        if result_type and row["result_type"] != result_type:
            continue
        if error_type and error_type not in (row.get("error_types") or []):
            continue
        if q and q.lower() not in (row["case_id"] + row["question"]).lower():
            continue
        out.append(row)
    return {"cases": out}


@router.get("/api/runs/{run_id}/cases/{case_id}")
def get_case(run_id: str, case_id: str) -> Dict[str, Any]:
    detail = state()["repo"].load_case_detail(run_id, case_id)
    if not detail:
        _error("not_found", "case not found", 404)
    ids = [c["case_id"] for c in state()["repo"].list_cases(run_id)]
    idx = ids.index(case_id) if case_id in ids else -1
    detail["prev_case_id"] = ids[idx - 1] if idx > 0 else None
    detail["next_case_id"] = ids[idx + 1] if 0 <= idx < len(ids) - 1 else None
    contract = _contracts_by_id().get(case_id)
    snapshot_path = state()["repo"].run_dir(run_id) / "contracts_snapshot.json"
    if snapshot_path.exists():
        import json
        from evaluator.models import CaseContract
        saved = next((c for c in json.loads(snapshot_path.read_text(encoding="utf-8")) if c["case_id"] == case_id), None)
        if saved:
            contract = CaseContract.model_validate(saved)
    if contract:
        detail["contract"] = contract.model_dump(mode="json")
        from evaluator.table_comparison import table_comparison
        detail["table_comparison"] = table_comparison(contract, detail)
    from evaluator.verdict import case_judgment
    items = (detail.get("table_comparison") or {}).get("items") or []
    detail["judgment"] = case_judgment(
        items,
        manual_verdict=detail.get("manual_verdict") or (detail.get("review") or {}).get("verdict"),
        contract=contract,
    )
    return detail


class VerdictBody(BaseModel):
    verdict: Optional[str] = None
    note: str = ""


class SqlRecheckBody(BaseModel):
    params: Optional[Dict[str, Any]] = None


@router.post("/api/runs/{run_id}/cases/{case_id}/sql-recheck")
def sql_recheck(run_id: str, case_id: str, body: SqlRecheckBody):
    from evaluator.sql_recheck import recheck_sql
    st = state()
    if not st["repo"].load_case_detail(run_id, case_id):
        _error("not_found", "case not found", 404)
    if not db_password(st["settings"]):
        _error("database_not_configured", "数据库未配置")
    executor = SqlExecutor(connect=make_pg_connector(st["settings"], db_password(st["settings"])))
    try:
        return recheck_sql(st["repo"], run_id, case_id, executor, body.params)
    except ValueError as exc:
        _error("invalid_recheck", str(exc))


@router.post("/api/runs/{run_id}/cases/{case_id}/verdict")
def set_verdict(run_id: str, case_id: str, body: VerdictBody) -> Dict[str, Any]:
    st = state()
    if not st["repo"].get_run(run_id):
        _error("not_found", "run not found", 404)
    if not st["repo"].load_case_detail(run_id, case_id):
        _error("not_found", "case not found", 404)
    try:
        verdict = normalize_manual_verdict(body.verdict)
    except ValueError as exc:
        _error("invalid_verdict", str(exc))
    st["repo"].save_verdict(run_id, case_id, verdict, body.note)
    metrics = _refresh_headline(st["repo"], run_id, st["contracts"])
    return {
        "ok": True,
        "verdict": verdict,
        "note": body.note,
        "judgment": (metrics.get("case_judgments") or {}).get(case_id),
        "headline": metrics.get("headline"),
    }


@router.get("/api/runs/{run_id}/export")
def export_run(run_id: str, format: str = Query("json")):
    repo: Repository = state()["repo"]
    if not repo.get_run(run_id):
        _error("not_found", "run not found", 404)
    export_dir = runs_dir() / run_id / "export"
    export_dir.mkdir(parents=True, exist_ok=True)
    fmt = format.lower()
    if fmt == "json":
        path = export_json(repo, run_id, export_dir / "report.json")
        media = "application/json"
    elif fmt in {"xlsx", "csv", "excel"}:
        path = export_xlsx(repo, run_id, export_dir / "report.xlsx")
        media = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    elif fmt == "html":
        path = export_html(repo, run_id, export_dir / "report.html")
        media = "text/html"
    else:
        _error("invalid_format", "format 必须是 json / xlsx / html")
    return FileResponse(path, media_type=media, filename=path.name)
