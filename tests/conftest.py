# -*- coding: utf-8 -*-
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evaluator.paths import GOLDEN_DIR, TOOL_ROOT


@pytest.fixture(scope="session")
def tool_root() -> Path:
    return TOOL_ROOT


@pytest.fixture(scope="session")
def golden_dir() -> Path:
    return GOLDEN_DIR
