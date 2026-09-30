# -*- coding: utf-8 -*-
import json
from pathlib import Path

import pytest

from golden_builder.compile import compile_all, ensure_pg8000_safe


def _published_contracts():
    """已发布契约：评测工具根目录 config/contracts.json。"""
    return Path(__file__).resolve().parents[2] / "config" / "contracts.json"


PUBLISHED_CONTRACTS = _published_contracts()


def test_bare_select_param_is_rejected():
    sql = 'SELECT \'环水集团\' AS "组织", :period AS "月份(期数)", rate FROM t WHERE ym=:period'
    with pytest.raises(ValueError, match="CAST"):
        ensure_pg8000_safe(sql, "T-001")


def test_cast_wrapped_and_double_colon_params_pass():
    # 已有显式类型的写法放行：CAST 包裹、:: 强转、WHERE 中的普通参数
    ensure_pg8000_safe(
        'SELECT CAST(:period AS int) AS "月份(期数)", rate FROM t WHERE ym=:period',
        "T-002",
    )
    ensure_pg8000_safe(
        'SELECT :period::int AS "月份", (rate)::numeric AS rate FROM t',
        "T-003",
    )


def test_all_compiled_sql_is_pg8000_safe():
    # finalize_select 内部已调用 ensure_pg8000_safe；这里兜底全量编译即守卫生效
    for spec, sql, _ in compile_all():
        ensure_pg8000_safe(sql, spec["case_id"])


def test_business_work_order_window_is_create_time_without_funnel():
    """工单类 SQL 期间一律 create_time 半开区间（GD-001~GD-011）。"""
    wo_cases = {
        "JL-016", "JL-017", "JL-018", "JL-023", "JL-024",
        "JL-026", "JL-027", "JL-028", "JL-029", "JL-030",
        "JL-031", "JL-032", "JL-033", "JL-034", "JL-035", "JL-036",
        "BJ-008", "BJ-009", "BJ-010", "BJ-011", "BJ-012", "BJ-013", "BJ-014", "BJ-015",
        "LS-023", "LS-024",
    }
    for spec, sql, _ in compile_all():
        if spec["case_id"] not in wo_cases:
            continue
        assert "sfyxgd" not in sql, spec["case_id"]
        assert "finish_time>=" not in sql, spec["case_id"]
        assert "create_time>=" in sql, spec["case_id"]


def test_bj012_keeps_address_column():
    for spec, sql, _ in compile_all():
        if spec["case_id"] != "BJ-012":
            continue
        assert "detailedaddress" in sql
        assert 'AS "地址"' in sql


def test_published_contracts_are_pg8000_safe():
    if not PUBLISHED_CONTRACTS.exists():
        pytest.skip("contracts.json 尚未生成")
    payload = json.loads(PUBLISHED_CONTRACTS.read_text(encoding="utf-8"))
    for contract in payload["contracts"]:
        ensure_pg8000_safe(contract["sql_template"], contract["case_id"])


def test_all_compiled_sql_forbids_percent_scale_and_month_periodtype():
    for spec, sql, _ in compile_all():
        compact = sql.replace(" ", "")
        assert "*100" not in compact, spec["case_id"]
        if spec["category"] == "CX":
            assert "periodtype='Month'" not in sql, spec["case_id"]
            assert "SzwgBusiness" in sql, spec["case_id"]
        if spec["org_scope"] == "GROUP_TOTAL" and spec["category"] == "CX":
            assert "F1014" not in sql, spec["case_id"]
        if spec["result_type"] == "detail":
            assert "ORDER BY" in sql.upper(), spec["case_id"]
        if spec["case_id"].startswith("BJ-"):
            assert "zonename" not in sql.lower(), spec["case_id"]
        if spec["case_id"] == "BJ-001":
            assert "NightFlow" in sql
        if spec["case_id"] == "BJ-002":
            assert "bz_bm" in sql or "CASE" in sql
        if spec["case_id"] == "JL-016":
            assert "water_leakage" in sql
            assert "leakage_quantity" not in sql
        if spec["case_id"] == "LS-001":
            assert "dm_yyzbtx_gggsgwlsl" in sql
        if any(item.get("value_scale") in {"percent", "pp", "volume"} for item in spec["measures"]):
            assert "ROUND(" in sql, spec["case_id"]
        for item in spec["measures"]:
            assert 'AS "%s"' % item["key"] in sql, spec["case_id"]
