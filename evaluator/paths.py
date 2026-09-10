# -*- coding: utf-8 -*-
from __future__ import annotations

import os
from pathlib import Path

TOOL_ROOT = Path(__file__).resolve().parent.parent
EVALUATOR_DIR = TOOL_ROOT / "evaluator"
CONFIG_DIR = TOOL_ROOT / "config"
STATIC_DIR = EVALUATOR_DIR / "static"
GOLDEN_DIR = TOOL_ROOT / "data" / "golden"


def runtime_dir() -> Path:
    return Path(os.environ.get("EVAL_RUNTIME_DIR") or (TOOL_ROOT / "runtime"))


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
