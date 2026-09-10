# -*- coding: utf-8 -*-
from evaluator.models import MeasureSpec, Tolerance
from evaluator.normalizer import parse_number


def test_percent_and_ratio_and_volume():
    percent = MeasureSpec(label="产销差率", unit="%", value_scale="percent", tolerance=Tolerance())
    ratio = MeasureSpec(label="产销差率", unit="%", value_scale="ratio01", tolerance=Tolerance())
    volume = MeasureSpec(label="漏损水量", unit="m³", value_scale="volume", tolerance=Tolerance(kind="rel", eps=0.001))
    v1, u1, e1 = parse_number("11.84%", percent)
    v2, u2, e2 = parse_number("11.8400", percent)
    v3, u3, e3 = parse_number("0.1184", ratio)
    assert e1 is None and abs(v1 - 11.84) < 1e-9
    assert parse_number("11.84％", percent)[0] == 11.84
    assert abs(v2 - 11.84) < 1e-9
    assert abs(v3 - 11.84) < 1e-9
    w1, _, _ = parse_number("2.3万方", volume)
    w2, _, _ = parse_number("23,000 m³", volume)
    w3, u3, _ = parse_number("102,857 万立方米", volume)
    w4, _, _ = parse_number("102,857 万", volume)
    assert abs(w1 - 23000) < 1e-6
    assert abs(w2 - 23000) < 1e-6
    assert abs(w3 - 1028570000) < 1e-6
    assert u3 == "m³"
    assert abs(w4 - 1028570000) < 1e-6


def test_pp_sign_and_empty_not_zero():
    pp = MeasureSpec(label="同比", unit="pp", value_scale="pp")
    v, u, err = parse_number("同比下降1.2个百分点", pp)
    assert u == "pp"
    assert abs(v + 1.2) < 1e-9
    below, _, _ = parse_number("低于目标4.09个百分点", pp)
    above, _, _ = parse_number("高于目标4.09个百分点", pp)
    assert abs(below + 4.09) < 1e-9
    assert abs(above - 4.09) < 1e-9
    empty, _, e2 = parse_number("--", percent if False else MeasureSpec(label="x", unit="%"))
    assert empty is None
    assert e2 is None


def test_count_exact_integer():
    spec = MeasureSpec(label="工单数", unit="count", value_scale="count")
    v, u, _ = parse_number("12", spec)
    assert v == 12
    assert u == "count"


def test_ratio01_sql_side_is_deterministically_scaled():
    # v3 契约：SQL 原始值是 0-1 比值（黄金集同源），评测端按声明语义 ×100，不靠量级猜测
    spec = MeasureSpec(label="产销差率", unit="%", value_scale="ratio01")
    v, u, err = parse_number("0.1184", spec, "sql")
    assert err is None and u == "%" and abs(v - 11.84) < 1e-9
    # 完成率可 >1（1.2 即 120%），不能按 |v|≤1 猜
    v, u, _ = parse_number("1.2", spec, "sql")
    assert u == "%" and abs(v - 120) < 1e-9
    # 数据库科学计数法输出（实测出现过 4.72e-05）
    v, u, _ = parse_number("4.72e-05", spec, "sql")
    assert u == "%" and abs(v - 0.00472) < 1e-12


def test_ratio01_agent_side_respects_unit_words_and_magnitude():
    spec = MeasureSpec(label="产销差率", unit="%", value_scale="ratio01")
    assert parse_number("0.125", spec, "agent")[0] == 12.5   # 裸小数 ×100
    assert parse_number("12.5", spec, "agent")[0] == 12.5    # 裸数>1 视为已是百分数读数
    assert parse_number("12.5%", spec, "agent")[0] == 12.5   # 带%不放大
    assert parse_number("0.5‰", spec, "agent")[0] == 0.05    # ‰ 已换算，不再 ×100


def test_ratio01_pp_contract_lands_in_pp_space():
    spec = MeasureSpec(label="单月产销差率环比变化", unit="pp", value_scale="ratio01")
    v, u, _ = parse_number("-0.016", spec, "sql")
    assert u == "pp" and abs(v + 1.6) < 1e-9
    v, u, _ = parse_number("下降1.6个百分点", spec, "agent")
    assert u == "pp" and abs(v + 1.6) < 1e-9


def test_value_scale_rule_mentions_canonical_target():
    from evaluator.normalizer import value_scale_rule
    pct = MeasureSpec(label="产销差率", unit="%", value_scale="ratio01")
    pp = MeasureSpec(label="环比变化", unit="pp", value_scale="ratio01")
    assert "百分数" in value_scale_rule(pct)
    assert "百分点" in value_scale_rule(pp)
