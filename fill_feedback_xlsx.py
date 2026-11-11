# -*- coding: utf-8 -*-
"""把「问数确认表」里的问题逐条问给当前配置的业务平台智能体，并把回答回填到指定列。

用法：
    python fill_feedback_xlsx.py                          # 默认 D 列：提问 → 校准 → 写回
    python fill_feedback_xlsx.py --column E               # 写入 E 列（回答存 temp/answers_E）
    python fill_feedback_xlsx.py ask --column E --rows 11,23
    python fill_feedback_xlsx.py sync --column E          # 只按平台会话历史校准已落盘回答
    python fill_feedback_xlsx.py write --column E         # 只把回答写进 Excel

约定：
- 复用 evaluator.agent_client.client_from_settings，和页面测评走完全同一套 platform.curl 会话配置；
- 一题一问、顺序执行；每次提问前先查平台会话是否空闲。平台会把同一会话的新消息排队，
  上一轮没跑完时新连接收到的可能是上一轮的回答，不先等空闲就会「这题填了上一题答案」；
- 提问后按平台 session/history 逐轮校准：以「这条消息 → 这轮最后一段回答」为准重写回答文件，
  避免 SSE 断流/排队造成的错位；
- 写回只动目标行的目标列与该行行高：先备份原文件，保存到临时文件后再原子替换，失败不留半成品；
- D33:D39 原本是合并格，按业务确认只在写 D 列时拆开，让 7 个问题各占一格。
"""
from __future__ import annotations

import argparse
import io
import json
import math
import os
import shutil
import sys
import time
import unicodedata
from copy import copy
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import httpx
from openpyxl import load_workbook
from openpyxl.utils import column_index_from_string, get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from evaluator.agent_client import client_from_settings
from evaluator.models import CaseContract
from evaluator.paths import TOOL_ROOT
from evaluator.run_context import build_run_context
from evaluator.settings import load_settings

SHEET_NAME = "问数确认表"
DEFAULT_COLUMN = "D"
QUESTION_COLUMN = 2  # B 列：问题
XLSX_PATH = TOOL_ROOT / "temp" / "漏损-反馈及指标说明表.xlsx"
ANSWER_ROOT = TOOL_ROOT / "temp"
BACKUP_DIR = TOOL_ROOT / "temp" / "backups"
PLATFORM_BASE = "https://assistant.sz-water.com.cn"

# (行号, 题干)。题干用于校验模板没被改动，避免行号漂移后把回答写到别的问题上。
TARGETS: List[Tuple[int, str]] = [
    (11, "宝安分公司今年 1-2 月产销差率与集团目标的差距是多少？"),
    (12, "对比各分公司今年产销差率完成率排名"),
    (13, "近三个月产销差率持续上升的分公司有哪些？"),
    (14, "本公司产销差率与其他公司对比情况如何？"),
    (15, "各单位累计产销差对全市产销差影响分析"),
    (23, "高漏耗小区有哪些？"),
    (24, "兴华花园漏损率趋势如何？"),
    (25, "夜间最小流量异常的 DMA 有哪些？"),
    (26, "本区域高漏损 DMA 片区整治后漏损率下降了多少？"),
    (27, "漏损率超过阈值的 DMA 片区有哪些？"),
    (28, "上一月漏损率大于20%的DMA小区有多少个？漏损率小于-5%的DMA小区有多少个？"),
    (29, "该季度持续高漏耗的 DMA 有哪些？"),
    (30, "本季度持续高漏耗的 DMA 片区主要集中在哪些区域？"),
    (31, "本单位连续 3 个月漏损率超标的 DMA 有哪些？"),
    (32, "持续高漏耗 DMA 的漏损原因分布是怎样的？"),
    (33, "上月漏损预警频次大于2次的小区有哪些？以及预警次数"),
    (34, "上月，漏损预警频次排前5的小区，以及预警次数。"),
    (35, "上月内管网维修漏损的水量"),
    (36, "上月内消防栓排放的水量"),
    (37, "上月DMA小区新增漏损点数量(今日、本周、本月)"),
    (38, "上月维修次最多的管道(或者阀门、消防栓)设施，取前5名，显示维修次数"),
    (39, "上月，管道(或者阀门、消防栓)维修大于XX次的设施有哪些？"),
]

MAX_CELL_CHARS = 32000  # Excel 单元格上限 32767
MAX_ROW_HEIGHT = 409.5  # Excel 行高上限，与已填写的答题行一致
MIN_READABLE_WIDTH = 40  # 目标列窄于这个值时，按同表最宽的答题列补宽，否则内容无法阅读


def _utf8_stdout() -> None:
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)


def _norm(text: str) -> str:
    return "".join(str(text or "").split())


def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def answer_dir_for(column: str) -> Path:
    return ANSWER_ROOT / ("answers" if column == "D" else f"answers_{column}")


def _answer_path(answer_dir: Path, row: int) -> Path:
    return answer_dir / f"row{row:02d}.json"


def check_questions(xlsx: Path) -> None:
    """确认模板里的题干与脚本内置清单一致，不一致就停下来，避免写错行。"""
    wb = load_workbook(xlsx, read_only=True)
    try:
        ws = wb[SHEET_NAME]
        problems = []
        for row, expected in TARGETS:
            actual = ws.cell(row=row, column=QUESTION_COLUMN).value
            if _norm(actual or "") != _norm(expected):
                problems.append(f"  第 {row} 行：模板「{actual}」≠ 脚本「{expected}」")
        if problems:
            raise SystemExit("模板题干与脚本清单不一致，已停止：\n" + "\n".join(problems))
    finally:
        wb.close()


# --------------------------------------------------------------------------- 平台接口


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
    """平台会话空闲才提问。

    平台会把同一会话的新消息排队；上一轮没跑完时，新连接收到的可能是上一轮的回答。
    不先等空闲，就会出现「这题填了上一题答案」的错位。
    """
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


def _fetch_history(platform: Dict[str, Any]) -> List[dict]:
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


def _turns(records: List[dict]) -> List[dict]:
    """把会话历史拆成「用户消息 → 该轮最后一段助手正文」。"""
    turns: List[dict] = []
    current: Optional[dict] = None
    for record in reversed(records):  # 接口按新→旧返回，这里按时间正序
        for item in record.get("output") or []:
            if item.get("role") == "user":
                if current:
                    turns.append(current)
                text = "".join(
                    str(part.get("text") or "") for part in item.get("content") or [] if part.get("type") == "input_text"
                )
                current = {"question": text.strip(), "parts": []}
            elif item.get("role") == "assistant" and current is not None:
                for part in item.get("content") or []:
                    if part.get("type") == "output_text" and str(part.get("text") or "").strip():
                        current["parts"].append(str(part["text"]).strip())
    if current:
        turns.append(current)
    for turn in turns:
        turn["answer"] = turn["parts"][-1] if turn["parts"] else ""
    return turns


def _score(answer: str) -> float:
    value = 0.0
    if "同一问题已解答" in answer:
        value -= 5
    if "**结论" in answer or "结论：" in answer:
        value += 3
    if "---" in answer:
        value += 0.5
    return value + min(len(answer), 6000) / 10000


def sync_answers(platform: Dict[str, Any], answer_dir: Path, rows: Sequence[int]) -> int:
    """按平台会话历史校准回答：以「消息 → 该轮最终回答」为准，覆盖 SSE 抓到的文本。"""
    wait_until_idle(platform, 180)
    turns = _turns(_fetch_history(platform))
    print(f"会话历史共 {len(turns)} 轮，开始按题对齐…")
    missing: List[int] = []
    for row, question in TARGETS:
        if row not in set(rows):
            continue
        candidates = [(index, turn["answer"]) for index, turn in enumerate(turns) if _norm(turn["question"]) == _norm(question) and turn["answer"]]
        if not candidates:
            print(f"  行{row}：历史中没有该题的回答")
            missing.append(row)
            continue
        index, answer = max(candidates, key=lambda item: _score(item[1]))
        record = {
            "row": row,
            "question": question,
            "text": answer,
            "completion_status": "completed",
            "error": None,
            "latency_ms": None,
            "captured_at": datetime.now().isoformat(timespec="seconds"),
            "source": f"session/history turn #{index}",
            "candidates": [{"turn": i, "chars": len(a)} for i, a in candidates],
            "platform_session_id": platform.get("session_id"),
            "platform_task_id": platform.get("task_id"),
        }
        _write_json(_answer_path(answer_dir, row), record)
        print(f"  行{row}：{len(answer)} 字（候选 {len(candidates)} 轮）")
    if missing:
        print(f"仍有 {len(missing)} 题没拿到回答：{missing}")
        return 2
    print(f"校准完成：{len([r for r in rows])} 题")
    return 0


# --------------------------------------------------------------------------- 提问


def run_ask(answer_dir: Path, rows: Sequence[int], force: bool, delay: float, wait_timeout: float, column: str) -> int:
    settings = load_settings()
    platform = settings.get("platform") or {}
    if not platform.get("enabled") or not platform.get("url"):
        raise SystemExit("platform 未启用或 platform.curl 未配置，无法提问。")
    print(f"平台会话 {platform.get('session_id')} / 任务 {platform.get('task_id')}")
    client = client_from_settings(settings)
    ctx = build_run_context(datetime.now())

    wanted = [(row, text) for row, text in TARGETS if row in set(rows)]
    total = len(wanted)
    done = 0
    for index, (row, question) in enumerate(wanted, 1):
        path = _answer_path(answer_dir, row)
        saved = _read_json(path)
        if saved and saved.get("completion_status") == "completed" and saved.get("text") and not force:
            print(f"[{index}/{total}] 行{row} 已有回答，跳过（{len(saved['text'])} 字）")
            done += 1
            continue
        if not wait_until_idle(platform, wait_timeout):
            print("平台会话一直没空闲，已停止；等它跑完后重跑本命令，会从这题继续。")
            return 4
        print(f"[{index}/{total}] 行{row} 提问：{question}", flush=True)
        started = time.perf_counter()
        answer = client.ask(
            CaseContract(case_id=f"ROW-{row}", question=question, result_type="detail", numeric_evaluable=False),
            ctx,
        )
        elapsed = round(time.perf_counter() - started, 1)
        record = {
            "row": row,
            "question": question,
            "text": answer.text or "",
            "completion_status": answer.completion_status,
            "error": answer.error,
            "latency_ms": answer.latency_ms,
            "captured_at": datetime.now().isoformat(timespec="seconds"),
            "column": column,
            "platform_session_id": platform.get("session_id"),
            "platform_task_id": platform.get("task_id"),
        }
        _write_json(path, record)
        if answer.completion_status != "completed" or not (answer.text or "").strip():
            print(f"  ✗ 未完成：{answer.completion_status} / {answer.error}（已保存 {path.name}，可重跑续问）")
            return 3
        done += 1
        print(f"  ✓ {elapsed}s，回答 {len(answer.text)} 字", flush=True)
        if delay > 0 and index < total:
            time.sleep(delay)
    print(f"\n提问完成：{done}/{total} 题，回答目录 {answer_dir}")
    return 0


# --------------------------------------------------------------------------- 写回


def _display_width(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(ch) in {"W", "F"} else 1 for ch in text)


def estimate_row_height(text: str, column_width: float) -> float:
    """按列宽估算自动换行后的行高，和已填写的答题行一样限制在 Excel 上限内。"""
    capacity = max(40, int(column_width * 1.05))
    lines = 0
    for paragraph in str(text).split("\n"):
        width = _display_width(paragraph)
        lines += max(1, math.ceil(width / capacity)) if width else 1
    return min(MAX_ROW_HEIGHT, max(19.5, lines * 13.2 + 6))


