# -*- coding: utf-8 -*-
"""JL-016 类明细表问题回归：表头分级映射、派生合计比较、组织上下级合并诊断。"""
from evaluator.comparator import DERIVED_TOTAL_NOTE
from evaluator.extractor import metric_for_label, table_header_mapping
from evaluator.models import CaseContract, CoveragePolicy, MeasureSpec
from evaluator.table_comparison import table_comparison

LEAK_ALIASES = [
    "维修漏损水量", "loss_vol", "维修工单漏损水量", "供水管道维修工单漏损水量",
    "工单漏损水量", "漏损水量", "漏损水量(m³)", "漏损量",
]


def detail_contract(measures=None, aliases=LEAK_ALIASES):
    if measures is None:
        measures = {"维修漏损水量": MeasureSpec(
            label="维修漏损水量", sql_column="loss_vol", unit="m³",
            aliases=aliases, value_scale="volume")}
    return CaseContract(
        case_id="JL-016", question="上月各单位供水管道维修工单记录的漏损水量合计是多少立方米？",
        result_type="detail", numeric_evaluable=True, row_key=["组织"],
        measures=measures, coverage_policy=CoveragePolicy(),
    )


def _detail(text, rows):
    return {
        "agent_answer": {"text": text},
        "sql_snapshot": {"columns": list(rows[0].keys()), "rows": rows},
    }


def _items_by_org(info):
    return {(i.get("coordinates") or {}).get("组织", ""): i for i in info["items"]}


HEADER_TABLE = """上月各单位供水管道维修工单记录的漏损水量如下：

| 组织 | 维修工单数 | 漏损水量（m³） |
|---|---|---|
| 南山 | 120 | 4,763.60 |
| 深水宝安 | 300 | 13,484.53 |
"""


def test_leakage_header_maps_via_contract_alias():
    """智能体表头「漏损水量（m³）」应映射到契约指标「维修漏损水量」。"""
    contract = detail_contract()
    mapping = {row["source_column"]: row for row in table_header_mapping(HEADER_TABLE, contract)}
    leak = mapping["漏损水量（m³）"]
    assert leak["status"] == "MAPPED"
    assert leak["target_metric"] == "维修漏损水量"
    assert leak["target_kind"] == "measure"
    assert leak["mapping_method"] == "contract_alias"
    assert leak["confidence"] == "exact"
    assert mapping["组织"]["status"] == "MAPPED"
    assert mapping["组织"]["target_kind"] == "dimension"


def test_leakage_header_maps_via_metric_word_when_alias_missing():
    """契约只留「维修漏损水量」别名时，去单位 + 指标词包含仍能唯一映射。"""
    contract = detail_contract(aliases=["维修漏损水量", "loss_vol"])
    assert metric_for_label("漏损水量（m³）", contract) == "维修漏损水量"
    mapping = {row["source_column"]: row for row in table_header_mapping(HEADER_TABLE, contract)}
    leak = mapping["漏损水量（m³）"]
    assert leak["status"] == "MAPPED"
    assert leak["mapping_method"] == "metric_word"
    assert leak["confidence"] == "fuzzy"


def test_workorder_count_never_maps_to_volume_metric():
    """「维修工单数」既不能映射到漏损水量，也不得出现在候选里。"""
    contract = detail_contract()
    assert metric_for_label("维修工单数", contract) is None
    mapping = {row["source_column"]: row for row in table_header_mapping(HEADER_TABLE, contract)}
    workorder = mapping["维修工单数"]
    assert workorder["status"] == "NON_TARGET"
    assert workorder["candidate_metrics"] == []


def test_ambiguous_header_stays_unmapped_with_candidates():
    """多个候选指标时不猜测：UNMAPPED 并列出候选，交人工确认。"""
    contract = detail_contract(measures={
        "维修漏损水量": MeasureSpec(label="维修漏损水量", sql_column="loss_vol",
                                   unit="m³", aliases=["loss_vol"], value_scale="volume"),
        "管网漏损水量": MeasureSpec(label="管网漏损水量", sql_column="pipe_loss_vol",
                                    unit="m³", aliases=["pipe_loss_vol"], value_scale="volume"),
    })
    assert metric_for_label("漏损水量（m³）", contract) is None
    mapping = {row["source_column"]: row for row in table_header_mapping("| 漏损水量（m³） |\n|---|\n| 1 |", contract)}
    leak = mapping["漏损水量（m³）"]
    assert leak["status"] == "UNMAPPED"
    assert leak["candidate_metrics"] == ["管网漏损水量", "维修漏损水量"]


