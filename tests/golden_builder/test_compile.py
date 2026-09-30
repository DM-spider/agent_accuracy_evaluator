# -*- coding: utf-8 -*-
from golden_builder.catalog import CASES
from golden_builder.compile import compile_all, finalize_select


def test_compile_all_141_builders():
    compiled = compile_all()
    assert len(compiled) == 141
    builders = {c["builder"] for c in CASES}
    used = {spec["builder"] for spec, sql, _ in compiled}
    assert builders == used
    for spec, sql, contract in compiled:
        assert sql.strip(), spec["case_id"]
        assert contract["sql_template"] == sql
        assert contract["case_id"] == spec["case_id"]
        assert contract["parameter_resolver"] == spec["parameter_resolver"]


def test_compiled_sql_rounds_and_uses_simplified_chinese_aliases():
    compiled = {spec["case_id"]: (spec, sql, contract) for spec, sql, contract in compile_all()}
    spec, sql, contract = compiled["CX-001"]
    assert 'ROUND((_g.rate)::numeric, 4) AS "产销差率"' in sql
    assert "*100" not in sql.replace(" ", "")
    assert contract["measures"]["产销差率"]["sql_column"] == "产销差率"
    _, sql3, c3 = compiled["CX-003"]
    assert 'AS "单月供水量"' in sql3 and 'AS "单月售水量"' in sql3
    assert "ROUND(" in sql3
    assert c3["measures"]["单月供水量"]["sql_column"] == "单月供水量"
    _, sql8, _ = compiled["CX-008"]
    assert 'AS "月份(期数)"' in sql8
    assert "CAST(_g." in sql8 and "AS int" in sql8
    assert sql8.rstrip().endswith('ORDER BY 1 ASC, 1 ASC') or "ORDER BY 1" in sql8.split(") _g")[-1]
    _, sql6, c6 = compiled["CX-006"]
    assert 'AS "年累计产销差率上年同期值"' in sql6 and 'AS "年累计产销差率同比变化"' in sql6
    assert c6["measures"]["年累计产销差率同比变化"]["sql_column"] == "年累计产销差率同比变化"


def test_compiled_sql_uses_business_names_and_comparison_context():
    compiled = {spec["case_id"]: (spec, sql, contract) for spec, sql, contract in compile_all()}
    _, sql5, c5 = compiled["CX-005"]
    assert "比率" not in sql5
    assert 'AS "年累计产销差率"' in sql5
    assert 'AS "产销差率年度目标"' in sql5
    assert 'AS "产销差率目标差距"' in sql5
    assert "年度考核目标" in c5["measures"]["产销差率年度目标"]["aliases"]
    _, sql7, _ = compiled["CX-007"]
    assert 'AS "组织"' in sql7
    assert 'AS "本期月份"' in sql7
    assert 'AS "上期月份"' in sql7
    assert 'AS "本期单月产销差率"' in sql7
    assert 'AS "上期单月产销差率"' in sql7
    assert 'AS "单月产销差率环比变化"' in sql7
    assert "上月值" not in sql7
    for spec, sql, _ in compile_all():
        for banned in ("比率", "上月值", '"差距"', '"占比"', '"目标"'):
            assert banned not in sql, spec["case_id"]


def test_finalize_select_keeps_inner_english_aliases():
    spec = next(c for c in CASES if c["case_id"] == "CX-001")
    inner = "SELECT (SUM(f.totalproduct)-SUM(f.totalconsume))/NULLIF(SUM(f.totalproduct),0) AS rate FROM t"
    out = finalize_select(inner, spec)
    assert "AS rate FROM t" in out
    assert out.strip().startswith("SELECT ROUND")
