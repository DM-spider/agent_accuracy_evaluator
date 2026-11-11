# -*- coding: utf-8 -*-
"""把执行时间轴压成一段按步骤的中文回顾。

分工：
- 模型只描述「每次调用在做什么」（action），不写结果、不写数字；
- 结果由程序从输出里抽出的 probes（标签 → 行数/错误码）填入。

这样既保留人话，也不会出现模型把 4308 读成 0 的情况。判定仍由
process_review 的确定性规则给出，叙事不参与判定。
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from evaluator.process_review import _step_target

MAX_ITEMS = 40
TEXT_LIMIT = 220
MAX_LINES = 8
ACTION_LIMIT = 80

STATUS_LABELS = {"ok": "成功", "empty": "空结果", "error": "失败", "timeout": "超时", "pending": "未返回"}

PROBE_ARROW = re.compile(
    r"([A-Za-z0-9_\-+().= /]{2,32}?)\s*->\s*"
    r"(?:\{\s*\"respCode\"\s*:\s*\"([A-Za-z0-9_]+)\""
    r"|\{\s*\"data\".*?\"totalRows\"\s*:\s*(\d+)"
    r"|totalRows\s*:\s*(\d+))",
    re.S,
)
PROBE_DIRECT = re.compile(r"([A-Za-z][A-Za-z0-9_\-+().= /]{1,31}?)\s+totalRows\s*:\s*(\d+)")
LEADING_NUMBER = re.compile(r"^\d+\s+")

SYSTEM_PROMPT = """你是执行过程记录员。给定一条执行时间轴，为每一次「调用」写一句中文说明，讲清这次调用在做什么。
只输出 JSON：{"walk": [{"t": 14.3, "action": "先用 basic、periodtype 两种方式调接口"}]}。

规则：
1. t 必须等于时间轴里某个「调用」的秒数；每条调用最多一句，按时间顺序。
2. action 只写「在做什么」，不超过 60 字；**不要写结果、不要写数字、不要写行数**，结果由程序填。
3. 只能依据时间轴里的 action/命令/推理内容，不要猜时间轴里没有的动机。
4. 不评价对错，不给建议。"""


def _seconds(value: Any) -> Optional[float]:
    try:
        return round(float(value) / 1000, 1)
    except (TypeError, ValueError):
        return None


def extract_probes(text: str) -> List[Dict[str, str]]:
    """从输出摘录里抽出「标签 → 结果」，一次调用可能有多个探测。"""
    probes: List[Dict[str, str]] = []
    seen = set()
    for match in PROBE_ARROW.finditer(text or ""):
        label = LEADING_NUMBER.sub("", match.group(1).strip())
        value = match.group(2) or match.group(3) or match.group(4)
        kind = "code" if match.group(2) else "rows"
        key = (label, value, kind)
        if label and key not in seen:
            seen.add(key)
            probes.append({"label": label, "value": str(value), "kind": kind})
    for match in PROBE_DIRECT.finditer(text or ""):
        label = LEADING_NUMBER.sub("", match.group(1).strip())
        key = (label, match.group(2), "rows")
        if label and key not in seen:
            seen.add(key)
            probes.append({"label": label, "value": str(match.group(2)), "kind": "rows"})
    return probes[:8]


def _step_item(entry: Dict[str, Any]) -> Dict[str, Any]:
    step = entry.get("step") or {}
    output = str(step.get("excerpt") or "").strip()
    probes = extract_probes(output)
    item = {
        "t": _seconds(entry.get("t_ms")),
        "status": step.get("status"),
        "rows": step.get("rows"),
        "target": _step_target(step),
        "probes": probes,
    }
    if not probes:
        item["output"] = output[:TEXT_LIMIT]
    return item


def _timeline_items(process: Dict[str, Any]) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for entry in (process.get("timeline") or [])[:MAX_ITEMS]:
        kind = entry.get("kind")
        if kind == "thought":
            text = str(entry.get("text") or "").strip()
            if text:
                items.append({"t": _seconds(entry.get("t_ms")), "kind": "思考", "text": text[:TEXT_LIMIT]})
        elif kind == "step":
            item = _step_item(entry)
            item["kind"] = "调用"
            items.append(item)
    return items


def build_messages(process: Dict[str, Any], question: str = "") -> List[Dict[str, str]]:
    payload = {
        "question": question,
        "summary": process.get("summary") or {},
        "timeline": _timeline_items(process),
    }
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False, default=str)},
    ]


def _probe_text(probe: Dict[str, str]) -> str:
    if probe.get("kind") == "code":
        return f"{probe['label']} 错误码 {probe['value']}"
    return f"{probe['label']} {probe['value']} 行"


def _result_text(step: Dict[str, Any]) -> str:
    probes = step.get("probes") or []
    if probes:
        return "、".join(_probe_text(probe) for probe in probes[:4])
    status = str(step.get("status") or "")
    label = STATUS_LABELS.get(status, status or "状态未知")
    rows = step.get("rows")
    return f"{label}（{rows} 行）" if rows is not None else label


def _match_step(items: List[Dict[str, Any]], value: Any) -> Optional[Dict[str, Any]]:
    try:
        target = float(value)
    except (TypeError, ValueError):
        return None
    steps = [item for item in items if item.get("kind") == "调用" and item.get("t") is not None]
    if not steps:
        return None
    best = min(steps, key=lambda item: abs(float(item["t"]) - target))
    return best if abs(float(best["t"]) - target) <= 1.0 else None


def build_walk(raw: Any, items: List[Dict[str, Any]]) -> Optional[List[str]]:
    """把模型给的 action 与程序抽取的结果拼成最终回顾行。"""
    if not isinstance(raw, list):
        return None
    lines: List[str] = []
    used = set()
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        action = str(entry.get("action") or "").strip()[:ACTION_LIMIT]
        if not action:
            continue
        step = _match_step(items, entry.get("t"))
        if step is None or id(step) in used:
            continue
        used.add(id(step))
        lines.append(f"{step['t']:.1f} 秒：{action} → {_result_text(step)}")
    return lines[:MAX_LINES] or None


def validate_narrative(data: Any, items: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not isinstance(data, dict):
        return None
    walk = build_walk(data.get("walk"), items)
    return {"walk": walk} if walk else None


def needs_narrative(process: Dict[str, Any]) -> bool:
    """只有需要解释的轮次才调用模型：有过程问题/观察，或一次调用里试了多个参数。"""
    review = process.get("review") or {}
    if review.get("findings") or review.get("observations"):
        return True
    steps = [step for step in (process.get("steps") or []) if isinstance(step, dict)]
    if len(steps) < 2:
        return False
    return any(len(extract_probes(str(step.get("excerpt") or ""))) >= 2 for step in steps)


def narrate(llm: Any, process: Dict[str, Any], question: str = "") -> Optional[Dict[str, Any]]:
    """调用评估模型生成动作说明；任何失败都返回 None，不影响批次。"""
    if llm is None or not getattr(llm, "configured", False):
        return None
    items = _timeline_items(process)
    if not any(item.get("kind") == "调用" for item in items):
        return None
    try:
        data = llm.complete_json(build_messages(process, question), max_tokens=1200)
    except Exception:
        return None
    return validate_narrative(data, items)
