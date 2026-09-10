# -*- coding: utf-8 -*-
import evaluator.settings as settings_mod


def test_db_password_reads_only_settings_file(monkeypatch):
    monkeypatch.setenv("EVAL_DB_PASSWORD", "environment-secret")

    password = settings_mod.db_password(
        {"database": {"password": "settings-secret", "password_env": "EVAL_DB_PASSWORD"}}
    )

    assert password == "settings-secret"
    assert settings_mod.db_password({"database": {"password_env": "EVAL_DB_PASSWORD"}}) == ""
