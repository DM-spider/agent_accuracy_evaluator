# -*- coding: utf-8 -*-
"""leakage-agent-cli 取数封装：调用、分页、原始响应落盘。

只做取数和分页，不做任何业务口径判断。
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

CLI_DIR = Path(
    r"D:\WorkingSet\PythonProjects\深水工作\water-loss-agent\zs-water-loss-agent\tools\leakage-agent-cli"
)
OUT_DIR = Path(__file__).resolve().parent / "out"
RAW_DIR = OUT_DIR / "raw"

METRIC = "metric"      # data.data.list, pageNo 从 0 起
DETAIL = "detail"      # data.data.result, pageNumber 从 1 起
WRITE = "write"        # 不用于读取


def _payload_data(payload: Dict[str, Any]) -> Dict[str, Any]:
    data = payload.get("data")
    if isinstance(data, dict) and isinstance(data.get("data"), dict):
        return data["data"]
    return data if isinstance(data, dict) else {}


def _cache_path(command: str, params: Dict[str, Any], page: int) -> Path:
    raw = json.dumps({"c": command, "p": params, "page": page}, ensure_ascii=False, sort_keys=True)
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]
    return RAW_DIR / f"{command}_{digest}.json"


def invoke_raw(
    command: str,
    params: Optional[Dict[str, Any]] = None,
    *,
    page: int = 0,
    timeout: int = 90,
    use_cache: bool = True,
) -> Dict[str, Any]:
    """调用一次 CLI，返回解析后的 JSON；原始响应落盘。"""
    params = dict(params or {})
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    cache = _cache_path(command, params, page)
    if use_cache and cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))
    args = [sys.executable, "-m", "leakage_agent_cli", "invoke", command, "--json"]
    for key, value in params.items():
        args += ["--param", f"{key}={value}"]
    completed = subprocess.run(
        args,
        cwd=str(CLI_DIR),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    text = (completed.stdout or "").strip()
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        payload = {
            "respCode": "CLI_PARSE_ERROR",
            "respMsg": (completed.stderr or text or "empty stdout")[:500],
            "exitCode": completed.returncode,
        }
    payload.setdefault("exitCode", completed.returncode)
    cache.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    return payload


def fetch_paged(
    command: str,
    params: Optional[Dict[str, Any]] = None,
    *,
    kind: str = METRIC,
    page_size: Optional[int] = None,
    max_pages: int = 500,
    use_cache: bool = True,
) -> Dict[str, Any]:
    """按命令类型翻页取全量。

    指标：pageNo 从 0 起，路径 data.data.list，total/pageCount。
    明细：pageNumber 从 1 起，路径 data.data.result，totalRows/totalPage。
    """
    params = dict(params or {})
    if kind == DETAIL:
        page_size = page_size or 50
        page_key = "pageNumber"
        first_page = 1
        rows_key = "result"
        total_key = "totalRows"
        pages_key = "totalPage"
    else:
        page_size = page_size or 100
        page_key = "pageNo"
        first_page = 0
        rows_key = "list"
        total_key = "total"
        pages_key = "pageCount"

    rows: List[Dict[str, Any]] = []
    total: Optional[int] = None
    total_pages: Optional[int] = None
    resp_code = None
    resp_msg = ""
    pages_fetched = 0

    for offset in range(max_pages):
        page = first_page + offset
        call_params = dict(params)
        call_params[page_key] = page
        call_params["pageSize"] = page_size
        payload = invoke_raw(command, call_params, page=page, use_cache=use_cache)
        resp_code = payload.get("respCode")
        resp_msg = str(payload.get("respMsg") or "")
        data = _payload_data(payload)
        chunk = data.get(rows_key) or []
        if total is None and isinstance(data.get(total_key), int):
            total = data[total_key]
        if total_pages is None and isinstance(data.get(pages_key), int):
            total_pages = data[pages_key]
        pages_fetched += 1
        rows.extend(chunk)

        if resp_code != "SUCCESS" or not chunk:
            break
        if len(chunk) < page_size:
            break
        if total_pages is not None and page >= total_pages - 1 + first_page:
            break
        if total is not None and len(rows) >= total:
            break

    return {
        "command": command,
        "params": params,
        "kind": kind,
        "respCode": resp_code,
        "respMsg": resp_msg,
        "rows": rows,
        "total": total,
        "totalPages": total_pages,
        "pagesFetched": pages_fetched,
        "error": None if resp_code == "SUCCESS" else f"{resp_code}: {resp_msg}",
    }


def probe_error(command: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """单次调用并返回 respCode/msg，用于复现阻塞问题。"""
    payload = invoke_raw(command, params or {}, use_cache=False)
    return {
        "command": command,
        "params": params or {},
        "respCode": payload.get("respCode"),
        "respMsg": payload.get("respMsg"),
        "exitCode": payload.get("exitCode"),
        "commandLength": len(command),
    }
