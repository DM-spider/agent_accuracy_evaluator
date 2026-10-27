# -*- coding: utf-8 -*-
"""CLI 对账产物的版本目录：按日期一天一个版本。

    cli_eval/out/
      report_20261027/
        results.json
        cli_eval_report_20261027.html
        golden_db/       当天的标准值快照
      report_20261026/
      raw/               接口响应缓存，跨版本共用，不属于任何版本

版本号是 8 位日期（20261027）。同一天重复运行会覆盖当天版本；
要写历史日期或重出旧报告，用 --date 指定（也接受 2026-10-27 这种写法）。
"""
from __future__ import annotations

import os
import re
from datetime import datetime
from pathlib import Path
from typing import List, Optional

OUT = Path(__file__).resolve().parent / "out"
DATE_ENV = "CLI_EVAL_VERSION"
VERSION_RE = re.compile(r"^\d{8}$")
DIR_PREFIX = "report_"
FOLDER_RE = re.compile(rf"^{DIR_PREFIX}(\d{{8}})$")
DASHED_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _normalize(value: str) -> Optional[str]:
    """把版本号收敛成 8 位日期；非法日期（20261340）也拒掉。"""
    text = str(value or "").strip()
    if DASHED_RE.match(text):
        text = text.replace("-", "")
    if not VERSION_RE.match(text):
        return None
    try:
        datetime.strptime(text, "%Y%m%d")
    except ValueError:
        return None
    return text


def today() -> str:
    return datetime.now().strftime("%Y%m%d")


def active_date() -> str:
    """当前版本日期：优先取 --date 写进环境变量的值，否则今天。"""
    return _normalize(os.environ.get(DATE_ENV, "")) or today()


def use_date(value: Optional[str]) -> str:
    """命令行 --date 生效点：校验格式并写入环境变量，供后续模块共用。"""
    if not value:
        return active_date()
    normalized = _normalize(value)
    if not normalized:
        raise ValueError(f"版本日期必须是 8 位日期（如 20261027）：{value}")
    os.environ[DATE_ENV] = normalized
    return normalized


def version_dir(date: Optional[str] = None) -> Path:
    return OUT / f"{DIR_PREFIX}{date or active_date()}"


def ensure_version_dir(date: Optional[str] = None) -> Path:
    path = version_dir(date)
    path.mkdir(parents=True, exist_ok=True)
    return path


def raw_dir() -> Path:
    """接口原始响应缓存，跨版本共用。"""
    path = OUT / "raw"
    path.mkdir(parents=True, exist_ok=True)
    return path


def list_versions() -> List[str]:
    if not OUT.exists():
        return []
    versions = []
    for path in OUT.iterdir():
        match = FOLDER_RE.match(path.name)
        if match and path.is_dir():
            versions.append(match.group(1))
    return sorted(versions)


def latest_version() -> Optional[str]:
    versions = list_versions()
    return versions[-1] if versions else None
