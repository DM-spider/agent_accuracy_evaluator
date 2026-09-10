# -*- coding: utf-8 -*-
from decimal import Decimal

from evaluator.sql_executor import SqlExecutor, SqlGuardError, normalize_sql_cell, validate_sql


def test_allows_select_and_cte():
    assert validate_sql("SELECT 1")
    assert validate_sql("WITH a AS (SELECT 1) SELECT * FROM a")


def test_rejects_dml_ddl_and_multi_statement():
    for sql in (
        "DELETE FROM t",
        "UPDATE t SET a=1",
        "DROP TABLE t",
        "SELECT 1; SELECT 2",
        "INSERT INTO t VALUES (1)",
        "CREATE TABLE t(a int)",
    ):
        try:
            validate_sql(sql)
            raise AssertionError(sql)
        except SqlGuardError:
            pass


class FakeCursor:
    def __init__(self, rows, columns, fail=None):
        self.rows = rows
        self.description = [(c,) for c in columns]
        self.fail = fail
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        if self.fail:
            raise self.fail

    def fetchmany(self, n):
        return self.rows[:n]

    def fetchall(self):
        return self.rows


class FakeConn:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor

    def close(self):
        pass


def test_timeout_and_row_limit_return_structured_error():
    cursor = FakeCursor(rows=[{"a": i} for i in range(10)], columns=["a"])
    executor = SqlExecutor(connect=lambda: FakeConn(cursor), max_rows=3)
    snap = executor.query("SELECT a FROM t WHERE id=:period", {"period": 1})
    assert snap.error is None
    assert snap.row_count == 3
    assert snap.truncated is True
    assert "password" not in (snap.executed_sql or "").lower()

    cursor2 = FakeCursor(rows=[], columns=["a"], fail=TimeoutError("statement timeout"))
    executor2 = SqlExecutor(connect=lambda: FakeConn(cursor2), timeout_seconds=1)
    snap2 = executor2.query("SELECT 1")
    assert snap2.error
    assert "timeout" in snap2.error.lower()


def test_normalize_sql_cell_rounds_and_keeps_period_int():
    assert normalize_sql_cell("rate", Decimal("0.079047223856482756")) == 0.0790
    assert normalize_sql_cell("gap", 4.72238564827449e-05) == 0.0000
    assert normalize_sql_cell("supply", "157439172.490000000000000000") == 157439172.4900
    assert normalize_sql_cell("月份(期数)", Decimal("202608.000000000000000000")) == 202608
    assert normalize_sql_cell("cnt", 13) == 13
