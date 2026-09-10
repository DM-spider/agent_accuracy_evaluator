# -*- coding: utf-8 -*-
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict

from evaluator.paths import CONFIG_DIR, TOOL_ROOT

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib  # type: ignore


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def load_settings(path: Path | None = None) -> Dict[str, Any]:
    example = CONFIG_DIR / "settings.example.toml"
    settings: Dict[str, Any] = {}
    if example.exists():
        settings = tomllib.loads(example.read_text(encoding="utf-8"))
    candidates = []
    if path:
        candidates.append(Path(path))
    env_path = os.environ.get("EVAL_SETTINGS")
    if env_path:
        candidates.append(Path(env_path))
    candidates.extend(
        [
            TOOL_ROOT / "config" / "settings.toml",
            Path.cwd() / "settings.toml",
        ]
    )
    for candidate in candidates:
        if candidate.exists():
            settings = _deep_merge(settings, tomllib.loads(candidate.read_text(encoding="utf-8")))
            break
    platform = settings.get("platform") or {}
    legacy = {"session_id", "task_id", "template_id", "org_id", "task_name", "login_session_env", "_login_session"}
    if legacy.intersection(platform):
        raise ValueError("请移除 platform 下的旧会话和凭证字段，仅在 platform.curl 中粘贴完整 Bash cURL")
    curl = platform.pop("curl", "")
    if not isinstance(curl, str):
        raise ValueError("platform.curl 必须是三单引号包裹的多行文本")
    if curl.strip():
        from evaluator.curl_config import parse_platform_curl
        platform.update(parse_platform_curl(curl))
    env_file = (settings.get("database") or {}).get("env_file")
    if env_file:
        from dotenv import dotenv_values
        source = Path(env_file)
        if not source.is_absolute():
            source = TOOL_ROOT / source
        values = dotenv_values(source, encoding="utf-8-sig", interpolate=False)
        db = settings.setdefault("database", {})
        for key, env_key in {"host": "PGHOST", "port": "PGPORT", "name": "PGDATABASE", "user": "PGUSER", "password": "PGPASSWORD"}.items():
            if values.get(env_key):
                db[key] = values[env_key]
    return settings


def db_password(settings: Dict[str, Any]) -> str:
    return settings.get("database", {}).get("password", "")


def agent_token(settings: Dict[str, Any]) -> str:
    env_name = settings.get("agent", {}).get("token_env", "EVAL_AGENT_TOKEN")
    return os.environ.get(env_name, "")


def platform_login_session(settings: Dict[str, Any]) -> str:
    config = settings.get("platform") or {}
    return config.get("_login_session", "")