def test_unit_incompatible_header_not_auto_mapped():
    """单位类型不兼容（万元 vs m³）时不得自动映射。"""
    contract = detail_contract(measures={
        "管网漏损水量": MeasureSpec(label="管网漏损水量", sql_column="pipe_loss_vol",
                                    unit="m³", aliases=["loss_vol"], value_scale="volume")})
    mapping = {row["source_column"]: row for row in table_header_mapping("| 漏损水量（万元） |\n|---|\n| 1 |", contract)}
    leak = mapping["漏损水量（万元）"]
    assert leak["status"] == "UNMAPPED"
    assert leak["candidate_metrics"] == ["管网漏损水量"]


def test_value_pairing_with_separator_and_org_alias():
    """千分位、尾零和组织别名（深水宝安→宝安水务集团）正常配对比较。"""
    text = """| 组织 | 漏损水量（m³） |
|---|---|
| 南山 | 4,763.60 |
| 深水宝安 | 13,484.53 |
"""
    rows = [
        {"组织": "南山", "维修漏损水量": 4763.6000},
        {"组织": "宝安水务集团", "维修漏损水量": 13484.5311},
    ]
    info = table_comparison(detail_contract(), _detail(text, rows))
    by_org = _items_by_org(info)
    assert by_org["南山分公司"]["status"] == "MATCH"
    assert by_org["宝安水务集团"]["status"] == "MATCH_WITH_TOLERANCE"
    assert by_org["南山分公司"]["mapping_state"] == "PAIRED"


def test_derived_total_matches_sql_sum():
    """智能体合计行 vs SQL 明细求和：一致时派生比较通过，且不按 SQL 无对应值处理。"""
    text = """| 组织 | 漏损水量（m³） |
|---|---|
| 南山 | 4,763.60 |
| 深水宝安 | 13,484.53 |
| 合计 | 18,248.13 |
"""
    rows = [
        {"组织": "南山", "维修漏损水量": 4763.6000},
        {"组织": "宝安水务集团", "维修漏损水量": 13484.5311},
    ]
    info = table_comparison(detail_contract(), _detail(text, rows))
    derived = [i for i in info["items"] if i.get("note") == DERIVED_TOTAL_NOTE]
    assert len(derived) == 1
    item = derived[0]
    assert item["coordinates"] == {"组织": "合计"}
    assert item["status"] in ("MATCH", "MATCH_WITH_TOLERANCE")
    assert item["expected_value"] == 18248.1311
    assert item["mapping_state"] == "DERIVED_TOTAL"
    assert not [i for i in info["items"]
                if i["status"] == "NO_BENCHMARK" and (i.get("coordinates") or {}).get("组织") == "合计"]


def test_derived_total_mismatch_is_wrong_value():
    text = """| 组织 | 漏损水量（m³） |
|---|---|
| 南山 | 4,763.60 |
| 合计 | 99,999.00 |
"""
    rows = [{"组织": "南山", "维修漏损水量": 4763.6000}]
    info = table_comparison(detail_contract(), _detail(text, rows))
    derived = [i for i in info["items"] if i.get("note") == DERIVED_TOTAL_NOTE]
    assert len(derived) == 1 and derived[0]["status"] == "WRONG_VALUE"


def test_org_rollup_diagnosed_but_missing_preserved():
    """龙岗差值=坪地缺失值 → 提示疑似组织合并，但明细行仍按缺失计分。"""
    text = """| 组织 | 漏损水量（m³） |
|---|---|
| 南山 | 4,763.60 |
| 龙岗水务集团 | 3,732.60 |
"""
    rows = [
        {"组织": "南山", "维修漏损水量": 4763.6000},
        {"组织": "龙岗水务集团", "维修漏损水量": 3257.3000},
        {"组织": "坪地供水", "维修漏损水量": 475.3000},
    ]
    info = table_comparison(detail_contract(), _detail(text, rows))
    by_org = _items_by_org(info)
    missing = by_org["坪地供水"]
    assert missing["status"] == "MISSING"
    assert "POSSIBLE_ORG_ROLLUP" in missing.get("note", "")
    assert any("疑似组织合并" in issue and "坪地供水" in issue for issue in info["issues"])
    # 配对成功的行不受其他行缺失影响
    assert by_org["南山分公司"]["status"] == "MATCH"
    assert by_org["龙岗水务集团"]["status"] == "WRONG_VALUE"


def test_field_mapping_survives_without_saved_claims():
    """历史运行没有 agent_claims.json 时同样能即时给出字段映射诊断。"""
    text = """| 组织 | 漏损水量（m³） |
|---|---|
| 南山 | 4,763.60 |
"""
    rows = [{"组织": "南山", "维修漏损水量": 4763.6000}]
    info = table_comparison(detail_contract(), _detail(text, rows))
    assert info["field_mapping"]
    columns = {row["source_column"] for row in info["field_mapping"]}
    assert {"组织", "漏损水量（m³）"} <= columns
    assert info["extraction"]["paired"] == 1
