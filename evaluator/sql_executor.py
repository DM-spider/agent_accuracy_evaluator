# -*- coding: utf-8 -*-
"""只读 SQL 执行器：单条 SELECT/CTE、超时、行数上限、参数绑定。"""
from __future__ import annotations

import re
import time
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Callable, Dict, Optional

from evaluator.models import SqlSnapshot
from evaluator.run_context import preview_sql, to_pg_params

_Q4 = Decimal("0.0001")
_NUMERIC_TEXT = re.compile(r"^-?\d+(\.\d+)?([eE][-+]?\d+)?$")

FORBIDDEN = re.compile(
    r"\b(INSERT|UPDATE|DELETE|MERGE|DROP|ALTER|TRUNCATE|COPY|CALL|CREATE|GRANT|REVOKE|VACUUM|EXECUTE|DO)\b",
    re.I,
)
SECRET_RE = re.compile(r"(?i)(password|passwd|token|api_key|apikey|secret)\s*[:=]\s*['\"]?[^,\s'\"]+")


class SqlGuardError(ValueError):
    pass


def sanitize_sql_log(sql: str) -> str:
    return SECRET_RE.sub(r"\1=***", sql or "")


def validate_sql(sql: str) -> str:
    text = (sql or "").strip()
    if not text:
        raise SqlGuardError("empty sql")
    # 拒绝分号后的第二条语句
    body = text
    if body.endswith(";"):
        body = body[:-1].rstrip()
    if ";" in body:
        raise SqlGuardError("multiple statements are not allowed")
    if FORBIDDEN.search(body):
        raise SqlGuardError("only SELECT / WITH ... SELECT is allowed")
    if not re.match(r"^\s*(SELECT|WITH)\b", body, re.I):
        raise SqlGuardError("only SELECT / WITH ... SELECT is allowed")
    return body


class SqlExecutor:
    def __init__(
        self,
        connect: Optional[Callable[[], Any]] = None,
        *,
        timeout_seconds: int = 30,
        max_rows: int = 5000,
        statement_timeout_ms: Optional[int] = None,
    ):
        self.connect = connect
        self.timeout_seconds = timeout_seconds
        self.max_rows = max_rows
        self.statement_timeout_ms = statement_timeout_ms or timeout_seconds * 1000

    def query(self, sql_template: str, params: Optional[Dict[str, Any]] = None) -> SqlSnapshot:
        params = params or {}
        started = time.perf_counter()
        try:
            template = validate_sql(sql_template)
            pg_sql, bound = to_pg_params(template, params)
            preview = sanitize_sql_log(preview_sql(template, bound))
        except (SqlGuardError, ValueError) as exc:
            return SqlSnapshot(
                sql_template=sql_template,
                params=_public_params(params),
                executed_sql=sanitize_sql_log(sql_template),
                error=str(exc),
                latency_ms=int((time.perf_counter() - started) * 1000),
            )
        if self.connect is None:
            return SqlSnapshot(
                sql_template=template,
                params=_public_params(bound),
                executed_sql=preview,
                error="database connection is not configured",
                latency_ms=int((time.perf_counter() - started) * 1000),
            )
        conn = None
        try:
            conn = self.connect()
            cursor = conn.cursor()
            cursor.execute("SET TRANSACTION READ ONLY")
            cursor.execute(f"SET statement_timeout = {int(self.statement_timeout_ms)}")
            cursor.execute(pg_sql, bound)
            columns = [d[0] for d in (cursor.description or [])]
            fetched = cursor.fetchmany(self.max_rows + 1)
            truncated = len(fetched) > self.max_rows
            fetched = fetched[: self.max_rows]
            rows = []
            for raw in fetched:
                if isinstance(raw, dict):
                    row = raw
                else:
                    row = {columns[i]: raw[i] if i < len(raw) else None for i in range(len(columns))}
                rows.append({key: normalize_sql_cell(key, value) for key, value in row.items()})
            return SqlSnapshot(
                sql_template=template,
                params=_public_params(bound),
                executed_sql=preview,
                columns=columns,
                rows=rows,
                row_count=len(rows),
                truncated=truncated,
                latency_ms=int((time.perf_counter() - started) * 1000),
            )
        except Exception as exc:
            return SqlSnapshot(
                sql_template=template,
                params=_public_params(bound),
                executed_sql=preview,
                error=_public_error(exc),
                latency_ms=int((time.perf_counter() - started) * 1000),
            )
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass


def normalize_sql_cell(key: Any, value: Any) -> Any:
    """期数转整数；非整数度量保留四位小数。"""
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, (datetime, date)):
        return value
    decimal_value = None
    if isinstance(value, Decimal):
        decimal_value = value
    elif isinstance(value, float):
        decimal_value = Decimal(str(value))
    elif isinstance(value, str) and _NUMERIC_TEXT.match(value.strip()):
        try:
            decimal_value = Decimal(value.strip())
        except InvalidOperation:
            return value
    if decimal_value is None:
        return value
    integral = decimal_value == decimal_value.to_integral_value()
    as_int = int(decimal_value) if integral else None
    if as_int is not None and 200001 <= as_int <= 209912 and 1 <= as_int % 100 <= 12:
        return as_int
    if integral and as_int is not None and abs(as_int) >= 1:
        return as_int
    try:
        return float(decimal_value.quantize(_Q4, rounding=ROUND_HALF_UP))
    except InvalidOperation:
        return value


def _public_params(params: Dict[str, Any]) -> Dict[str, Any]:
    hidden = {"password", "token", "secret", "api_key"}
    return {k: ("***" if k.lower() in hidden else v) for k, v in params.items()}


def _public_error(exc: Exception) -> str:
    return sanitize_sql_log(str(exc))


def make_pg_connector(settings: Dict[str, Any], password: str) -> Callable[[], Any]:
    db = settings.get("database") or {}

    def _connect():
        import pg8000.dbapi as pg8000

        pg8000.paramstyle = "pyformat"

        return pg8000.connect(
            host=db.get("host"),
            port=int(db.get("port") or 80),
            database=db.get("name"),
            user=db.get("user"),
            password=password,
            ssl_context=False,
            timeout=int(db.get("connect_timeout") or 15),
        )

    return _connect
