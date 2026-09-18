# -*- coding: utf-8 -*-
"""配置加载。生产配置与凭证只从 settings.toml 读取，不接受环境变量覆盖。"""
from __future__ import annotations

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
    """先读示例配置作为默认值，再用显式路径或本机 settings.toml 覆盖。

    只接受显式文件路径依赖注入；不支持环境变量覆盖生产配置。
    """
    example = CONFIG_DIR / "settings.example.toml"
    settings: Dict[str, Any] = {}
    if example.exists():
        settings = tomllib.loads(example.read_text(encoding="utf-8"))
    candidates = []
    if path:
        candidates.append(Path(path))
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
    return settings


def db_password(settings: Dict[str, Any]) -> str:
    return settings.get("database", {}).get("password", "")


def agent_token(settings: Dict[str, Any]) -> str:
    """业务智能体 token 只读取 settings.agent.token。"""
    return settings.get("agent", {}).get("token", "")


def evaluator_llm_settings(settings: Dict[str, Any]) -> Dict[str, Any]:
    return settings.get("evaluator_llm") or {}


def evaluator_llm_api_key(settings: Dict[str, Any]) -> str:
    """评估 LLM 密钥只读取 settings.evaluator_llm.api_key。"""
    return evaluator_llm_settings(settings).get("api_key", "")


def platform_login_session(settings: Dict[str, Any]) -> str:
    config = settings.get("platform") or {}
    return config.get("_login_session", "")
