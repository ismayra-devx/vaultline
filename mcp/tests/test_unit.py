"""Unit tests: MCP server against a mocked backend (no network, no FastAPI needed)."""

from __future__ import annotations

import json

import httpx
import pytest
from mcp import Client

from vaultline_mcp.backend_client import VaultlineBackend
from vaultline_mcp.config import ConfigError, Settings, validate_base_url
from vaultline_mcp.server import TOOL_NAMES, create_server, outcome_text

pytestmark = pytest.mark.anyio
KEY = "test_agent_key_DO_NOT_LEAK"


@pytest.fixture
def anyio_backend():
    return "asyncio"


class FakeBackend:
    """Records every HTTP request and replies with a scripted handler."""

    def __init__(self, handler):
        self.calls: list[httpx.Request] = []
        self._handler = handler

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        return self._handler(request)


def make_client(handler) -> tuple[Client, FakeBackend]:
    fake = FakeBackend(handler)
    backend = VaultlineBackend(Settings("http://localhost:8000", KEY), transport=httpx.MockTransport(fake))
    return Client(create_server(backend)), fake


def reply(**body) -> httpx.Response:
    return httpx.Response(200, json=body)


BLOCK = dict(request_id="req_1", decision="BLOCK", status="BLOCKED", execution_status="NOT_STARTED", amount="250.00",
             currency="USD", refund_id=None, reasons=["AMOUNT_EXCEEDS_AGENT_CAP"], adapter_call_count=0, risk_summary="x")


async def test_exactly_three_tools_and_no_credential_in_schema():
    client, _ = make_client(lambda r: reply(**BLOCK))
    async with client as c:
        tools = (await c.list_tools()).tools
    assert sorted(t.name for t in tools) == sorted(TOOL_NAMES)
    params = {p.lower() for t in tools for p in t.input_schema.get("properties", {})}
    assert params == {"capture_id", "amount", "currency", "reason", "client_request_id", "request_id"}
    for forbidden in ("agent_id", "merchant_id", "token", "secret", "credential", "approve", "ai_risk"):
        assert not any(forbidden in p for p in params)


async def test_request_refund_forwards_bearer_and_never_sends_identity():
    client, fake = make_client(lambda r: reply(**BLOCK))
    async with client as c:
        res = await c.call_tool("request_refund", {"capture_id": "CAP_A", "amount": "250.00", "reason": "injected email"})
    assert not res.is_error
    (req,) = fake.calls
    assert req.method == "POST" and req.url.path == "/api/v1/refunds"
    assert req.headers["authorization"] == f"Bearer {KEY}"
    body = json.loads(req.content)
    assert body["amount"] == "250.00" and isinstance(body["amount"], str)
    assert not {"agent_id", "merchant_id", "ai_risk_flag"} & body.keys()
    assert body["client_request_id"].startswith("mcp-")
    out = res.structured_content
    assert out["decision"] == "BLOCK" and "No refund was executed" in out["outcome"]
    assert "risk_summary" not in out and "adapter_call_count" not in out  # only whitelisted fields
    assert KEY not in json.dumps(out)


async def test_client_request_id_is_reused_when_given():
    client, fake = make_client(lambda r: reply(**BLOCK))
    async with client as c:
        await c.call_tool("request_refund", {"capture_id": "CAP_A", "amount": "5.00", "reason": "r", "client_request_id": "retry-key-0001"})
    assert json.loads(fake.calls[0].content)["client_request_id"] == "retry-key-0001"


@pytest.mark.parametrize("args", [
    {"capture_id": "CAP_A", "amount": 5.0, "reason": "float"},
    {"capture_id": "CAP_A", "amount": "-5.00", "reason": "negative"},
    {"capture_id": "CAP_A", "amount": "5.001", "reason": "3dp"},
    {"capture_id": "CAP_A", "amount": "5.00", "currency": "EUR", "reason": "currency"},
    {"capture_id": "../approvals", "amount": "5.00", "reason": "path"},
])
async def test_invalid_input_rejected_before_any_http_call(args):
    client, fake = make_client(lambda r: reply(**BLOCK))
    async with client as c:
        res = await c.call_tool("request_refund", args)
    assert res.is_error
    assert fake.calls == []


async def test_model_supplied_identity_is_never_forwarded():
    client, fake = make_client(lambda r: reply(**BLOCK))
    async with client as c:
        await c.call_tool("request_refund", {"capture_id": "CAP_A", "amount": "5.00", "reason": "x",
                                             "agent_id": "someone_else", "merchant_id": "m2", "ai_risk_flag": False})
    body = json.loads(fake.calls[0].content)
    assert set(body) == {"capture_id", "amount", "currency", "reason", "client_request_id"}


