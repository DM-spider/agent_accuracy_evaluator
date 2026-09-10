# -*- coding: utf-8 -*-
"""无真实智能体时的 20 题混合模拟包。问答稿是 Markdown，运行时按真实 HTTP 报文回放。"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from evaluator.paths import GOLDEN_DIR, TOOL_ROOT

MIXED_CASE_IDS: List[str] = [
    "CX01",
    "CX02",
    "CX03",
    "CX04",
    "CX05",
    "CX06",
    "CX09",
    "CX10",
    "RS03",
    "LS1",
    "LS4",
    "BJ2",
    "BJ4",
    "FB1",
    "GD1",
    "GD2",
    "GD3",
    "GD5",
    "SB3",
    "MI63",
]

# 10 题保持金标，10 题植入可检出的错误
PLANTED_ERRORS: Dict[str, Dict[str, str]] = {
    "CX03": {"kind": "WRONG_VALUE", "note": "莲塘累计产销差率 14.08% → 19.08%"},
    "CX04": {"kind": "WRONG_VALUE", "note": "南山实际产销差率 11.84% → 16.84%"},
    "CX06": {"kind": "WRONG_VALUE", "note": "深汕供水量 0 → 5，且只答了 10 行"},
    "CX09": {"kind": "WRONG_VALUE", "note": "南山产销差量 14,336,141.99 → 14,336,146.99"},
    "CX10": {"kind": "WRONG_VALUE", "note": "宝安率 5.35%→7.35%，集团率 7.55%→9.55%"},
    "LS4": {"kind": "MISSING", "note": "高漏耗小区只答 5 行，覆盖不足"},
    "BJ2": {"kind": "WRONG_VALUE", "note": "福田保税区波动标准差 61.59 → 91.59"},
    "GD2": {"kind": "WRONG_VALUE", "note": "本月工单完成率 93.28% → 83.28%"},
    "GD3": {"kind": "WRONG_VALUE", "note": "45 分钟到场及时率 34.09% → 54.09%"},
    "SB3": {"kind": "WRONG_VALUE", "note": "龙岗爆管次数 571 → 671"},
}

MIXED_ANSWERS_FILE = "agent_answers_mixed.json"
SCRIPT_NAME = "模拟智能体20题.md"
MOCK_AGENT_URL = "http://mock-agent/ask"
HEADING_RE = re.compile(r"^## ([A-Z]{2,}\d+)\b", re.M)


def script_path() -> Path:
    for candidate in (
        TOOL_ROOT / "docs" / SCRIPT_NAME,
        GOLDEN_DIR / SCRIPT_NAME,
        Path.cwd() / "docs" / SCRIPT_NAME,
    ):
        if candidate.exists():
            return candidate
    return TOOL_ROOT / "docs" / SCRIPT_NAME


def demo_pack() -> Dict:
    correct = [cid for cid in MIXED_CASE_IDS if cid not in PLANTED_ERRORS]
    return {
        "id": "mixed-20",
        "title": "模拟 20 题（10 对 10 错）",
        "case_ids": list(MIXED_CASE_IDS),
        "correct_case_ids": correct,
        "planted_errors": PLANTED_ERRORS,
        "answers_file": MIXED_ANSWERS_FILE,
        "script_path": str(script_path()),
        "script_name": SCRIPT_NAME,
        "mock_url": MOCK_AGENT_URL,
    }


def parse_mock_script(text: str) -> Dict[str, Dict[str, str]]:
    """从问答稿解析每题的问题与智能体返回正文。"""
    matches = list(HEADING_RE.finditer(text or ""))
    out: Dict[str, Dict[str, str]] = {}
    for idx, match in enumerate(matches):
        case_id = match.group(1)
        start = match.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        block = text[start:end]
        question = ""
        q_match = re.search(r"\*\*问题：\*\*\s*(.+)", block)
        if q_match:
            question = q_match.group(1).strip()
        answer = ""
        a_match = re.search(r"### 智能体返回\s*(.*)", block, re.S)
        if a_match:
            answer = a_match.group(1).strip()
            answer = re.sub(r"\n---\s*$", "", answer).strip()
        out[case_id] = {"question": question, "answer": answer, "block": block.strip()}
    return out


def load_script_answers(path: Optional[Path] = None) -> Dict[str, str]:
    target = path or script_path()
    parsed = parse_mock_script(target.read_text(encoding="utf-8"))
    return {cid: item["answer"] for cid, item in parsed.items() if item.get("answer")}


PROSE = {
    "CX01": "按评测基准时间，集团本月采用单月口径（最新完整业务月）。产销差率=(供水量-售水量)/供水量×100%，集团口径为深圳本地剔除布吉水司。结果如下。",
    "CX02": "集团近6个月产销差按年累计口径连续观察。各月供水量、售水量和累计产销差率如下，后两个期数数值持平，趋势走平。",
    "CX03": "各分公司今年1-5月累计产销差率由高到低排列如下。莲塘供水居首。",
    "CX04": "各分公司产销差目标完成情况如下。完成率=实际产销差率/目标产销差率×100%；差距为百分点。南山实际率偏高，完成率超过100%。",
    "CX05": "集团本月供水量、售水量如下，口径与产销差率题一致（单月、剔除布吉水司）。",
    "CX06": "各分公司本月产销差明细如下。深汕本月供水量已回填，其余单位按单月口径列出。",
    "CX09": "各单位累计产销差量对全市产销差的影响权重如下。南山贡献最大。",
    "CX10": "宝安分公司产销差率采用集团口径，并与集团整体对比，差距为百分点。",
    "RS03": "本区域产销差相对变化如下：同比、环比为百分点，目标比为完成率。",
    "LS1": "各分公司综合漏损率由高到低如下，同时给出漏损量和供水量。",
    "LS4": "本月居民小区高漏耗名单按月均漏损率排序，下面列出当前看到的前几名。",
    "BJ2": "近7天夜间流量波动最大的 DMA 如下，按波动标准差最大值排序。",
    "BJ4": "DMA 夜间流量预警处置情况如下。超时口径为 10 天内未处理。",
    "FB1": "指定 DMA 最新一日总分表偏差如下。偏差率大于 10% 需核查、大于 20% 告警。",
    "GD1": "近30天供水管道维修工单按流转状态统计如下。",
    "GD2": "本月相对上月的工单完成率如下。完成率=已完成工单数/工单总数。",
    "GD3": "本月维抢修 45 分钟到场及时率如下，并附超时工单 Top10。",
    "GD5": "上一月各水务运营中心检漏效率对比如下。检漏效率=检漏工单数/供水管道维修工单数。",
    "SB3": "本月各分公司爆管事件（事故类型含“爆”）统计如下。",
    "MI63": "本月已派发检漏工单闭环情况如下，按流转状态列出数量和完成数。",
}


def write_mock_script(cases: List[Dict], dest: Optional[Path] = None, *, anchor_time: str = "2026-08-17T10:00:00+08:00") -> Path:
    """把 20 题写成可读问答稿：每题都有请求 JSON 和智能体返回正文。"""
    dest = dest or (TOOL_ROOT / "docs" / SCRIPT_NAME)
    dest.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# 模拟智能体 20 题问答稿",
        "",
        "这份稿子是 **真实智能体尚未接入时** 的模拟对话。每题都按真实调用形状写成：",
        "",
        "1. **用户请求**：`POST http://mock-agent/ask`，正文只含问题、题号、基准时间，不含 SQL。",
        "2. **智能体返回**：自然语言说明 + Markdown 数据表（与线上智能体最终回答同一形态，不是纯 JSON 指标对象）。",
        "",
        "评测运行时会按题号回放这份返回正文，并在详情页展示请求/响应对照。",
        "",
        "| 题号 | 预期 | 植入差异 |",
        "|---|---|---|",
    ]
    for case in cases:
        case_id = case["case_id"]
        planted = PLANTED_ERRORS.get(case_id)
        mark = "植入错误" if planted else "正确"
        note = planted["note"] if planted else "与金标一致"
        lines.append(f"| {case_id} | {mark} | {note} |")
    lines += ["", "---", ""]
    for case in cases:
        case_id = case["case_id"]
        question = case.get("question_asked") or case.get("question") or ""
        table = (case.get("final_answer_text") or "").strip()
        planted = PLANTED_ERRORS.get(case_id)
        title = "植入错误" if planted else "正确"
        request = {
            "question": question,
            "case_id": case_id,
            "anchor_time": anchor_time,
            "timezone": "Asia/Shanghai",
        }
        prose = PROSE.get(case_id, "查询结果如下。")
        extra = f"\n\n> 本题模拟错误：{planted['note']}" if planted else ""
        lines += [
            f"## {case_id} · {title}",
            "",
            f"**问题：** {question}",
            "",
            "### 用户请求",
            "",
            "```http",
            f"POST {MOCK_AGENT_URL}",
            "Content-Type: application/json",
            "```",
            "",
            "```json",
            json.dumps(request, ensure_ascii=False, indent=2),
            "```",
            "",
            "### 智能体返回",
            "",
            prose,
            "",
            table,
            extra,
            "",
            "---",
            "",
        ]
    dest.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    golden_copy = GOLDEN_DIR / SCRIPT_NAME
    if GOLDEN_DIR.exists():
        golden_copy.write_text(dest.read_text(encoding="utf-8"), encoding="utf-8")
    return dest


def build_agent_exchange(case_id: str, question: str, answer: str, *, anchor_time: str, timezone_name: str, model: str = "mock-mixed-agent") -> Tuple[Dict, Dict]:
    request = {
        "question": question,
        "case_id": case_id,
        "anchor_time": anchor_time,
        "timezone": timezone_name,
    }
    response = {
        "ok": True,
        "model": model,
        "case_id": case_id,
        "answer": answer,
    }
    return request, response
