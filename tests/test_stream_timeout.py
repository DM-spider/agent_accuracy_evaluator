import socket
import threading
import time
from datetime import datetime, timezone

from evaluator.models import CaseContract, RunContext
from evaluator.stream_client import StreamAgentClient


def _context() -> RunContext:
    return RunContext(
        run_id="r1",
        anchor_time=datetime(2026, 11, 6, tzinfo=timezone.utc),
        current_month="202611",
        previous_month="202610",
        current_month_start="2026-11-01",
        next_month_start="2026-12-01",
        current_month_end="2026-11-30",
        previous_month_start="2026-10-01",
        previous_month_end="2026-10-31",
        year_start="2026-01-01",
        today="2026-11-06",
        yesterday="2026-11-05",
        yoy_month="202510",
    )


def _case() -> CaseContract:
    return CaseContract(case_id="C1", question="q", result_type="stat", numeric_evaluable=True)


def test_no_response_headers_fails_fast():
    """平台连响应头都不回时，应按 headers_timeout 熔断，而不是等满读超时。"""
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]
    accepted = threading.Event()

    def serve():
        try:
            conn, _ = server.accept()
            accepted.set()
            conn.recv(65536)
            time.sleep(3)
            conn.close()
        except OSError:
            pass

    threading.Thread(target=serve, daemon=True).start()
    try:
        client = StreamAgentClient(
            session_id="s",
            task_id="t",
            template_id="x",
            org_id="o",
            login_session="cookie",
            url=f"http://127.0.0.1:{port}/stream",
            headers_timeout=0.3,
            read_timeout=30,
            total_timeout=30,
        )
        started = time.time()
        answer = client.ask(_case(), _context())
        elapsed = time.time() - started
    finally:
        server.close()

    assert accepted.is_set()
    assert elapsed < 2
    assert answer.error == "STREAM_ERROR:NoResponseHeaders"
    assert answer.completion_status == "unknown"
    assert answer.timings.get("headers_timeout_hit") is True
