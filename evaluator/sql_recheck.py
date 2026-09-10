"""Append independent SQL evidence without replacing the original evaluation."""
import json
from datetime import datetime, timezone
from uuid import uuid4

from evaluator.run_context import parameter_tokens


def recheck_sql(repo, run_id, case_id, executor, params=None):
    detail = repo.load_case_detail(run_id, case_id)
    if not detail:
        raise KeyError("case not found")
    run = repo.get_run(run_id)
    if run.get("status") in {"RUNNING", "PENDING"}:
        raise ValueError("测评运行中，暂不能补查")
    contracts = json.loads((repo.run_dir(run_id) / "contracts_snapshot.json").read_text(encoding="utf-8"))
    contract = next(c for c in contracts if c["case_id"] == case_id)
    template = contract.get("sql_template", "")
    alignment = (detail.get("result") or {}).get("alignment") or {}
    defaults = alignment.get("sql_params") or alignment.get("requested_params") or (detail.get("sql_snapshot") or {}).get("params") or {}
    bound = dict(defaults if params is None else params)
    names = {name for _, name, _ in parameter_tokens(template)}
    if set(bound) != names:
        raise ValueError("参数必须与原 SQL 模板完全一致: " + ", ".join(sorted(names)))
    if any(isinstance(v, bool) or not isinstance(v, (str, int, float)) or len(str(v)) > 100 for v in bound.values()):
        raise ValueError("参数必须是长度不超过 100 的日期、数值或字符串")
    started_at = datetime.now(timezone.utc).isoformat()
    payload = executor.query(template, bound).model_dump(mode="json")
    payload.update(started_at=started_at, finished_at=datetime.now(timezone.utc).isoformat(),
                   purpose="reference_only", parameter_source="manual" if params is not None else "saved_context",
                   note="事后只读补查，仅供复核；不代表原测评时刻快照，不修改原评分。")
    folder = repo.run_dir(run_id) / "cases" / case_id / "sql_rechecks"
    folder.mkdir(exist_ok=True)
    path = folder / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f") + "_" + uuid4().hex + ".json")
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload
