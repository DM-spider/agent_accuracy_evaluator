# -*- coding: utf-8 -*-
"""把一组问题逐条问给当前配置的业务平台智能体，导出「问题 | 结果」两列 Excel。

用法：
    python ask_questions_table.py --questions temp/questions_12.txt --out temp/回答_20261111.xlsx

要点：
- 复用 evaluator 的平台客户端与 platform.curl 配置；
- 平台同一会话串行排队，每题前先等会话空闲，避免拿到上一题的流；
- 提问后按 session/history 校准，只认「本轮开始时间之后」的轮次，避免同名旧题串进来；
- 结果原样写入 B 列，不截断、不改写。
"""
from __future__ import annotations

import argparse
import io
import json
import math
import os
import sys
import time
import unicodedata
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import httpx
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from evaluator.agent_client import client_from_settings
from evaluator.models import CaseContract
from evaluator.paths import TOOL_ROOT
from evaluator.run_context import build_run_context
from evaluator.settings import load_settings

PLATFORM_BASE = "https://assistant.sz-water.com.cn"
MAX_ROW_HEIGHT = 409.5
MAX_CELL_CHARS = 32000


def _utf8_stdout() -> None:
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)


def _norm(text: str) -> str:
    return "".join(str(text or "").split())


def _platform_headers(platform: Dict[str, Any]) -> Dict[str, str]:
    return {
        "accept": "application/json",
        "Cookie": f"SESSION_ID={platform.get('_login_session')}",
        "x-session-id": platform.get("_login_session") or "",
        "x-org-id": platform.get("org_id") or "",
        "origin": platform.get("origin") or "",
        "referer": platform.get("referer") or "",
        "content-type": "application/json",
    }


def _client(platform: Dict[str, Any]) -> httpx.Client:
    return httpx.Client(timeout=60, trust_env=bool(platform.get("trust_env", False)))


def wait_until_idle(platform: Dict[str, Any], timeout_seconds: float, interval: float = 15.0) -> bool:
    body = {"sessionId": platform.get("session_id"), "taskId": platform.get("task_id")}
    headers = _platform_headers(platform)
    deadline = time.time() + max(0.0, timeout_seconds)
    with _client(platform) as client:
        while True:
            try:
                status = client.post(PLATFORM_BASE + "/cc-control/api/user/mid-platform/session/status", json=body, headers=headers).json()
                pending = client.post(
                    PLATFORM_BASE + "/cc-control/api/user/mid-platform/task/pending-sessions", json=body, headers=headers
                ).json()
                state = str((status.get("data") or {}).get("sessionStatus") or "")
                queued = len((pending.get("data") or {}).get("list") or [])
            except Exception as exc:
                print(f"  会话状态查询失败（{type(exc).__name__}），按空闲继续", flush=True)
                return True
            if state != "运行中" and queued == 0:
                return True
            if time.time() > deadline:
                return False
            print(f"  平台仍在执行上一轮（状态 {state or '未知'}，排队 {queued} 条），等待空闲…", flush=True)
            time.sleep(interval)


def fetch_history(platform: Dict[str, Any]) -> List[dict]:
    body = {"sessionId": platform.get("session_id"), "taskId": platform.get("task_id")}
    headers = _platform_headers(platform)
    records: List[dict] = []
    offset = 0
    with _client(platform) as client:
        while True:
            payload = client.post(
                PLATFORM_BASE + "/cc-control/api/user/mid-platform/session/history",
                json={**body, "limit": 120, "offset": offset},
                headers=headers,
            ).json()
            data = payload.get("data") or {}
            chunk = data.get("data") or []
            records.extend(chunk)
            if str(data.get("hasMore")).lower() != "true" or not chunk:
                break
            offset = int(data.get("nextOffset") or offset + len(chunk))
    return records


def turns_with_time(records: List[dict]) -> List[dict]:
    """会话历史拆成「用户消息（含时间戳）→ 该轮最后一段助手正文」。"""
    turns: List[dict] = []
    current: Optional[dict] = None

    def close() -> None:
        nonlocal current
        if current:
            current["answer"] = current["parts"][-1] if current["parts"] else ""
            turns.append(current)
            current = None

    for record in reversed(records):  # 接口按新→旧返回
        for item in record.get("output") or []:
            if item.get("role") == "user":
                close()
                text = "".join(
                    str(part.get("text") or "") for part in item.get("content") or [] if part.get("type") == "input_text"
                )
                try:
                    stamp = int(item.get("timestamp") or 0)
                except (TypeError, ValueError):
                    stamp = 0
                current = {"question": text.strip(), "ts": stamp, "parts": []}
            elif item.get("role") == "assistant" and current is not None:
                for part in item.get("content") or []:
                    if part.get("type") == "output_text" and str(part.get("text") or "").strip():
                        current["parts"].append(str(part["text"]).strip())
    close()
    turns.sort(key=lambda turn: turn["ts"])
    return turns


def score(answer: str) -> float:
    value = 0.0
    if "同一问题已解答" in answer:
        value -= 5
    if "**结论" in answer or "结论：" in answer:
        value += 3
    if "---" in answer:
        value += 0.5
    return value + min(len(answer), 6000) / 10000


def display_width(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(ch) in {"W", "F"} else 1 for ch in text)


def estimate_height(text: str, column_width: float) -> float:
    capacity = max(40, int(column_width * 1.05))
    lines = 0
    for paragraph in str(text).split("\n"):
        width = display_width(paragraph)
        lines += max(1, math.ceil(width / capacity)) if width else 1
    return min(MAX_ROW_HEIGHT, max(19.5, lines * 13.2 + 6))


def load_questions(path: Path) -> List[str]:
    raw = path.read_text(encoding="utf-8").splitlines()
    return [line.strip() for line in raw if line.strip()]


def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def create_task(platform: Dict[str, Any]) -> str:
    """新建一个任务，返回 taskId（用于绕开旧任务里损坏的技能包副本）。"""
    body = {
        "taskName": "[个人空间]" + datetime.now().strftime("%Y-%m-%d %H:%M"),
        "templateId": platform.get("template_id"),
    }
    with _client(platform) as client:
        payload = client.post(PLATFORM_BASE + "/cc-control/api/user/mid-platform/task/create", json=body, headers=_platform_headers(platform)).json()
    data = payload.get("data") or {}
    task_id = str(data.get("taskId") or "")
    if not data.get("success") or not task_id:
        raise SystemExit(f"新建任务失败：{json.dumps(payload, ensure_ascii=False)[:300]}")
    print(f"已新建任务 {task_id}")
    return task_id


def ask_all(questions: Sequence[str], settings: Dict[str, Any], answers_dir: Path, delay: float, wait_timeout: float) -> Tuple[Dict[int, str], bool]:
    platform = settings.get("platform") or {}
    client = client_from_settings(settings)
    ctx = build_run_context(datetime.now())
    answers: Dict[int, str] = {}
    ok = True
    for index, question in enumerate(questions, 1):
        target = answers_dir / f"q{index:02d}.json"
        saved = _read_json(target)
        if (
            saved
            and saved.get("completion_status") == "completed"
            and saved.get("session_id") == platform.get("session_id")
            and saved.get("question") == question
            and str(saved.get("text") or "").strip()
        ):
            print(f"[{index}/{len(questions)}] 已有回答，跳过：{question}")
            answers[index] = str(saved["text"])
            continue
        if not wait_until_idle(platform, wait_timeout):
            print("平台会话一直没空闲，停止提问。")
            return answers, False
        print(f"[{index}/{len(questions)}] 提问：{question}", flush=True)
        started = time.perf_counter()
        answer = client.ask(
            CaseContract(case_id=f"Q{index:02d}", question=question, result_type="detail", numeric_evaluable=False),
            ctx,
        )
        elapsed = round(time.perf_counter() - started, 1)
        answers[index] = answer.text or ""
        record = {
            "index": index,
            "question": question,
            "text": answer.text or "",
            "completion_status": answer.completion_status,
            "error": answer.error,
            "latency_ms": answer.latency_ms,
            "captured_at": datetime.now().isoformat(timespec="seconds"),
            "session_id": platform.get("session_id"),
            "task_id": platform.get("task_id"),
        }
        answers_dir.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, target)
        if answer.completion_status != "completed" or not (answer.text or "").strip():
            print(f"  ✗ 未完成：{answer.completion_status} / {answer.error}")
            ok = False
            break
        print(f"  ✓ {elapsed}s，{len(answer.text)} 字", flush=True)
        if delay > 0 and index < len(questions):
            time.sleep(delay)
    return answers, ok


def sync_from_history(questions: Sequence[str], platform: Dict[str, Any], since_ms: int) -> Dict[int, dict]:
    wait_until_idle(platform, 180)
    turns = turns_with_time(fetch_history(platform))
    fresh = [turn for turn in turns if turn["ts"] >= since_ms and turn["answer"]]
    print(f"本轮开始后共 {len(fresh)} 轮回答，开始按题对齐…")
    resolved: Dict[int, dict] = {}
    for index, question in enumerate(questions, 1):
        candidates = [(turn["ts"], turn["answer"]) for turn in fresh if _norm(turn["question"]) == _norm(question)]
        if not candidates:
            print(f"  {index:02d} 历史中没有：{question}")
            continue
        stamp, answer = max(candidates, key=lambda item: score(item[1]))
        resolved[index] = {"text": answer, "ts": stamp, "candidates": len(candidates)}
        print(f"  {index:02d} {len(answer)} 字（候选 {len(candidates)} 轮）")
    return resolved


