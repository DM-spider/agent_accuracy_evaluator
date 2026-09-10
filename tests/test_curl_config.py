import json

import pytest

from evaluator.curl_config import STREAM_URL, parse_platform_curl
from evaluator.settings import load_settings, platform_login_session
from evaluator.agent_client import client_from_settings


def command(cookie="test-cookie", url=STREAM_URL):
    body = json.dumps(dict(sessionId="chat-2", taskId="task-2", templateId="tpl-2", taskName="leakage-skill", message="原问题"), ensure_ascii=False)
    return f"curl '{url}' \\\n-H 'content-type: application/json' \\\n-H 'x-org-id: org-2' \\\n-b 'SESSION_ID={cookie}' \\\n--data-raw '{body}'"


def test_extract_browser_curl_without_executing_message():
    fields = parse_platform_curl(command())
    assert fields == dict(session_id="chat-2", task_id="task-2", template_id="tpl-2", task_name="leakage-skill", org_id="org-2", _login_session="test-cookie")
    assert "原问题" not in str(fields)


def test_config_only_uses_curl_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv("EVAL_PLATFORM_LOGIN_SESSION", "stale-cookie")
    path = tmp_path / "settings.toml"
    path.write_text("[platform]\nenabled=true\ncurl='''\n" + command() + "\n'''\n", encoding="utf-8")
    settings = load_settings(path)
    assert "curl" not in settings["platform"]
    assert platform_login_session(settings) == "test-cookie"
    client = client_from_settings(settings)
    assert client.session_id == "chat-2" and client.login_session == "test-cookie"
    client.close()
    assert platform_login_session({"platform": {}}) == ""


@pytest.mark.parametrize("suffix", [" ; echo secret", " -H 'x-session-id: conflicting'", " --data-raw '{}'", " --insecure"])
def test_invalid_curl_fails_without_exposing_cookie(suffix):
    with pytest.raises(ValueError) as caught:
        parse_platform_curl(command(cookie="private-credential") + suffix)
    assert "private-credential" not in str(caught.value)


def test_wrong_endpoint_and_malformed_json_are_rejected():
    with pytest.raises(ValueError):
        parse_platform_curl(command(url="https://example.com"))
    with pytest.raises(ValueError):
        parse_platform_curl(command().replace('"sessionId"', 'broken'))


def test_old_fields_are_rejected(tmp_path):
    path = tmp_path / "settings.toml"
    path.write_text('[platform]\nsession_id="old"', encoding="utf-8")
    with pytest.raises(ValueError, match="旧会话"):
        load_settings(path)
