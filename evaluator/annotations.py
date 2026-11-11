# -*- coding: utf-8 -*-
"""人工平反标注：append-only CSV，不新建数据库表。

一行 = 一次平反说明。同一题反复平反会有多行，读取方按 created_at 取最新一条。
文件默认位于 <项目根>/annotations/annotations.csv，可直接用 Excel 打开。
"""
from __future__ import annotations

import argparse
import csv
import getpass
import subprocess
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from evaluator.models import REQUIRED_DIMENSIONS
from evaluator.paths import TOOL_ROOT

ANNOTATION_DIR = TOOL_ROOT / "annotations"
ANNOTATION_FILE = ANNOTATION_DIR / "annotations.csv"

COLUMNS: Tuple[str, ...] = (
    "run_id",
    "case_id",
    "auto_verdict",
    "manual_verdict",
    "reason_code",
    "dimensions",
    "note",
    "evidence",
    "annotator",
    "prompt_version",
    "model",
    "code_version",
    "created_at",
)

# 归因枚举：决定“先改哪里”，不是给人看的自由文本。
REASON_CODES: Dict[str, str] = {
    "EXTRACT_MISS": "抽取没抓到",
    "THRESHOLD_STRICT": "判定太严",
    "LLM_MISJUDGE": "LLM 判错",
    "CONTRACT_WRONG": "契约或基准错",
    "SCOPE_MISS": "范围理解错",
    "OTHER": "其他",
}

DIMENSION_SEPARATOR = ";"

_LOCK = threading.Lock()


def annotation_path(path: Optional[str | Path] = None) -> Path:
    return Path(path) if path else ANNOTATION_FILE


def _now() -> str:
    return datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds")


def code_version() -> str:
    """当前代码版本，写进标注，方便规则升级后区分新旧标注。"""
    try:
        result = subprocess.run(
            ["git", "-C", str(TOOL_ROOT), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def system_user() -> str:
    try:
        return getpass.getuser()
    except Exception:  # pragma: no cover - 极端环境下取不到用户名
        return ""


def normalize_dimensions(values: Optional[Iterable[str]]) -> List[str]:
    """只保留七维内的取值，去重并保持固定顺序。"""
    wanted = {str(value).strip() for value in (values or []) if str(value or "").strip()}
    return [key for key in REQUIRED_DIMENSIONS if key in wanted]


def validate_reason(value: Optional[str]) -> str:
    code = str(value or "").strip().upper()
    if code not in REASON_CODES:
        raise ValueError("归因必须是：" + "、".join(REASON_CODES))
    return code


def _row_for_csv(row: Dict[str, Any]) -> Dict[str, str]:
    dimensions = row.get("dimensions") or []
    if isinstance(dimensions, str):
        dimensions = [item for item in dimensions.split(DIMENSION_SEPARATOR) if item]
    return {
        "run_id": str(row.get("run_id") or ""),
        "case_id": str(row.get("case_id") or ""),
        "auto_verdict": str(row.get("auto_verdict") or ""),
        "manual_verdict": str(row.get("manual_verdict") or ""),
        "reason_code": str(row.get("reason_code") or ""),
        "dimensions": DIMENSION_SEPARATOR.join(normalize_dimensions(dimensions)),
        "note": str(row.get("note") or ""),
        "evidence": str(row.get("evidence") or ""),
        "annotator": str(row.get("annotator") or ""),
        "prompt_version": str(row.get("prompt_version") or ""),
        "model": str(row.get("model") or ""),
        "code_version": str(row.get("code_version") or ""),
        "created_at": str(row.get("created_at") or _now()),
    }


def append_annotation(
    *,
    run_id: str,
    case_id: str,
    auto_verdict: str,
    manual_verdict: str,
    reason_code: str,
    dimensions: Optional[Iterable[str]] = None,
    note: str = "",
    evidence: str = "",
    annotator: Optional[str] = None,
    prompt_version: str = "",
    model: str = "",
    version: Optional[str] = None,
    path: Optional[str | Path] = None,
) -> Dict[str, Any]:
    """追加一条标注。返回写入的行（含解析后的维度列表）。"""
    code = validate_reason(reason_code)
    row = _row_for_csv(
        {
            "run_id": run_id,
            "case_id": case_id,
            "auto_verdict": auto_verdict,
            "manual_verdict": manual_verdict,
            "reason_code": code,
            "dimensions": normalize_dimensions(dimensions),
            "note": note,
            "evidence": evidence,
            "annotator": annotator if annotator is not None else system_user(),
            "prompt_version": prompt_version,
            "model": model,
            "code_version": version if version is not None else code_version(),
            "created_at": _now(),
        }
    )
    target = annotation_path(path)
    with _LOCK:
        target.parent.mkdir(parents=True, exist_ok=True)
        new_file = not target.exists() or target.stat().st_size == 0
        with target.open("a", newline="", encoding="utf-8-sig" if new_file else "utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=COLUMNS, lineterminator="\r\n")
            if new_file:
                writer.writeheader()
            writer.writerow(row)
    return _from_csv_row(row)


def _from_csv_row(raw: Dict[str, str]) -> Dict[str, Any]:
    row = {key: (raw.get(key) or "") for key in COLUMNS}
    row["dimensions"] = [item for item in (row.get("dimensions") or "").split(DIMENSION_SEPARATOR) if item]
    row["reason_label"] = REASON_CODES.get(row.get("reason_code") or "", row.get("reason_code") or "")
    return row


def load_annotations(path: Optional[str | Path] = None) -> List[Dict[str, Any]]:
    target = annotation_path(path)
    if not target.exists():
        return []
    with target.open("r", newline="", encoding="utf-8-sig") as handle:
        return [_from_csv_row(raw) for raw in csv.DictReader(handle)]


def latest_by_case(rows: Sequence[Dict[str, Any]]) -> Dict[Tuple[str, str], Dict[str, Any]]:
    """同一题取 created_at 最新的一条；时间相同保留后写入的。"""
    latest: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for row in rows:
        key = (row.get("run_id") or "", row.get("case_id") or "")
        current = latest.get(key)
        if current is None or str(row.get("created_at") or "") >= str(current.get("created_at") or ""):
            latest[key] = row
    return latest


def for_case(rows: Sequence[Dict[str, Any]], run_id: str, case_id: str) -> List[Dict[str, Any]]:
    """该题的全部标注，按时间从新到旧。"""
    matched = [row for row in rows if row.get("run_id") == run_id and row.get("case_id") == case_id]
    matched.sort(key=lambda row: str(row.get("created_at") or ""), reverse=True)
    return matched


def summarize(rows: Sequence[Dict[str, Any]], run_id: Optional[str] = None) -> Dict[str, Any]:
    counts: Dict[str, int] = {code: 0 for code in REASON_CODES}
    scoped = [row for row in rows if not run_id or row.get("run_id") == run_id]
    for row in scoped:
        code = row.get("reason_code") or ""
        counts[code] = counts.get(code, 0) + 1
    return {
        "total": len(scoped),
        "by_reason": counts,
        "labels": REASON_CODES,
        "cases": len({(row.get("run_id"), row.get("case_id")) for row in scoped}),
    }


def write_snapshot(out_path: str | Path, path: Optional[str | Path] = None) -> int:
    """把每题最新一条导出去重快照，返回题数。"""
    rows = latest_by_case(load_annotations(path))
    ordered = [rows[key] for key in sorted(rows)]
    target = Path(out_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS, lineterminator="\r\n")
        writer.writeheader()
        for row in ordered:
            writer.writerow(_row_for_csv(row))
    return len(ordered)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="人工平反标注（CSV）")
    parser.add_argument("--path", default=None, help="标注文件路径，默认 annotations/annotations.csv")
    sub = parser.add_subparsers(dest="command", required=True)

    snapshot = sub.add_parser("snapshot", help="导出每题最新的去重快照")
    snapshot.add_argument("--out", required=True, help="输出 CSV 路径")

    summary = sub.add_parser("summary", help="按归因聚合计数")
    summary.add_argument("--run-id", default=None)

    args = parser.parse_args(argv)
    if args.command == "snapshot":
        count = write_snapshot(args.out, args.path)
        print(f"已导出 {count} 题快照: {args.out}")
        return 0
    data = summarize(load_annotations(args.path), args.run_id)
    print(f"标注 {data['total']} 条，覆盖 {data['cases']} 题")
    for code, label in REASON_CODES.items():
        print(f"  {label:<8} {data['by_reason'].get(code, 0)}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
