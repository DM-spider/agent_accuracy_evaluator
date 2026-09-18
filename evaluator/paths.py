# -*- coding: utf-8 -*-
"""工具目录解析。runtime_dir / golden_dir 只由传入的 settings 决定。"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

TOOL_ROOT = Path(__file__).resolve().parent.parent
EVALUATOR_DIR = TOOL_ROOT / "evaluator"
CONFIG_DIR = TOOL_ROOT / "config"
STATIC_DIR = EVALUATOR_DIR / "static"
GOLDEN_DIR = TOOL_ROOT / "data" / "golden"

_RUNTIME_DIR_OVERRIDE: Optional[Path] = None
_GOLDEN_DIR_OVERRIDE: Optional[Path] = None


def configure_paths(settings: Optional[Dict[str, Any]]) -> None:
    """bootstrap 时按 settings.paths 固定运行目录，不支持环境变量覆盖。"""
    global _RUNTIME_DIR_OVERRIDE, _GOLDEN_DIR_OVERRIDE
    paths = (settings or {}).get("paths") or {}
    runtime = str(paths.get("runtime_dir") or "").strip()
    golden = str(paths.get("golden_dir") or "").strip()
    _RUNTIME_DIR_OVERRIDE = Path(runtime) if runtime else None
    _GOLDEN_DIR_OVERRIDE = Path(golden) if golden else None


def runtime_dir() -> Path:
    return _RUNTIME_DIR_OVERRIDE or (TOOL_ROOT / "runtime")


def golden_dir() -> Path:
    return _GOLDEN_DIR_OVERRIDE or GOLDEN_DIR


def runs_dir() -> Path:
    return runtime_dir() / "runs"


def db_path() -> Path:
    return runtime_dir() / "evaluation.db"


RUNTIME_DIR = runtime_dir()
RUNS_DIR = runs_dir()
DB_PATH = db_path()


def ensure_runtime() -> Path:
    root = runtime_dir()
    root.mkdir(parents=True, exist_ok=True)
    runs_dir().mkdir(parents=True, exist_ok=True)
    return root