def _cell_text(text: str, row: int, answer_dir: Path) -> str:
    if len(text) <= MAX_CELL_CHARS:
        return text
    relative = answer_dir.relative_to(TOOL_ROOT) if answer_dir.is_relative_to(TOOL_ROOT) else answer_dir
    return text[:MAX_CELL_CHARS] + f"\n…（超长截断，完整回答见 {relative}/row{row:02d}.json）"


def _snapshot(ws: Worksheet, skip_cells: set) -> Dict[str, Any]:
    """记录除目标答题格以外的全部内容，写回后逐格比对，确保没动别处。"""
    values = {}
    for row in ws.iter_rows():
        for cell in row:
            if cell.coordinate in skip_cells:
                continue
            values[cell.coordinate] = cell.value
    return {
        "values": values,
        "merges": {str(item) for item in ws.merged_cells.ranges},
        "heights": {r: (dim.height, dim.customHeight) for r, dim in ws.row_dimensions.items()},
        "widths": {key: dim.width for key, dim in ws.column_dimensions.items()},
    }


def _run_write(xlsx: Path, answer_dir: Path, rows: Sequence[int], overwrite: bool, column: str) -> int:
    check_questions(xlsx)
    column_index = column_index_from_string(column)
    wb = load_workbook(xlsx)
    ws = wb[SHEET_NAME]

    wanted = [(row, text) for row, text in TARGETS if row in set(rows)]
    filled: List[int] = []
    skipped: List[str] = []
    texts: Dict[int, str] = {}
    for row, question in wanted:
        record = _read_json(_answer_path(answer_dir, row))
        if not record or record.get("completion_status") != "completed" or not (record.get("text") or "").strip():
            skipped.append(f"行{row}（无可用回答）")
            continue
        cell = ws.cell(row=row, column=column_index)
        if cell.value and not overwrite:
            skipped.append(f"行{row}（{column} 列已有内容）")
            continue
        texts[row] = _cell_text(str(record["text"]), row, answer_dir)
        filled.append(row)
    if not filled:
        print("没有可写入的回答。")
        return 3
    missing = [item for item in skipped if "无可用回答" in item]
    if missing:
        print("以下题目没有回答，先补问再写：" + "，".join(missing))
        return 3

    wanted_rows = set(filled)
    for merged in list(ws.merged_cells.ranges):
        if merged.min_col <= column_index <= merged.max_col and merged.max_row > merged.min_row:
            if any(merged.min_row <= row <= merged.max_row for row in wanted_rows):
                ws.unmerge_cells(str(merged))
                print(f"已拆开合并格 {merged}")

    # 新列通常还是默认窄宽，先按同表最宽的答题列补宽，否则整列内容不可读。
    width = ws.column_dimensions[column].width or 0
    if width < MIN_READABLE_WIDTH:
        reference = max([dim.width or 0 for key, dim in ws.column_dimensions.items() if key != column] or [0])
        reference = reference or 136.5
        print(f"{column} 列原宽 {width or '默认'} 过窄，已按 {reference:.0f} 调整")
        width = reference
        ws.column_dimensions[column].width = width

    skip_cells = {ws.cell(row=row, column=column_index).coordinate for row in filled}
    before = _snapshot(ws, skip_cells)

    for row in filled:
        cell = ws.cell(row=row, column=column_index)
        cell.value = texts[row]
        alignment = copy(cell.alignment)
        alignment.wrap_text = True
        alignment.vertical = "top"
        cell.alignment = alignment
        needed = estimate_row_height(texts[row], width)
        current = ws.row_dimensions[row].height or 0
        ws.row_dimensions[row].height = min(MAX_ROW_HEIGHT, max(current, needed))

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = BACKUP_DIR / f"{xlsx.stem}_{column}_{stamp}{xlsx.suffix}"
    shutil.copy2(xlsx, backup)
    print(f"已备份原文件：{backup}")

    tmp = xlsx.with_name(xlsx.stem + ".writing" + xlsx.suffix)
    try:
        wb.save(tmp)
        os.replace(tmp, xlsx)
    except PermissionError:
        tmp.unlink(missing_ok=True)
        raise SystemExit(f"写入失败：{xlsx.name} 正被 Excel 占用，请关闭后重试（原文件未改动）。")
    finally:
        tmp.unlink(missing_ok=True)

    # 重新打开校验：目标格与落盘回答一致，其余单元格内容不变。
    check = load_workbook(xlsx)
    cws = check[SHEET_NAME]
    errors = []
    for row in filled:
        if (cws.cell(row=row, column=column_index).value or "") != texts[row]:
            errors.append(f"{column}{row} 内容与回答不一致")
    after = _snapshot(cws, skip_cells)
    changed = [key for key in before["values"] if before["values"][key] != after["values"].get(key)]
    if changed:
        errors.append(f"非目标单元格被改动：{changed[:5]}")
    height_changed = {r for r in before["heights"] if before["heights"][r] != after["heights"].get(r)}
    if height_changed - set(filled):
        errors.append(f"非目标行高被改动：{sorted(height_changed - set(filled))}")
    width_changed = {key for key in before["widths"] if before["widths"].get(key) != after["widths"].get(key)}
    if width_changed - {column}:
        errors.append(f"非目标列宽被改动：{sorted(width_changed - {column})}")
    if errors:
        raise SystemExit("写回校验未通过：\n" + "\n".join(errors))

    print(f"已回填 {len(filled)} 题：{', '.join(column + str(r) for r in filled)}")
    if skipped:
        print("跳过：" + "，".join(skipped))
    print(f"校验通过：目标格正确，其余 {len(before['values'])} 个单元格内容未变。")
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    _utf8_stdout()
    parser = argparse.ArgumentParser(description="向当前配置的平台智能体提问，并回填「问数确认表」指定列")
    parser.add_argument("phase", nargs="?", default="all", choices=["ask", "sync", "write", "all"], help="ask=只提问，sync=按会话历史校准，write=只写回，all=提问+校准+写回")
    parser.add_argument("--column", default=DEFAULT_COLUMN, help="写入列，默认 D，如 E")
    parser.add_argument("--xlsx", default=str(XLSX_PATH), help="目标工作簿路径")
    parser.add_argument("--answers", default="", help="回答落盘目录，默认按列推导")
    parser.add_argument("--rows", default="", help="只处理指定行，逗号分隔，如 11,23")
    parser.add_argument("--force", action="store_true", help="忽略已落盘回答，重新提问")
    parser.add_argument("--overwrite", action="store_true", help="目标列已有内容时也覆盖")
    parser.add_argument("--delay", type=float, default=2.0, help="两题之间的间隔秒数")
    parser.add_argument("--wait-timeout", type=float, default=900.0, help="每问前等待平台空闲的最长秒数")
    args = parser.parse_args(argv)

    column = args.column.strip().upper()
    if not column.isalpha() or len(column) > 2:
        raise SystemExit(f"列名不合法：{args.column}")
    xlsx = Path(args.xlsx).resolve()
    answer_dir = Path(args.answers).resolve() if args.answers else answer_dir_for(column)
    if not xlsx.exists():
        raise SystemExit(f"找不到工作簿：{xlsx}")
    rows = [int(item) for item in args.rows.split(",") if item.strip()] or [row for row, _ in TARGETS]
    unknown = sorted(set(rows) - {row for row, _ in TARGETS})
    if unknown:
        raise SystemExit(f"不在清单中的行号：{unknown}")

    settings = load_settings()
    platform = settings.get("platform") or {}

    ask_failed = False
    if args.phase in {"ask", "all"}:
        code = run_ask(answer_dir, rows, args.force, args.delay, args.wait_timeout, column)
        if code != 0:
            ask_failed = True
            print("提问中断，先按平台会话历史校准已完成的回答…")
    if args.phase in {"sync", "all"} or ask_failed:
        code = sync_answers(platform, answer_dir, rows)
        if code != 0:
            return code
        if ask_failed:
            return 3
    if args.phase in {"write", "all"}:
        return _run_write(xlsx, answer_dir, rows, args.overwrite, column)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
