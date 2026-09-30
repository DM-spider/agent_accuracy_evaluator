# -*- coding: utf-8 -*-
from golden_builder.fragments import CX_RATE_AGG, WLLS_RATE_AGG, ZONE_GROUP_TOTAL, ZONE_LOCAL_13


def test_group_total_excludes_buji():
    assert "F1014" not in ZONE_GROUP_TOTAL
    assert "F1001" in ZONE_GROUP_TOTAL
    assert "F1014" in ZONE_LOCAL_13
    assert "'F1'" not in ZONE_LOCAL_13
    assert "'F100'" not in ZONE_LOCAL_13


def test_rate_expr_has_no_times_100():
    assert "*100" not in CX_RATE_AGG.replace(" ", "")
    assert "*100" not in WLLS_RATE_AGG.replace(" ", "")
    assert "NULLIF" in CX_RATE_AGG


def test_make_detail_uses_simplified_chinese_headers():
    from golden_builder.assemble import make_detail
    measures = [{"key": "漏损率", "sql_column": "rate", "value_scale": "percent"}]
    detail = make_detail(
        ["org", "rate", "dmaname"],
        [("南山分公司", 0.12, "某小区")],
        ["org"],
        measures,
    )
    row = detail["rows"][0]
    assert "漏损率" in row
    assert "组织" in row
    assert "DMA名称" in row
    assert "rate" not in row
    assert "org" not in row
    assert detail["key_fields"] == ["组织"]


def test_make_stat_rounds_ratio_and_volume_to_four_decimals():
    from golden_builder.assemble import make_stat
    measures = [
        {"key": "产销差率", "sql_column": "rate", "value_scale": "percent", "unit": "%"},
        {"key": "供水量", "sql_column": "supply", "value_scale": "volume", "unit": "m³"},
        {"key": "目标差距", "sql_column": "gap", "value_scale": "pp", "unit": "pp"},
    ]
    result = make_stat(
        ["产销差率", "供水量", "目标差距"],
        [(0.079047223856482756, 157439172.490000000000000000, 4.72238564827449e-05)],
        measures,
    )
    by_key = {f["key"]: f["value"] for f in result["fields"]}
    assert by_key["产销差率"] == 0.0790
    assert by_key["供水量"] == 157439172.4900
    assert by_key["目标差距"] == 0.0000


def test_make_detail_keeps_period_as_int():
    from golden_builder.assemble import make_detail
    measures = [{"key": "产销差率", "sql_column": "rate", "value_scale": "percent"}]
    detail = make_detail(
        ["月份(期数)", "产销差率"],
        [(202608.0, 0.082742)],
        ["月份(期数)"],
        measures,
    )
    assert detail["rows"][0]["月份(期数)"] == 202608
    assert detail["rows"][0]["产销差率"] == 0.0827


def test_make_detail_keeps_work_order_id_as_text():
    from golden_builder.assemble import make_detail
    measures = [{"key": "工单编号", "sql_column": "id", "value_scale": "text"}]
    detail = make_detail(
        ["工单编号", "组织"],
        [("WS20260731Y2320", "南山分公司")],
        ["工单编号"],
        measures,
    )
    assert detail["rows"][0]["工单编号"] == "WS20260731Y2320"
