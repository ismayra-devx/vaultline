"""Hosted-mode guard rails + full service flow with the real adapter against simulated PayPal."""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.config import Config
from backend.db import Database
from backend.hosted import HostedConfigError, enforce_hosted_config
from backend.main import create_app
from backend.paypal.sandbox_adapter import PayPalSandboxAdapter
from backend.tests.test_paypal_sandbox_adapter import FakePayPal

AGENT_KEY = "hosted-agent-key-0123456789abcdef"
JUDGE_PW = "hosted-judge-password-0123456789"


@pytest.fixture
def hosted_env(monkeypatch):
    monkeypatch.setenv("VAULTLINE_DEPLOYMENT", "hosted")
    monkeypatch.setenv("VAULTLINE_PAYPAL_MODE", "sandbox")
    monkeypatch.setenv("PAYPAL_CLIENT_ID", "id")
    monkeypatch.setenv("PAYPAL_CLIENT_SECRET", "secret")


def cfg(tmp_path, **kw):
    c = Config()
    c.db_path = str(tmp_path / "v.db")
    c.agent_key = kw.get("agent_key", AGENT_KEY)
    c.judge_password = kw.get("judge_password", JUDGE_PW)
    return c


def test_hosted_refuses_public_default_secrets(hosted_env, tmp_path):
    with pytest.raises(HostedConfigError) as e:
        enforce_hosted_config(cfg(tmp_path, agent_key="vl_agent_secret_key_abc123"))
    assert "VAULTLINE_AGENT_KEY" in str(e.value) and "vl_agent_secret_key_abc123" not in str(e.value)


def test_hosted_refuses_short_secrets(hosted_env, tmp_path):
    with pytest.raises(HostedConfigError):
        enforce_hosted_config(cfg(tmp_path, judge_password="short"))


def test_hosted_refuses_fake_adapter(hosted_env, monkeypatch, tmp_path):
    monkeypatch.setenv("VAULTLINE_PAYPAL_MODE", "fake")
    with pytest.raises(HostedConfigError):
        enforce_hosted_config(cfg(tmp_path))


def test_hosted_refuses_missing_paypal_creds(hosted_env, monkeypatch, tmp_path):
    monkeypatch.delenv("PAYPAL_CLIENT_SECRET")
    with pytest.raises(HostedConfigError):
        enforce_hosted_config(cfg(tmp_path))


def test_hosted_db_rotates_away_from_public_default_key(hosted_env, tmp_path):
    c = cfg(tmp_path)
    # Simulate a DB first created with the public demo key.
    old = Config(); old.db_path = c.db_path
    d = Database(c.db_path); d.init_schema(); d.seed_defaults(old)
    app = create_app(config=c, db=Database(c.db_path), adapter=PayPalSandboxAdapter("id", "s", transport=httpx.MockTransport(FakePayPal())))
    client = TestClient(app)
    assert client.get("/api/v1/agents/me/policy", headers={"Authorization": "Bearer vl_agent_secret_key_abc123"}).status_code == 401
    assert client.get("/api/v1/agents/me/policy", headers={"Authorization": f"Bearer {AGENT_KEY}"}).status_code == 200


@pytest.fixture
def sandbox_app(hosted_env, tmp_path):
    pp = FakePayPal()
    adapter = PayPalSandboxAdapter("id", "secret", transport=httpx.MockTransport(pp))
    c = cfg(tmp_path)
    app = create_app(config=c, db=Database(c.db_path), adapter=adapter)
    client = TestClient(app)
    tok = client.post("/api/merchant/login", json={"username": c.judge_username, "password": JUDGE_PW}).json()["session_token"]
    return client, pp, {"Authorization": f"Bearer {tok}"}


AGENT = {"Authorization": f"Bearer {AGENT_KEY}"}


def refund(client, amount, cap="CAPTURE1", cid=None):
    body = {"capture_id": cap, "amount": amount, "currency": "USD", "reason": "t"}
    if cid:
        body["client_request_id"] = cid
    return client.post("/api/v1/refunds", json=body, headers=AGENT).json()


def test_no_demo_captures_in_sandbox_mode(sandbox_app):
    client, pp, _ = sandbox_app
    r = refund(client, "5.00", cap="CAP_A")
    assert r["decision"] == "BLOCK" and r["adapter_call_count"] == 0


def test_capture_registration_requires_merchant(sandbox_app):
    client, _, merchant = sandbox_app
    assert client.post("/api/v1/captures/register", json={"capture_id": "CAPTURE1"}, headers=AGENT).status_code == 403
    assert client.post("/api/v1/captures/register", json={"capture_id": "CAPTURE1"}).status_code == 401
    r = client.post("/api/v1/captures/register", json={"capture_id": "CAPTURE1"}, headers=merchant)
    assert r.status_code == 200 and r.json() == {"capture_id": "CAPTURE1", "status": "COMPLETED", "amount": "30.00", "registered": True}
    assert client.post("/api/v1/captures/register", json={"capture_id": "MISSING"}, headers=merchant).status_code == 404


def test_full_flow_with_real_adapter(sandbox_app):
    client, pp, merchant = sandbox_app
    client.post("/api/v1/captures/register", json={"capture_id": "CAPTURE1"}, headers=merchant)

    blocked = refund(client, "250.00")
    assert blocked["decision"] == "BLOCK" and blocked["adapter_call_count"] == 0
    assert not [c for c in pp.calls if c.url.path.endswith("/refund")]  # zero money-moving calls

    allowed = refund(client, "5.00")
    assert allowed["decision"] == "ALLOW" and allowed["execution_status"] == "COMPLETED"
    assert allowed["paypal_refund_id"] == "REF0001"

    esc = refund(client, "20.00")
    assert esc["decision"] == "ESCALATE" and esc["execution_status"] == "NOT_STARTED"
    approved = client.post(f"/api/v1/approvals/{esc['approval_id']}/approve", json={}, headers=merchant).json()
    assert approved["execution_status"] == "COMPLETED" and approved["paypal_refund_id"] == "REF0002"


def test_paypal_timeout_is_unknown_not_failed(sandbox_app):
    client, pp, merchant = sandbox_app
    client.post("/api/v1/captures/register", json={"capture_id": "CAPTURE1"}, headers=merchant)
    pp.refund_mode = "timeout"
    r = refund(client, "5.00")
    assert r["decision"] == "ALLOW" and r["execution_status"] == "UNKNOWN"
