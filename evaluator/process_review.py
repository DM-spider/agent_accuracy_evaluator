# -*- coding: utf-8 -*-
"""过程评测：把执行记录翻译成「哪里有问题 + 具体证据 + 下一步查什么」。

只做确定性判断，不调用 LLM，也不改变结果评分口径。输出面向业务读者：
先给一句人话结论，再列出是哪几次调用、什么状态、查的什么。
真正的对话质量仍由 llm_evaluation.json 决定。
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

REFUSAL_RE = re.compile(
    # 只认「环境/通道」层面的不可用：接口字段缺失、命令没授权属于口径问题，
    # 由结果对比判断，不能记成执行故障。
    r"(NEED_LLM|NEED_HOST_RUNTIME|取数(?:通道|服务|链路|入口)\s*(?:不可用|未就绪|异常|失败)"
    r"|宿主运行时.{0,6}(?:不可用|未就绪))"
)
SEVERITY_ORDER = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
SEVERITY_LABELS = {"HIGH": "需要处理", "MEDIUM": "建议关注", "LOW": "提示"}
STATUS_LABELS = {"empty": "返回空", "error": "失败", "timeout": "超时", "pending": "未返回", "ok": "成功"}

# 判断一次调用是不是在取数：有结构化调用，或命令里出现取数客户端/查询特征。
DATA_MARKERS = (
    "clitransport",
    "cli_client",
    "t.call(",
    ".call(",
    "invoke",
    "metric",
    "select ",
    " sql",
    "query",
    "bz_bm",
    "water_",
    "loss_",
    "dma",
)
CALL_TARGET_RE = re.compile(r"\.call\(\s*['\"][^'\"]+['\"]\s*,\s*['\"]([^'\"]+)['\"]")


def _finding(code: str, severity: str, title: str, detail: str, evidence: Optional[List[str]] = None) -> Dict[str, Any]:
    return {
        "code": code,
        "severity": severity,
        "severity_label": SEVERITY_LABELS.get(severity, severity),
        "title": title,
        "detail": detail,
        "evidence": list(evidence or []),
    }


def _repeated_calls(steps: List[Dict[str, Any]]) -> int:
    seen: Dict[str, int] = {}
    for step in steps:
        for call in step.get("calls") or []:
            key = json.dumps(
                {"code": call.get("code"), "params": call.get("params") or {}},
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            )
            seen[key] = seen.get(key, 0) + 1
    return sum(count - 1 for count in seen.values() if count > 1)


def _step_target(step: Dict[str, Any]) -> str:
    """这条命令查的是什么：优先结构化调用码，其次命令里的接口名。"""
    for call in step.get("calls") or []:
        code = str(call.get("code") or "").strip()
        if code:
            return code
    match = CALL_TARGET_RE.search(str(step.get("command") or ""))
    if match:
        return match.group(1)
    for code in step.get("codes") or []:
        if code:
            return str(code)
    return ""


def _looks_like_data(step: Dict[str, Any]) -> bool:
    if step.get("calls"):
        return True
    command = str(step.get("command") or "").lower()
    return any(marker in command for marker in DATA_MARKERS)


def _call_records(steps: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    records = []
    for index, step in enumerate(steps, start=1):
        if not isinstance(step, dict):
            continue
        records.append(
            {
                "index": index,
                "t_ms": step.get("t_ms"),
                "status": str(step.get("status") or ""),
                "target": _step_target(step),
                "data_like": _looks_like_data(step),
            }
        )
    return records


def _ref(record: Dict[str, Any]) -> str:
    parts = [f"第 {record['index']} 次调用"]
    if record.get("t_ms") is not None:
        try:
            parts.append(f"{float(record['t_ms']) / 1000:.1f} 秒")
        except (TypeError, ValueError):
            pass
    parts.append(STATUS_LABELS.get(record["status"], record["status"] or "状态未知"))
    if record.get("target"):
        parts.append(record["target"])
    return " · ".join(parts)


def _refs(records: List[Dict[str, Any]], limit: int = 5) -> List[str]:
    return [_ref(record) for record in records[:limit]]


def review_from_process(
    process: Dict[str, Any],
    answer_text: str = "",
    timings: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """按当前规则重算一份过程评测（用于展示，不写回存档）。

    旧批次没有 NoResponseHeaders 这个错误码；如果读超时且从未收到响应头，
    补上同样的判定，避免历史题继续显示成普通 ReadTimeout。
    """
    process = process or {}
    summary = process.get("summary") or {}
    steps = process.get("steps")
    if not steps:
        steps = [item["step"] for item in process.get("timeline") or [] if item.get("kind") == "step"]
    error = str(summary.get("error") or "")
    timings = timings or {}
    if "ReadTimeout" in error and timings.get("headers_ms") is None:
        error = "STREAM_ERROR:NoResponseHeaders"
    return review_process(
        summary=summary,
        counts=process.get("counts") or {},
        steps=steps or [],
        completion_status=str(summary.get("completion_status") or ""),
        error=error,
        answer_text=answer_text,
    )


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
    repeats = _repeated_calls(steps)
    calls = _call_records(steps)
    data_calls = sum(1 for record in calls if record["data_like"])
    empty_calls = [record for record in calls if record["status"] == "empty"]
    error_calls = [record for record in calls if record["status"] == "error"]
    timeout_calls = [record for record in calls if record["status"] == "timeout"]
    pending_calls = [record for record in calls if record["status"] == "pending"]

    findings: List[Dict[str, Any]] = []
    observations: List[str] = []
    suggestions: List[str] = []
    no_response = "NoResponseHeaders" in str(error or "")

    if no_response:
        findings.append(
            _finding(
                "PLATFORM_NO_RESPONSE",
                "HIGH",
                "平台没有响应，这一轮大概率没发出去",
                "客户端一直没收到平台的任何数据，连 HTTP 响应头都没有。"
                "这通常意味着任务没有在平台上创建（会话或任务已失效），刚才等的时间不会产出结果。",
            )
        )
        suggestions.append("重新从浏览器复制 cURL 覆盖 platform.curl（会话可能已过期），确认平台侧能看到这条消息后再跑。")

    if completion_status and completion_status != "completed" and not no_response:
        findings.append(
            _finding(
                "EXECUTION_INCOMPLETE",
                "HIGH",
                "这一轮没有正常结束",
                f"回答状态是 {completion_status}，执行记录可能被截断，后面的结论不一定可信。",
            )
        )
        suggestions.append("确认平台侧任务已经结束后再重跑；不确定远端状态时，不要直接当成回答错误。")

    if error and not no_response:
        findings.append(
            _finding(
                "EXECUTION_ERROR",
                "HIGH",
                "执行中途报错",
                f"智能体调用以错误结束：{error}。这一轮的结论建立在一次不完整的执行上。",
            )
        )

    if REFUSAL_RE.search(answer_text or ""):
        findings.append(
            _finding(
                "AGENT_DECLARED_UNAVAILABLE",
                "HIGH",
                "回答说取数通道不可用",
                "回答里出现了「取数不可用 / NEED_LLM」这类说法。这通常是环境或接线问题，不是问法问题；"
                "同一批里如果多题都这样，先修通道再测评。",
            )
        )
        suggestions.append("先确认问数通道与宿主运行时是否就绪，再跑下一批；否则整批都会被记成答非所问。")

    if timeouts:
        detail = "有命令跑到超时被终止，这几次的结果不能算数。"
        if timeout_calls:
            detail = f"有 {timeouts} 次调用跑到超时被终止，这几次的结果不能算数。具体是："
        findings.append(
            _finding("TOOL_TIMEOUT", "HIGH", f"{timeouts} 次调用超时", detail, _refs(timeout_calls))
        )
        suggestions.append("把跨月或全年的查询拆成逐月，或与平台确认单条命令的超时上限。")

    if errors:
        detail = "命令返回了失败状态——不是「查到了空」，而是根本没查成。"
        if error_calls:
            detail = f"有 {errors} 次调用返回失败状态，不是「查到了空」，而是根本没查成。具体是："
        findings.append(
            _finding("TOOL_ERROR", "HIGH", f"{errors} 次调用失败", detail, _refs(error_calls))
        )
        suggestions.append("按命令契约检查参数（命令码、必填项、覆盖范围），再决定是否换命令。")

    if tool_calls and empty / max(tool_calls, 1) >= 0.4:
        detail = f"{tool_calls} 次工具调用里有 {empty} 次没有返回数据。"
        targets = [record["target"] for record in empty_calls]
        if empty_calls and len(set(targets)) == 1 and targets[0]:
            detail = f"{tool_calls} 次工具调用里有 {empty} 次没有返回数据，都发生在「{targets[0]}」。"
        last_empty = max((record["index"] for record in empty_calls), default=0)
        recovered = any(record["status"] == "ok" and record["index"] > last_empty for record in calls)
        if recovered:
            # 试错是正常策略：只要后续根据反馈调整并取到数，就不算过程问题。
            first_ok = next(record["index"] for record in calls if record["status"] == "ok" and record["index"] > last_empty)
            observations.append(
                f"第 {'、'.join(str(record['index']) for record in empty_calls)} 次调用返回空，"
                f"第 {first_ok} 次调用成功取到数据；属于拿到反馈后调整参数的试探过程。"
            )
        elif len(empty_calls) >= 2:
            detail += "后面没有再成功取到数据，这一轮可能没有完成取数。"
            findings.append(
                _finding("EMPTY_UNRESOLVED", "MEDIUM", f"{empty} 次调用返回空，之后没有取到数据", detail, _refs(empty_calls))
            )
            suggestions.append("先核对必填参数、单位名称和时间格式；如果确认参数无误，再判断业务上是否真的没有数据。")
        elif len(empty_calls) == 1:
            # 只查了一次、结果为空：可能参数不对，也可能业务上确实没有数据，不能当过程问题。
            observations.append(
                f"第 {empty_calls[0]['index']} 次调用返回空；只查了一次，无法判断是参数问题还是业务上确实没有数据。"
            )
        else:
            # 旧存档没有逐次调用状态，只能按计数提示。
            findings.append(
                _finding(
                    "EMPTY_RESULT_HIGH",
                    "MEDIUM",
                    f"{empty} 次调用没有返回数据",
                    detail + "旧存档没有逐次调用明细，无法判断这些空结果是否属于正常试探。",
                )
            )

    if repeats:
        findings.append(
            _finding(
                "REPEATED_CALLS",
                "MEDIUM",
                f"{repeats} 次重复调用",
                "相同的命令和参数被反复调用，通常是在重试同一个失败请求，时间和额度都花在了重复动作上。",
            )
        )

    if tool_calls >= 20:
        findings.append(
            _finding(
                "MANY_STEPS",
                "MEDIUM",
                f"调用次数偏多（{tool_calls} 次）",
                "一次问数用了这么多次工具调用，中间环节越多，超时和出错的机会越大。",
            )
        )
        suggestions.append("先把指标口径和组织范围定下来，再一次性取数，别用试参数代替口径确认。")

    if shells >= 5 and data_calls == 0:
        findings.append(
            _finding(
                "EXPLORATION_HEAVY",
                "MEDIUM",
                "这一轮没有真正发起取数",
                f"{shells} 次调用都是命令行自检，没有出现取数客户端或查询指令。"
                "正式测评的轮次不应该花在这些动作上。",
            )
        )
        suggestions.append("确认问数入口走的是取数主链；环境自检不要占用正式测评的轮次。")

    if pending:
        findings.append(
            _finding(
                "PENDING_STEPS",
                "LOW",
                f"{pending} 次调用没有返回结果",
                "流在收到结果前断了，这几步有没有执行、结果是什么都不确定。",
                _refs(pending_calls),
            )
        )

    findings.sort(key=lambda item: SEVERITY_ORDER.get(item["severity"], 9))
    suggestions = list(dict.fromkeys(suggestions))

    if any(item["severity"] == "HIGH" for item in findings):
        verdict = "PROCESS_RISK"
    elif not tool_calls and not summary.get("thoughts") and not summary.get("notes"):
        verdict = "PROCESS_UNKNOWN"
    elif findings:
        verdict = "PROCESS_ATTENTION"
    else:
        verdict = "PROCESS_HEALTHY"

    return {
        "verdict": verdict,
        "findings": findings,
        "observations": observations,
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
