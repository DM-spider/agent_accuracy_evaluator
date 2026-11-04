# -*- coding: utf-8 -*-
"""过程评测：把执行记录里的信号翻译成「问题 + 建议」。

只做确定性判断，不调用 LLM，也不改变结果评分口径。输出面向业务读者：
哪里出了问题、下一步该查什么。真正的对话质量仍由 llm_evaluation.json 决定。
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

REFUSAL_RE = re.compile(
    r"(NEED_LLM|NEED_HOST_RUNTIME|取数.{0,8}不可用|无法取数|取不到|没有实时数值|无法给出数值|不估算|不给估算)"
)
SEVERITY_ORDER = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}


def _finding(code: str, severity: str, title: str, detail: str) -> Dict[str, str]:
    return {"code": code, "severity": severity, "title": title, "detail": detail}


def _repeated_calls(steps: List[Dict[str, Any]]) -> int:
    seen: Dict[str, int] = {}
    for step in steps:
        for call in step.get("calls") or []:
            key = json.dumps(
                {"code": call.get("code"), "params": call.get("params") or {}},
                ensure_ascii=False, sort_keys=True, default=str,
            )
            seen[key] = seen.get(key, 0) + 1
    return sum(count - 1 for count in seen.values() if count > 1)


def review_process(
    *,
    summary: Optional[Dict[str, Any]] = None,
    counts: Optional[Dict[str, Any]] = None,
    steps: Optional[List[Dict[str, Any]]] = None,
    completion_status: str = "",
    error: str = "",
    answer_text: str = "",
) -> Dict[str, Any]:
    summary = summary or {}
    counts = counts or {}
    steps = steps or []

    tool_calls = int(counts.get("tool_calls") or 0)
    shells = int(counts.get("shell") or 0)
    empty = int(counts.get("empty") or 0)
    errors = int(counts.get("error") or 0)
    timeouts = int(counts.get("timeout") or 0)
    pending = int(counts.get("pending") or 0)
    data_calls = max(tool_calls - shells, 0)
    repeats = _repeated_calls(steps)

    findings: List[Dict[str, str]] = []
    suggestions: List[str] = []

    if completion_status and completion_status != "completed":
        findings.append(_finding(
            "EXECUTION_INCOMPLETE", "HIGH", "本轮没有拿到完整终态",
            f"回答状态为 {completion_status}，过程记录可能被截断，结论不完整。",
        ))
        suggestions.append("确认平台侧任务已结束后再重跑；不确定远端状态时不要直接当作回答错误处理。")

    if error:
        findings.append(_finding(
            "EXECUTION_ERROR", "HIGH", "执行中断",
            f"智能体调用以错误结束：{error}。",
        ))

    if REFUSAL_RE.search(answer_text or ""):
        findings.append(_finding(
            "AGENT_DECLARED_UNAVAILABLE", "HIGH", "回答声明取数通道不可用",
            "回答里出现取数不可用/NEED_LLM 等声明，说明这次是环境或接线问题，不是问法问题。",
        ))
        suggestions.append("先确认问数通道与宿主运行时是否就绪，再跑下一批；否则整批都会被记成答非所问。")

    if timeouts:
        findings.append(_finding(
            "TOOL_TIMEOUT", "HIGH", f"{timeouts} 次工具调用超时",
            "有命令跑到整体超时被终止，那一轮的结果不可信。",
        ))
        suggestions.append("把跨月或全年的取数拆成逐月查询，或与平台确认单条命令的超时上限。")

    if errors:
        findings.append(_finding(
            "TOOL_ERROR", "HIGH", f"{errors} 次工具调用失败",
            "命令返回失败终态或非 SUCCESS 业务码。",
        ))
        suggestions.append("先按命令契约校验参数（命令码、必填项、覆盖范围），再决定是否换命令。")

    if tool_calls and empty / max(tool_calls, 1) >= 0.4:
        findings.append(_finding(
            "EMPTY_RESULT_HIGH", "MEDIUM", "空结果占比偏高",
            f"{tool_calls} 次调用里有 {empty} 次返回空，可能是参数写法问题，也可能确实无数据。",
        ))
        suggestions.append("先核对维度值与期间写法，再判断是否真无数据，别把参数错误当成业务事实。")

    if repeats:
        findings.append(_finding(
            "REPEATED_CALLS", "MEDIUM", f"{repeats} 次完全重复的调用",
            "相同命令码与参数被反复调用，可能是在重试同一个失败请求。",
        ))

    if tool_calls >= 20:
        findings.append(_finding(
            "MANY_STEPS", "MEDIUM", f"步骤偏多（{tool_calls} 步）",
            "取数过程中转次数较多，时延和出错概率都会上升。",
        ))
        suggestions.append("先收敛指标口径与组织范围，再一次性取数；避免用试参数代替口径确认。")

    if shells >= 5 and (data_calls == 0 or shells > data_calls):
        findings.append(_finding(
            "EXPLORATION_HEAVY", "MEDIUM", "执行以环境探索为主",
            f"命令类步骤 {shells} 次、取数类 {data_calls} 次，说明这一轮主要在自检和探查，没有真正取数。",
        ))
        suggestions.append("确认问数入口是否走的是取数主链；环境自检不应占用正式测评的轮次。")

    if pending:
        findings.append(_finding(
            "PENDING_STEPS", "LOW", f"{pending} 步没有返回",
            "步骤在断流时没有收到结果，状态未知。",
        ))

    findings.sort(key=lambda item: SEVERITY_ORDER.get(item["severity"], 9))
    suggestions = list(dict.fromkeys(suggestions))

    if not tool_calls and not summary.get("thoughts") and not summary.get("notes"):
        verdict = "PROCESS_UNKNOWN"
    elif any(item["severity"] == "HIGH" for item in findings):
        verdict = "PROCESS_RISK"
    elif findings:
        verdict = "PROCESS_ATTENTION"
    else:
        verdict = "PROCESS_HEALTHY"

    return {
        "verdict": verdict,
        "findings": findings,
        "suggestions": suggestions,
        "stats": {
            "tool_calls": tool_calls,
            "data_calls": data_calls,
            "shell_calls": shells,
            "empty": empty,
            "errors": errors,
            "timeouts": timeouts,
            "repeats": repeats,
        },
    }
