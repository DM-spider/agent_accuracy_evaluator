# -*- coding: utf-8 -*-
from collections import Counter

from golden_builder.catalog import CASES


def test_141_unique_ids_and_questions():
    assert len(CASES) == 141
    ids = [c["case_id"] for c in CASES]
    assert len(set(ids)) == 141
    assert len({c["question"] for c in CASES}) == 141
    assert Counter(c["category"] for c in CASES) == {"CX": 37, "LS": 25, "DMA": 26, "JL": 36, "BJ": 17}
    assert ids[0] == "CX-001" and ids[-1] == "BJ-017"


def test_business_25q_cases_present():
    # 2026-09-21 内化业务核对过的 25 题：补录题号必须存在
    ids = {c["case_id"] for c in CASES}
    assert {"CX-036", "CX-037", "DMA-026", "JL-036", "BJ-016", "BJ-017"} <= ids


def test_detail_has_row_key_and_forbidden_words():
    banned = ("看板", "SQL", "底表", "QBI", "百分点")
    for c in CASES:
        for word in banned:
            assert word not in c["question"], c["case_id"]
        if c["result_type"] == "detail":
            assert c["row_key"], c["case_id"]
        assert c["builder"] and c["parameter_resolver"], c["case_id"]
        assert c["measures"], c["case_id"]
