# -*- coding: utf-8 -*-
from pathlib import Path

import evaluator.settings as settings_mod
from evaluator.contract_loader import resolve_contracts_path
from evaluator.paths import configure_paths, runtime_dir


def _write_settings(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "settings.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_db_password_reads_only_settings_file(monkeypatch):
    monkeypatch.setenv("EVAL_DB_PASSWORD", "environment-secret")

    password = settings_mod.db_password(
        {"database": {"password": "settings-secret", "password_env": "EVAL_DB_PASSWORD"}}
    )

    assert password == "settings-secret"
    assert settings_mod.db_password({"database": {"password_env": "EVAL_DB_PASSWORD"}}) == ""


def test_agent_token_reads_settings_not_environment(monkeypatch):
    monkeypatch.setenv("EVAL_AGENT_TOKEN", "environment-secret")
    monkeypatch.setenv("MY_AGENT_TOKEN", "environment-secret")
    assert settings_mod.agent_token({"agent": {"token": "settings-secret"}}) == "settings-secret"
    assert settings_mod.agent_token({"agent": {"token_env": "MY_AGENT_TOKEN"}}) == ""


def test_evaluator_llm_api_key_reads_settings_only(monkeypatch):
    monkeypatch.setenv("EVAL_LLM_API_KEY", "environment-secret")
    settings = {"evaluator_llm": {"api_key": "settings-secret"}}
    assert settings_mod.evaluator_llm_api_key(settings) == "settings-secret"
    assert settings_mod.evaluator_llm_api_key({}) == ""


def test_load_settings_uses_explicit_path(tmp_path, monkeypatch):
    monkeypatch.setenv("EVAL_SETTINGS", str(tmp_path / "missing.toml"))
    path = _write_settings(
        tmp_path,
        """
[app]
host = "127.0.0.1"
[paths]
runtime_dir = "runtime-from-file"
[agent]
token = "file-token"
[evaluator_llm]
api_key = "file-key"
""",
    )
    settings = settings_mod.load_settings(path)
    assert settings["app"]["host"] == "127.0.0.1"
    assert settings["agent"]["token"] == "file-token"
    assert settings["evaluator_llm"]["api_key"] == "file-key"


def test_contract_path_is_fixed_and_runtime_dir_follows_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("EVAL_RUNTIME_DIR", str(tmp_path / "env-runtime"))
    settings = {"app": {}, "paths": {"runtime_dir": str(tmp_path / "toml-runtime")}}
    assert resolve_contracts_path().name == "contracts.json"
    configure_paths(settings)
    assert runtime_dir() == tmp_path / "toml-runtime"