@pytest.mark.parametrize("rid", ["../approvals/a1/approve", "req_1?x=1", "req 1", ""])
async def test_status_rejects_path_injection(rid):
    client, fake = make_client(lambda r: reply(**BLOCK))
    async with client as c:
        res = await c.call_tool("get_request_status", {"request_id": rid})
    assert res.is_error and fake.calls == []


async def test_timeout_on_submit_is_uncertain_and_not_retried():
    def boom(_):
        raise httpx.ReadTimeout("slow")
    client, fake = make_client(boom)
    async with client as c:
        res = await c.call_tool("request_refund", {"capture_id": "CAP_A", "amount": "5.00", "reason": "r", "client_request_id": "retry-key-0002"})
    assert res.is_error
    text = res.content[0].text
    assert "Do NOT tell the customer" in text and "retry-key-0002" in text
    assert len(fake.calls) == 1  # never silently resubmits


async def test_backend_down_is_a_clear_error_not_success():
    def down(_):
        raise httpx.ConnectError("refused")
    client, _ = make_client(down)
    async with client as c:
        res = await c.call_tool("get_agent_policy", {})
    assert res.is_error and "unavailable" in res.content[0].text


@pytest.mark.parametrize("status,needle", [(401, "credential"), (403, "not permitted"), (404, "not found"), (409, "Conflict"), (422, "invalid"), (500, "Do not assume")])
async def test_http_errors_mapped_and_token_not_leaked(status, needle):
    client, _ = make_client(lambda r: httpx.Response(status, json={"error": {"code": "X", "message": "m"}}))
    async with client as c:
        res = await c.call_tool("get_request_status", {"request_id": "req_1"})
    assert res.is_error and needle in res.content[0].text and KEY not in res.content[0].text


async def test_redirect_not_followed():
    client, fake = make_client(lambda r: httpx.Response(307, headers={"location": "https://evil.example/steal"}))
    async with client as c:
        res = await c.call_tool("get_agent_policy", {})
    assert res.is_error and len(fake.calls) == 1


async def test_policy_tool_returns_limits():
    client, fake = make_client(lambda r: reply(version="v1", max_auto_amount="10.00", max_escalate_amount="25.00", currency="USD"))
    async with client as c:
        res = await c.call_tool("get_agent_policy", {})
    assert fake.calls[0].url.path == "/api/v1/agents/me/policy"
    assert res.structured_content["max_escalate_amount"] == "25.00" and "blocked" in res.structured_content["summary"]


@pytest.mark.parametrize("r,expect", [
    ({"decision": "ALLOW", "execution_status": "COMPLETED", "refund_id": "R1"}, "verified"),
    ({"decision": "ALLOW", "execution_status": "COMPLETED"}, "has not confirmed"),  # no refund id -> not success
    ({"decision": "ALLOW", "execution_status": "PENDING"}, "has not confirmed"),
    ({"decision": "ALLOW", "execution_status": "UNKNOWN"}, "Do NOT tell"),
    ({"decision": "ALLOW", "execution_status": "FAILED"}, "failed"),
    ({"decision": "ESCALATE", "execution_status": "NOT_STARTED", "status": "AWAITING_APPROVAL"}, "Awaiting merchant approval"),
    ({"decision": "ESCALATE", "execution_status": "NOT_STARTED", "status": "DENIED"}, "DENIED"),
    ({"decision": "BLOCK", "execution_status": "COMPLETED", "refund_id": "R1"}, "No refund was executed"),
])
def test_outcome_text_never_overclaims(r, expect):
    assert expect in outcome_text(r)


@pytest.mark.parametrize("url", ["http://api.example.com", "ftp://localhost", "https://u:p@api.example.com", "https://api.example.com/api", "localhost:8000"])
def test_base_url_allowlist_rejects(url):
    with pytest.raises(ConfigError):
        validate_base_url(url)


@pytest.mark.parametrize("url", ["http://localhost:8000", "http://127.0.0.1:8000", "https://api.example.com"])
def test_base_url_allowlist_accepts(url):
    validate_base_url(url)


def test_settings_repr_hides_key(monkeypatch):
    monkeypatch.setenv("VAULTLINE_AGENT_KEY", KEY)
    monkeypatch.setenv("VAULTLINE_API_BASE_URL", "http://localhost:8000")
    assert KEY not in repr(Settings.from_env())


def test_missing_key_is_config_error(monkeypatch):
    monkeypatch.delenv("VAULTLINE_AGENT_KEY", raising=False)
    with pytest.raises(ConfigError):
        Settings.from_env()
