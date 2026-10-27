# -*- coding: utf-8 -*-
"""回答表格与 SQL 基准的确定性比对。"""
from evaluator.models import CaseContract, MeasureSpec, Tolerance
from evaluator.reconcile import parse_tables, reconcile, verdict_from_report


def _contract(measures, row_key=("组织",), result_type="detail"):
    return CaseContract(
        case_id="T-001",
        question="各单位指标是多少？",
        result_type=result_type,
        numeric_evaluable=True,
        realtime_ready=True,
        sql_template="SELECT 1",
        row_key=list(row_key),
        measures=measures,
    )


def _volume():
    return MeasureSpec(
        label="漏损水量", unit="m³", value_scale="volume", sql_column="维修漏损水量",
        tolerance=Tolerance(kind="rel", eps=0.001, abs_floor=0.01),
    )


ANSWER = """## 结论

| 单位 | 维修漏损水量（m³） |
|---|---|
| 南山分公司 | 5,794.95 |
| 福田分公司 | 3,011.92 |
| **合计** | **8,806.87** |
"""

SQL_ROWS = [
    {"组织": "南山分公司", "维修漏损水量": 5794.95},
    {"组织": "福田分公司", "维修漏损水量": 3011.92},
]


def test_parse_markdown_tables():
    tables = parse_tables(ANSWER)
    assert len(tables) == 1
    assert tables[0]["header"] == ["单位", "维修漏损水量（m³）"]
    assert len(tables[0]["rows"]) == 3


def test_all_rows_hit_ignores_extra_aggregate_row():
    report = reconcile(_contract({"维修漏损水量": _volume()}), ANSWER, SQL_ROWS)
    assert report["available"] is True
    assert report["row_hit_rate"] == 1.0
    assert report["value_hit_rate"] == 1.0
    assert report["extra_rows"] == []  # 合计行是允许的补充
    assert verdict_from_report(report) == "QUALIFIED"


def test_partial_rows_give_partial_verdict():
    answer = """| 单位 | 维修漏损水量 |
|---|---|
| 南山分公司 | 5,794.95 |
| 福田分公司 | 9,999.00 |
"""
    report = reconcile(_contract({"维修漏损水量": _volume()}), answer, SQL_ROWS)
    assert report["row_hit_rate"] == 1.0
    assert report["value_hit_rate"] == 0.5
    assert verdict_from_report(report) == "PARTIAL"


def test_missing_row_is_reported():
    answer = """| 单位 | 维修漏损水量 |
|---|---|
| 南山分公司 | 5,794.95 |
"""
    report = reconcile(_contract({"维修漏损水量": _volume()}), answer, SQL_ROWS)
    assert report["row_hit_rate"] == 0.5
    assert [item["key"] for item in report["missing_rows"]] == ["福田分公司"]
    assert verdict_from_report(report) == "PARTIAL"


def test_wan_header_scales_cells():
    spec = _volume()
    sql = [{"组织": "龙华水务集团", "维修漏损水量": 1583700}]
    answer = """| 单位 | 维修漏损水量（万 m³） |
|---|---|
| 龙华水务集团 | 158.37 |
"""
    report = reconcile(_contract({"维修漏损水量": spec}), answer, sql)
    assert report["value_hit_rate"] == 1.0
    assert verdict_from_report(report) == "QUALIFIED"


def test_month_key_from_requested_periods():
    spec = MeasureSpec(label="供水量", unit="m³", value_scale="volume", sql_column="供水量")
    contract = _contract({"供水量": spec}, row_key=("月份(期数)",))
    answer = """| 月份 | 供水量 |
|---|---|
| 1月 | 2,515,137 |
"""
    sql = [{"月份(期数)": 202601, "供水量": 2515137}]
    report = reconcile(contract, answer, sql, {"period_0": 202601})
    assert report["row_hit_rate"] == 1.0
    assert report["value_hit_rate"] == 1.0


def test_org_alias_and_parenthetical_suffix():
    spec = _volume()
    contract = _contract({"维修漏损水量": spec})
    answer = """| 单位 | 维修漏损水量 |
|---|---|
| 大鹏环水公司 | 289.4 |
| 其他（数据归口未细分） | 141 |
"""
    sql = [{"组织": "大鹏新区分公司", "维修漏损水量": 289.4}]
    report = reconcile(contract, answer, sql)
    assert report["row_hit_rate"] == 1.0
    assert [item["key"] for item in report["extra_rows"]] == ["其他"]


def test_no_table_falls_back_to_llm():
    report = reconcile(_contract({"维修漏损水量": _volume()}), "全集团合计 8,806.87 立方米。", SQL_ROWS)
    assert report["available"] is False
    assert report["reason"] == "no_table"
    assert verdict_from_report(report) is None


def test_ratio_value_tolerates_scale_ambiguity():
    spec = MeasureSpec(
        label="同比变化", unit="pp", value_scale="ratio01", sql_column="同比变化",
        tolerance=Tolerance(kind="abs", eps=0.01),
    )
    contract = _contract({"同比变化": spec})
    answer = """| 单位 | 同比变化 |
|---|---|
| 深汕水务 | -0.59 |
"""
    sql = [{"组织": "深汕水务", "同比变化": -0.0060}]
    report = reconcile(contract, answer, sql)
    assert report["value_hit_rate"] == 1.0


def test_extra_non_aggregate_row_caps_verdict_at_partial():
    answer = """| 单位 | 维修漏损水量 |
|---|---|
| 南山分公司 | 5,794.95 |
| 福田分公司 | 3,011.92 |
| 龙城分公司 | 315.44 |
"""
    report = reconcile(_contract({"维修漏损水量": _volume()}), answer, SQL_ROWS)
    assert report["row_hit_rate"] == 1.0
    assert [item["key"] for item in report["extra_rows"]] == ["龙城分公司"]
    assert verdict_from_report(report) == "PARTIAL"
