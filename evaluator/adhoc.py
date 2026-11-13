# -*- coding: utf-8 -*-
"""临时题：黄金集之外的输入题，用固定占位题提供 SQL 基准。

黄金集末尾的 TMP-000 只做基准占位：SQL 固定返回 -999999。
输入题能在黄金集里精确匹配到题面时沿用原题契约；匹配不到就套用 TMP-000，
评测仍走正常链路（取数、比对、LLM 评估），只是结论必然不合格。
"""
from __future__ import annotations

from typing import Dict, Iterable, List, Sequence

from evaluator.models import CaseContract, MeasureSpec, Tolerance

ADHOC_SCENE = "临时题"
FALLBACK_CASE_ID = "TMP-000"
FALLBACK_PREFIX = "TMP-"
FALLBACK_QUESTION = "未收录问题（临时题占位）"
FALLBACK_METRIC = "未收录占位值"
FALLBACK_VALUE = -999999.0
FALLBACK_SQL = f'SELECT {int(FALLBACK_VALUE)} AS "{FALLBACK_METRIC}"'
FALLBACK_CALIBER = "临时题占位：无专属 SQL 基准，SQL 固定返回未收录占位值 -999999；回答给不出该值即不合格。"
MAX_ADHOC_QUESTIONS = 50


def is_adhoc_case(case_id: str) -> bool:
    return str(case_id or "").startswith(FALLBACK_PREFIX)


def fallback_contract() -> CaseContract:
    """占位题契约；若黄金集里已有 TMP-000，以数据集里的定义为准。"""
    return CaseContract(
        case_id=FALLBACK_CASE_ID,
        question=FALLBACK_QUESTION,
        result_type="stat",
        numeric_evaluable=True,
        realtime_ready=True,
        sql_template=FALLBACK_SQL,
        parameter_resolver="none",
        measures={
            FALLBACK_METRIC: MeasureSpec(
                label=FALLBACK_METRIC,
                sql_column=FALLBACK_METRIC,
                unit="",
                value_scale="count",
                tolerance=Tolerance(kind="abs", eps=0.0001),
            )
        },
        scene_big=ADHOC_SCENE,
        category="TMP",
        indicator_type="未收录问题占位",
        caliber=FALLBACK_CALIBER,
        notes=FALLBACK_CALIBER,
    )


def _norm(text: str) -> str:
    return "".join(str(text or "").split())


def build_adhoc_contracts(questions: Sequence[str], templates: Iterable[CaseContract]) -> List[CaseContract]:
    """把粘贴的题面转成本批次契约；匹配不到黄金题时套用 TMP-000。"""
    cleaned: List[str] = []
    for raw in questions:
        text = str(raw or "").strip()
        if text and text not in cleaned:
            cleaned.append(text)
    if not cleaned:
        raise ValueError("请至少输入一道题")
    if len(cleaned) > MAX_ADHOC_QUESTIONS:
        raise ValueError(f"一次最多 {MAX_ADHOC_QUESTIONS} 道题")

    templates = list(templates)
    fallback = next((c for c in templates if c.case_id == FALLBACK_CASE_ID), None) or fallback_contract()
    by_question: Dict[str, CaseContract] = {}
    for contract in templates:
        if contract.numeric_evaluable and not is_adhoc_case(contract.case_id):
            by_question.setdefault(_norm(contract.question), contract)

    out: List[CaseContract] = []
    for index, question in enumerate(cleaned, 1):
        base = by_question.get(_norm(question)) or fallback
        contract = base.model_copy(
            deep=True,
            update={"case_id": f"TMP-{index:03d}", "question": question, "scene_big": ADHOC_SCENE, "category": "TMP"},
        )
        if base.case_id == FALLBACK_CASE_ID:
            contract.caliber = FALLBACK_CALIBER
            contract.notes = FALLBACK_CALIBER
        out.append(contract)
    return out
