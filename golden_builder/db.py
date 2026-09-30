# -*- coding: utf-8 -*-
"""标准集构建 · 只读数据库连接工具。

铁律：
- 对 ai_agent 库只做 SELECT，绝不执行任何写操作（不建表/不写临时表）。
- 凭据只从 `config/settings.toml` 读取（与评测工具同源），不硬编码。
- 每次查询走独立连接，会话级只读 + 自动提交。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import psycopg2

_CONN: Optional[Dict[str, Any]] = None


def _conn_params() -> Dict[str, Any]:
    global _CONN
    if _CONN is not None:
        return _CONN
    from evaluator.paths import CONFIG_DIR
    from evaluator.settings import db_password, load_settings

    settings = load_settings()
    database = settings.get("database") or {}
    password = db_password(settings)
    if not database.get("host") or not database.get("user") or not password:
        raise RuntimeError("数据库未配置：请填写 %s 的 [database] 段" % (CONFIG_DIR / "settings.toml"))
    _CONN = dict(
        host=database.get("host"),
        port=int(database.get("port") or 5432),
        dbname=database.get("name") or "ai_agent",
        user=database.get("user"),
        password=password,
        connect_timeout=int(database.get("connect_timeout") or 15),
        # 外部表（ODPS FDW）偶发慢查询：单语句 10 分钟超时，避免整批挂死
        options="-c statement_timeout=600000",
    )
    return _CONN


def query(sql: str, params: Any = None) -> Tuple[List[str], List[tuple]]:
    """执行只读查询，返回 (列名列表, 行列表)。

    注意：无参数时直接 execute(sql)，避免 psycopg2 把 SQL 中的 '%'（LIKE 通配符）
    当作占位符解析。
    """
    conn = psycopg2.connect(**_conn_params())
    try:
        conn.set_session(readonly=True, autocommit=True)
        cur = conn.cursor()
        if params:
            cur.execute(sql, params)
        else:
            cur.execute(sql)
        rows = cur.fetchall()
        cols = [d[0] for d in cur.description]
        cur.close()
        return cols, rows
    finally:
        conn.close()


def get_latest_periods() -> Dict[str, Optional[int]]:
    """取产销差表最新可用期数（供时间解析的“最新可用”判断）。"""
    _, rows = query(
        """
        SELECT
          MAX(CASE WHEN periodtype='SzwgBusinessYear' THEN businessyearmonth END) AS latest_ym_year,
          MAX(CASE WHEN periodtype='SzwgBusiness'     THEN businessyearmonth END) AS latest_ym_month
        FROM dwd_lsxt_fqcxfx
        """
    )
    r = rows[0]
    return {"SzwgBusinessYear": int(r[0]) if r[0] is not None else None,
            "SzwgBusiness": int(r[1]) if r[1] is not None else None}


if __name__ == "__main__":
    cols, rows = query("SELECT 1")
    print("self-check:", cols, rows)
    print("latest_periods:", get_latest_periods())
