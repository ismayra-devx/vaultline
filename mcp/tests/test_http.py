"""Hosted (streamable HTTP) mode: per-caller credentials, auth gate, host checks, hosted config rules."""

from __future__ import annotations

import httpx
import pytest
from starlette.testclient import TestClient

from vaultline_mcp.backend_client import VaultlineBackend
from vaultline_mcp.config import ConfigError, Settings
from vaultline_mcp.server import create_http_app, create_server

HOST = "vaultline-mcp.example.com"
H = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json", "Host": HOST, "MCP-Protocol-Version": "2025-06-18"}
POLICY = {"version": "v1", "max_auto_amount": "10.00", "max_escalate_amount": "25.00", "currency": "USD"}


def rpc(i, method, params=None):
    return {"jsonrpc": "2.0", "id": i, "method": method, "params": params or {}}


INIT = rpc(1, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}})


@pytest.fixture
def hosted():
    seen: list[str | None] = []

    def backend(req: httpx.Request) -> httpx.Response:
        seen.append(req.headers.get("authorization"))
        if req.url.path.endswith("/policy"):
            return httpx.Response(200, json=POLICY)
        return httpx.Response(200, json={"request_id": "req_1", "decision": "BLOCK", "status": "BLOCKED", "execution_status": "NOT_STARTED"})

    s = Settings("https://backend.example.com", transport="http", hosted=True, allowed_hosts=(HOST,))
    app = create_http_app(create_server(VaultlineBackend(s, transport=httpx.MockTransport(backend)), s), s)
    with TestClient(app, base_url=f"https://{HOST}") as client:
        yield client, seen


def call(client, key, name, args=None, host=HOST):
    headers = H | {"Host": host} | ({"Authorization": f"Bearer {key}"} if key else {})
    return client.post("/mcp", json=rpc(2, "tools/call", {"name": name, "arguments": args or {}}), headers=headers)


def test_health_is_public(hosted):
    client, _ = hosted
    assert client.get("/health", headers={"Host": HOST}).json() == {"status": "ok"}


def test_no_bearer_is_rejected_before_mcp(hosted):
    client, seen = hosted
    r = client.post("/mcp", json=INIT, headers=H)
    assert r.status_code == 401 and r.headers["www-authenticate"] == "Bearer"
    assert seen == []


def test_non_bearer_scheme_rejected(hosted):
    client, seen = hosted
    assert client.post("/mcp", json=INIT, headers=H | {"Authorization": "Basic Zm9vOmJhcg=="}).status_code == 401
    assert seen == []


def test_each_callers_own_key_is_forwarded(hosted):
    client, seen = hosted
    assert call(client, "agent-key-A", "get_agent_policy").status_code == 200
    assert call(client, "agent-key-B", "request_refund", {"capture_id": "C1", "amount": "5.00", "reason": "r"}).status_code == 200
    assert seen == ["Bearer agent-key-A", "Bearer agent-key-B"]


def test_foreign_host_header_rejected(hosted):
    client, seen = hosted
    assert call(client, "agent-key-A", "get_agent_policy", host="evil.example.com").status_code == 421
    assert seen == []


def test_tool_result_over_http(hosted):
    client, _ = hosted
    body = call(client, "agent-key-A", "request_refund", {"capture_id": "C1", "amount": "250.00", "reason": "r"}).json()
    out = body["result"]["structuredContent"]
    assert out["decision"] == "BLOCK" and "No refund was executed" in out["outcome"]


# ---------------------------------------------------------------- hosted config rules
@pytest.fixture
def hosted_env(monkeypatch):
    for k in ("VAULTLINE_AGENT_KEY", "VAULTLINE_MCP_TRANSPORT", "VAULTLINE_MCP_ALLOWED_HOSTS", "RENDER_EXTERNAL_HOSTNAME"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("VAULTLINE_DEPLOYMENT", "hosted")
    monkeypatch.setenv("VAULTLINE_API_BASE_URL", "https://vaultline-api.onrender.com")
    monkeypatch.setenv("RENDER_EXTERNAL_HOSTNAME", "vaultline-mcp.onrender.com")
    return monkeypatch


def test_hosted_settings_ok(hosted_env):
    s = Settings.from_env()
    assert (s.transport, s.hosted, s.host, s.agent_key) == ("http", True, "0.0.0.0", "")
    assert s.allowed_hosts == ("vaultline-mcp.onrender.com",)


@pytest.mark.parametrize("url", ["http://localhost:8000", "https://localhost:8000", "https://127.0.0.1", "http://vaultline-api.onrender.com", "https://0.0.0.0"])
def test_hosted_refuses_localhost_or_plain_http_backend(hosted_env, url):
    hosted_env.setenv("VAULTLINE_API_BASE_URL", url)
    with pytest.raises(ConfigError):
        Settings.from_env()


def test_hosted_requires_backend_url(hosted_env):
    hosted_env.delenv("VAULTLINE_API_BASE_URL")
    with pytest.raises(ConfigError):
        Settings.from_env()


def test_hosted_refuses_shared_agent_key(hosted_env):
    hosted_env.setenv("VAULTLINE_AGENT_KEY", "a-shared-key")
    with pytest.raises(ConfigError):
        Settings.from_env()


def test_hosted_refuses_stdio(hosted_env):
    hosted_env.setenv("VAULTLINE_MCP_TRANSPORT", "stdio")
    with pytest.raises(ConfigError):
        Settings.from_env()


def test_hosted_requires_public_hostname(hosted_env):
    hosted_env.delenv("RENDER_EXTERNAL_HOSTNAME")
    with pytest.raises(ConfigError):
        Settings.from_env()
