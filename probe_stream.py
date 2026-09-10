"""Send one authorized stream request and capture protocol evidence without retries."""
from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx


URL = "https://aiemployee.sz-water.com.cn/cc-control/api/user/mid-platform/session/message/stream"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--org-id", required=True)
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--template-id", required=True)
    parser.add_argument("--task-name", default="leakage-skill")
    parser.add_argument("--message", required=True)
    args = parser.parse_args()
    credential = os.environ.get("EVAL_PLATFORM_LOGIN_SESSION", "")
    if not credential:
        parser.error("EVAL_PLATFORM_LOGIN_SESSION is required")
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
