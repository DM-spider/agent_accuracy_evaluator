"""Parse a browser's Bash cURL export as data, never execute it."""
import json
import re
import shlex
from http.cookies import SimpleCookie

STREAM_URL = "https://aiemployee.sz-water.com.cn/cc-control/api/user/mid-platform/session/message/stream"


def parse_platform_curl(command):
    try:
        tokens = shlex.split(command.replace("\\\r\n", "").replace("\\\n", ""), posix=True)
    except ValueError:
        raise ValueError("platform.curl 引号不完整，请粘贴完整的 Copy as cURL (bash) 内容") from None
    if not tokens or tokens.pop(0) not in {"curl", "curl.exe"}:
        raise ValueError("platform.curl 必须以 curl 开头")
    headers, cookies, url, body = {}, [], None, None
    values = {"-H", "--header", "-b", "--cookie", "-d", "--data", "--data-raw", "--data-binary", "--url", "-X", "--request", "-A", "--user-agent", "-e", "--referer"}
    index = 0
    while index < len(tokens):
        token = tokens[index]
        index += 1
        if token in {"--compressed", "--location", "-L", "--silent", "-s"}:
            continue
        if token in values:
            if index >= len(tokens):
                raise ValueError("platform.curl 请求参数不完整")
            value = tokens[index]
            index += 1
            if token in {"-H", "--header"}:
                key, sep, header_value = value.partition(":")
                if not sep:
                    raise ValueError("platform.curl 请求头格式不正确")
                key, header_value = key.strip().lower(), header_value.strip()
                if key in headers and headers[key] != header_value:
                    raise ValueError("platform.curl 包含冲突的请求头")
                headers[key] = header_value
            elif token in {"-b", "--cookie"}:
                cookies.append(value)
            elif token in {"-d", "--data", "--data-raw", "--data-binary"}:
                if body is not None:
                    raise ValueError("platform.curl 只能包含一个 JSON 请求体")
                body = value
            elif token == "--url":
                if url is not None:
                    raise ValueError("platform.curl 只能包含一个网址")
                url = value
            elif token in {"-X", "--request"} and value.upper() != "POST":
                raise ValueError("platform.curl 必须是发送消息的 POST 请求")
        elif token.startswith(("https://", "[https://")) and url is None:
            url = token
        else:
            raise ValueError("platform.curl 含不支持的参数，请使用浏览器 Copy as cURL (bash)，不要粘贴脚本")
    link = re.fullmatch(r"\[(https://[^\]]+)\]\(\1\)", url or "")
    if link:
        url = link.group(1)
    if url != STREAM_URL:
        raise ValueError("platform.curl 必须使用 message/stream 消息接口")
    try:
        payload = json.loads(body or "")
    except (TypeError, ValueError):
        raise ValueError("platform.curl 请求体必须是有效 JSON，不能引用文件") from None
    if not isinstance(payload, dict):
        raise ValueError("platform.curl 请求体必须是 JSON 对象")
    result = {}
    for key, source in {"session_id": "sessionId", "task_id": "taskId", "template_id": "templateId", "task_name": "taskName"}.items():
        value = payload.get(source)
        if not isinstance(value, str) or not value.strip():
            raise ValueError("platform.curl 请求体缺少 " + source)
        result[key] = value.strip()
    result["org_id"] = headers.get("x-org-id", "")
    if not result["org_id"]:
        raise ValueError("platform.curl 缺少 x-org-id 请求头")
    cookie_values = []
    for raw in cookies + ([headers["cookie"]] if "cookie" in headers else []):
        try:
            jar = SimpleCookie()
            jar.load(raw)
            if "SESSION_ID" in jar:
                cookie_values.append(jar["SESSION_ID"].value)
        except Exception:
            raise ValueError("platform.curl Cookie 格式不正确") from None
    if headers.get("x-session-id"):
        cookie_values.append(headers["x-session-id"])
    if not cookie_values or len(set(cookie_values)) != 1:
        raise ValueError("platform.curl 登录凭证缺失，或 Cookie 与 x-session-id 不一致")
    if not cookie_values[0] or any(c in cookie_values[0] for c in "\r\n;"):
        raise ValueError("platform.curl 登录凭证格式不正确")
    result["_login_session"] = cookie_values[0]
    return result
