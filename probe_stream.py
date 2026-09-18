"""Send one authorized stream request and capture protocol evidence without retries."""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx


URL = "https://aiemployee.sz-water.com.cn/cc-control/api/user/mid-platform/session/message/stream"


def _platform_default(name: str) -> str:
    from evaluator.settings import load_settings, platform_login_session

    settings = load_settings()
    platform = settings.get("platform") or {}
    if name == "login_session":
        return platform_login_session(settings)
    if name == "task_name":
        return platform.get("task_name") or "leakage-skill"
    return str(platform.get(name) or "")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--org-id", default=_platform_default("org_id"))
    parser.add_argument("--session-id", default=_platform_default("session_id"))
    parser.add_argument("--task-id", default=_platform_default("task_id"))
    parser.add_argument("--template-id", default=_platform_default("template_id"))
    parser.add_argument("--task-name", default=_platform_default("task_name"))
    parser.add_argument("--message", required=True)
    args = parser.parse_args()
    credential = _platform_default("login_session")
    if not credential:
        parser.error("请在 config/settings.toml 的 platform.curl 中配置登录会话")
    body = {
        "sessionId": args.session_id,
        "taskId": args.task_id,
        "message": args.message,
        "taskName": args.task_name,
        "templateId": args.template_id,
    }
    headers = {
        "accept": "*/*",
        "x-org-id": args.org_id,
        "x-session-id": credential,
        "Cookie": "SESSION_ID=" + credential,
        "origin": "https://aiemployee.sz-water.com.cn",
        "referer": "https://aiemployee.sz-water.com.cn/workspace/",
    }
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    output_dir = Path(__file__).resolve().parent / "runtime" / "protocol_probes" / stamp
    output_dir.mkdir(parents=True)
    meta = {"url": URL, "request": body, "started_at": datetime.now(timezone.utc).isoformat()}
    started = time.perf_counter()
    pieces = []
    try:
        with httpx.Client(timeout=httpx.Timeout(30, connect=15), follow_redirects=False) as client:
            with client.stream("POST", URL, json=body, headers=headers) as response:
                meta["http_status"] = response.status_code
                meta["content_type"] = response.headers.get("content-type", "")
                meta["headers_ms"] = round((time.perf_counter() - started) * 1000, 1)
                print(json.dumps({"http_status": response.status_code, "content_type": meta["content_type"]}), flush=True)
                size = 0
                for chunk in response.iter_bytes():
                    if chunk and "first_body_ms" not in meta:
                        meta["first_body_ms"] = round((time.perf_counter() - started) * 1000, 1)
                    pieces.append(chunk)
                    size += len(chunk)
                    if size > 4_000_000 or time.perf_counter() - started > 180:
                        meta["capture_state"] = "local_limit_remote_state_unknown"
                        break
                else:
                    meta["capture_state"] = "transport_eof_protocol_completion_unverified"
    except Exception as exc:
        meta["capture_state"] = "request_error_no_retry"
        meta["error"] = str(exc).replace(credential, "[REDACTED]")
        meta["error_type"] = type(exc).__name__
    finally:
        meta["transport_total_ms"] = round((time.perf_counter() - started) * 1000, 1)
        meta["ended_at"] = datetime.now(timezone.utc).isoformat()
        raw = b"".join(pieces).decode("utf-8", errors="replace").replace(credential, "[REDACTED]")
        meta["captured_bytes"] = sum(map(len, pieces))
        (output_dir / "response.txt").write_text(raw, encoding="utf-8")
        (output_dir / "metadata.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({**meta, "output_dir": str(output_dir)}, ensure_ascii=False, indent=2), flush=True)
    return 1 if "error" in meta else 0


if __name__ == "__main__":
    raise SystemExit(main())