def write_table(questions: Sequence[str], answers: Dict[int, str], out_path: Path) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "问题与结果"
    header_fill = PatternFill("solid", fgColor="FFE5F6FF")
    thin = Side(style="thin", color="FFB7C7D9")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)

    sheet["A1"] = "问题"
    sheet["B1"] = "结果"
    for cell in sheet[1]:
        cell.font = Font(name="Microsoft YaHei", size=11, bold=True)
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = border
    sheet.row_dimensions[1].height = 24

    widths = {"A": 46, "B": 110}
    for column, width in widths.items():
        sheet.column_dimensions[column].width = width

    for offset, question in enumerate(questions, 1):
        row = offset + 1
        answer = answers.get(offset, "")
        if len(answer) > MAX_CELL_CHARS:
            answer = answer[:MAX_CELL_CHARS] + "\n…（超长截断）"
        sheet.cell(row=row, column=1, value=question)
        sheet.cell(row=row, column=2, value=answer)
        for column in (1, 2):
            cell = sheet.cell(row=row, column=column)
            cell.font = Font(name="Microsoft YaHei", size=10)
            cell.alignment = Alignment(wrap_text=True, vertical="top")
            cell.border = border
        needed = max(
            estimate_height(question, widths["A"]),
            estimate_height(answer, widths["B"]),
        )
        sheet.row_dimensions[row].height = needed

    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_name(out_path.stem + ".writing" + out_path.suffix)
    workbook.save(tmp)
    os.replace(tmp, out_path)


def main(argv: Optional[Sequence[str]] = None) -> int:
    _utf8_stdout()
    parser = argparse.ArgumentParser(description="逐题提问并导出「问题 | 结果」两列 Excel")
    parser.add_argument("--questions", required=True, help="问题清单（每行一题）")
    parser.add_argument("--out", required=True, help="输出 xlsx 路径")
    parser.add_argument("--answers", default=str(TOOL_ROOT / "temp" / "answers_table"), help="回答落盘目录")
    parser.add_argument("--delay", type=float, default=2.0, help="两题之间的间隔秒数")
    parser.add_argument("--wait-timeout", type=float, default=900.0, help="每问前等待平台空闲的最长秒数")
    parser.add_argument("--no-ask", action="store_true", help="不提问，直接从本轮历史重建（需配合 --since）")
    parser.add_argument("--since", default="", help="只认这个时间之后的回答，ISO 格式，默认当前时间")
    parser.add_argument("--session-id", default="", help="覆盖配置里的 sessionId（换新会话时用，任务不变）")
    parser.add_argument("--create-task", action="store_true", help="先新建一个任务再提问（旧任务工作区损坏时用）")
    args = parser.parse_args(argv)

    questions_path = Path(args.questions).resolve()
    out_path = Path(args.out).resolve()
    if not questions_path.exists():
        raise SystemExit(f"找不到问题清单：{questions_path}")
    questions = load_questions(questions_path)
    if not questions:
        raise SystemExit("问题清单为空")
    print(f"共 {len(questions)} 题，输出 {out_path}")

    settings = load_settings()
    platform = dict(settings.get("platform") or {})
    if not platform.get("enabled") or not platform.get("url"):
        raise SystemExit("platform 未启用或 platform.curl 未配置，无法提问。")
    if args.create_task:
        platform["task_id"] = create_task(platform)
        platform["session_id"] = args.session_id.strip() or str(uuid.uuid4())
    elif args.session_id:
        platform["session_id"] = args.session_id.strip()
    settings["platform"] = platform
    print(f"平台会话 {platform.get('session_id')} / 任务 {platform.get('task_id')}")

    since = datetime.fromisoformat(args.since).timestamp() * 1000 if args.since else time.time() * 1000
    since_ms = int(since) - 5000  # 允许几秒时钟偏差

    answers: Dict[int, str] = {}
    if not args.no_ask:
        answers, ok = ask_all(questions, settings, Path(args.answers), args.delay, args.wait_timeout)
        if not ok:
            print("提问中断，尝试按会话历史校准已完成的题…")

    resolved = sync_from_history(questions, platform, since_ms)
    merged: Dict[int, str] = {}
    for index in range(1, len(questions) + 1):
        if index in resolved:
            merged[index] = resolved[index]["text"]
        elif index in answers and answers[index].strip():
            merged[index] = answers[index]
            print(f"  {index:02d} 历史未命中，使用 SSE 抓到的回答（{len(answers[index])} 字）")
    missing = [index for index in range(1, len(questions) + 1) if index not in merged]
    if missing:
        print("以下题目没有拿到回答：" + "，".join(f"{i:02d} {questions[i - 1]}" for i in missing))
        return 3

    write_table(questions, merged, out_path)
    print(f"已生成两列表格：{out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
