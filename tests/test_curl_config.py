import json

import httpx
import pytest

from evaluator.contract_loader import load_contracts
from evaluator.curl_config import STREAM_URL, parse_platform_curl
from evaluator.run_context import build_run_context
from evaluator.settings import load_settings, platform_login_session
from evaluator.agent_client import client_from_settings


def command(cookie="test-cookie", url=STREAM_URL, site="https://assistant.sz-water.com.cn", site_headers=True):
    body = json.dumps(dict(sessionId="chat-2", taskId="task-2", templateId="tpl-2", taskName="leakage-skill", modelCode="deepseek-v4-flash", message="原问题"), ensure_ascii=False)
    headers = f"-H 'content-type: application/json' \\\n-H 'x-org-id: org-2' \\\n"
    if site_headers:
        headers += f"-H 'Origin: {site}' \\\n-H 'Referer: {site}/szleakage-workspace/' \\\n"
    return f"curl '{url}' \\\n{headers}-b 'SESSION_ID={cookie}' \\\n--data-raw '{body}'"


def test_extract_browser_curl_without_executing_message():
    fields = parse_platform_curl(command())
    assert fields == dict(session_id="chat-2", task_id="task-2", template_id="tpl-2", task_name="leakage-skill", org_id="org-2", _login_session="test-cookie",
                          url=STREAM_URL, origin="https://assistant.sz-water.com.cn", referer="https://assistant.sz-water.com.cn/szleakage-workspace/",
                          payload=dict(sessionId="chat-2", taskId="task-2", templateId="tpl-2", taskName="leakage-skill", modelCode="deepseek-v4-flash"))
    assert "原问题" not in str(fields)


def test_session_body_fields_survive_to_request(tmp_path):
    """平台要求 modelCode 等会话语境字段，必须原样带进真实请求。"""
    path = tmp_path / "settings.toml"
    path.write_text("[platform]\nenabled=true\ncurl='''\n" + command() + "\n'''\n", encoding="utf-8")
    settings = load_settings(path)
    client = client_from_settings(settings)
    sent = []

    def handle(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=b"")

    client.transport = httpx.MockTransport(handle)
    client.ask(load_contracts()[0], build_run_context("2026-09-07"))
    client.close()
    assert sent[0]["modelCode"] == "deepseek-v4-flash"
    assert sent[0]["message"] == load_contracts()[0].question


def test_address_and_same_origin_come_from_curl_not_code():
    fields = parse_platform_curl(command(site_headers=False))
    assert fields["url"] == STREAM_URL
    assert fields["origin"] == "https://assistant.sz-water.com.cn"
    assert fields["referer"] == "https://assistant.sz-water.com.cn/"


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
        parse_platform_curl(command(url="https://aiemployee.sz-water.com.cn/cc-control/api/user/mid-platform/session/message/stream"))
    with pytest.raises(ValueError):
        parse_platform_curl(command().replace('"sessionId"', 'broken'))


def test_old_credentials_and_address_fields_are_rejected(tmp_path):
    path = tmp_path / "settings.toml"
    path.write_text('[platform]\nsession_id="old"', encoding="utf-8")
    with pytest.raises(ValueError, match="请移除 platform"):
        load_settings(path)


@pytest.mark.parametrize("field", ["url", "origin", "referer"])
def test_address_fields_only_come_from_curl(tmp_path, field):
    path = tmp_path / "settings.toml"
    path.write_text(f'[platform]\n{field}="pasted-by-hand"\ncurl=\'\'\'\n{command()}\n\'\'\'\n', encoding="utf-8")
    with pytest.raises(ValueError, match="请移除 platform"):
        load_settings(path)
