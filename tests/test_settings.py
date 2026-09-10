# -*- coding: utf-8 -*-
import evaluator.settings as settings_mod


def test_db_password_falls_back_to_persistent_user_environment(monkeypatch):
    monkeypatch.delenv("EVAL_DB_PASSWORD", raising=False)
    monkeypatch.setattr(settings_mod, "_persistent_user_env", lambda name: "registry-secret")

    password = settings_mod.db_password(
        {"database": {"password_env": "EVAL_DB_PASSWORD"}}
    )

    assert password == "registry-secret"
