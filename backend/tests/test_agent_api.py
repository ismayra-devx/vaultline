from decimal import Decimal
import pytest

def test_health_check(client):
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}

def test_agent_refund_allow_executes_automatically(client, agent_headers, fake_adapter):
    payload = {
        "capture_id": "CAP_A",
        "amount": "8.50",
        "currency": "USD",
        "action": "refund",
    }
    res = client.post("/api/agent/refund-requests", json=payload, headers=agent_headers)
    assert res.status_code == 200, res.text
    data = res.json()

    # Verify all expected response fields
    assert "request_id" in data
    assert data["decision"] == "ALLOW"
    assert isinstance(data["reasons"], list)
    assert "risk_summary" in data
    assert data["approval_required"] is False
    assert data["execution_status"] == "COMPLETED"
    assert data["paypal_refund_id"] is not None
    assert data["adapter_call_count"] == 1
    assert fake_adapter.money_call_count == 1

def test_agent_refund_escalate_creates_approval(client, agent_headers, fake_adapter):
    # $20.00 requires escalation
    payload = {
        "capture_id": "CAP_A",
        "amount": "20.00",
        "currency": "USD",
        "action": "refund",
    }
    res = client.post("/api/agent/refund-requests", json=payload, headers=agent_headers)
    assert res.status_code == 200, res.text
    data = res.json()

    assert data["decision"] == "ESCALATE"
    assert data["approval_required"] is True
    assert data["expires_at"] is not None
    assert data["execution_status"] is None or data["execution_status"] == "NOT_STARTED"
    assert data["paypal_refund_id"] is None
    assert data["adapter_call_count"] == 0
    # Money adapter was NOT called
    assert fake_adapter.money_call_count == 0

def test_agent_refund_block_250_zero_adapter_calls(client, agent_headers, fake_adapter):
    payload = {
        "capture_id": "CAP_A",
        "amount": "250.00",
        "currency": "USD",
        "action": "refund",
    }
    res = client.post("/api/agent/refund-requests", json=payload, headers=agent_headers)
    assert res.status_code == 200, res.text
    data = res.json()

    assert data["decision"] == "BLOCK"
    assert data["approval_required"] is False
    assert data["adapter_call_count"] == 0
    assert fake_adapter.money_call_count == 0

def test_float_amount_strictly_rejected_by_schema(client, agent_headers):
    # 10.5 is a JSON float, must be rejected by Pydantic schema
    payload = {
        "capture_id": "CAP_A",
        "amount": 10.5,
        "currency": "USD",
        "action": "refund",
    }
    res = client.post("/api/agent/refund-requests", json=payload, headers=agent_headers)
    assert res.status_code == 422
    assert "float" in res.text.lower() or "decimal" in res.text.lower()

def test_zero_and_negative_amounts_rejected(client, agent_headers, fake_adapter):
    # Zero amount
    res_zero = client.post(
        "/api/agent/refund-requests",
        json={"capture_id": "CAP_A", "amount": "0.00", "currency": "USD", "action": "refund"},
        headers=agent_headers,
    )
    assert res_zero.status_code == 200
    data_zero = res_zero.json()
    assert data_zero["decision"] == "BLOCK"
    assert fake_adapter.money_call_count == 0

    # Negative amount
    res_neg = client.post(
        "/api/agent/refund-requests",
        json={"capture_id": "CAP_A", "amount": "-15.00", "currency": "USD", "action": "refund"},
        headers=agent_headers,
    )
    assert res_neg.status_code == 200
    data_neg = res_neg.json()
    assert data_neg["decision"] == "BLOCK"
    assert fake_adapter.money_call_count == 0

def test_body_supplied_identity_rejected(client, agent_headers):
    # Supplying merchant_id
    res1 = client.post(
        "/api/agent/refund-requests",
        json={
            "capture_id": "CAP_A",
            "amount": "5.00",
            "currency": "USD",
            "action": "refund",
            "merchant_id": "merch_hacked",
        },
        headers=agent_headers,
    )
    assert res1.status_code == 422
    assert "extra" in res1.text.lower() or "forbidden" in res1.text.lower() or "merchant_id" in res1.text.lower()

    # Supplying agent_id
    res2 = client.post(
        "/api/agent/refund-requests",
        json={
            "capture_id": "CAP_A",
            "amount": "5.00",
            "currency": "USD",
            "action": "refund",
            "agent_id": "agent_spoofed",
        },
        headers=agent_headers,
    )
    assert res2.status_code == 422
    assert "extra" in res2.text.lower() or "forbidden" in res2.text.lower() or "agent_id" in res2.text.lower()

def test_revoked_agent_key_gets_401(client, test_db, test_config):
    # Revoke agent in DB
    conn = test_db.get_connection()
    conn.execute("UPDATE agents SET status = 'revoked' WHERE id = ?", (test_config.agent_id,))
    conn.commit()

    res = client.post(
        "/api/agent/refund-requests",
        json={"capture_id": "CAP_A", "amount": "5.00", "currency": "USD", "action": "refund"},
        headers={"Authorization": f"Bearer {test_config.agent_key}"},
    )
    assert res.status_code == 401

def test_merchant_session_cannot_access_agent_endpoints(client, merchant_headers):
    res = client.post(
        "/api/agent/refund-requests",
        json={"capture_id": "CAP_A", "amount": "5.00", "currency": "USD", "action": "refund"},
        headers=merchant_headers,
    )
    assert res.status_code == 403

def test_get_own_request_vs_other_agent_request(client, agent_headers, test_db):
    # Create request 1 by agent_test_1
    res = client.post(
        "/api/agent/refund-requests",
        json={"capture_id": "CAP_A", "amount": "5.00", "currency": "USD", "action": "refund"},
        headers=agent_headers,
    )
    req_id = res.json()["request_id"]

    # Read own request
    get_res = client.get(f"/api/agent/requests/{req_id}", headers=agent_headers)
    assert get_res.status_code == 200
    assert get_res.json()["request_id"] == req_id

    # Create another agent in DB
    import hashlib
    conn = test_db.get_connection()
    other_key = "secret_agent_2_key"
    conn.execute(
        """
        INSERT INTO agents (id, merchant_id, name, api_key_hash, status, created_at)
        VALUES ('agent_other', 'merch_test_1', 'OtherAgent', ?, 'active', '2026-11-09T00:00:00Z')
        """,
        (hashlib.sha256(other_key.encode()).hexdigest(),),
    )
    conn.commit()

    # Agent 2 tries to access Agent 1's request -> 404 or 403
    other_headers = {"Authorization": f"Bearer {other_key}"}
    unauth_res = client.get(f"/api/agent/requests/{req_id}", headers=other_headers)
    assert unauth_res.status_code in (403, 404)

def test_get_agent_policy(client, agent_headers):
    res = client.get("/api/agent/policy", headers=agent_headers)
    assert res.status_code == 200
    data = res.json()
    assert data["version"] == "v1"
    assert data["currency"] == "USD"
    assert data["max_auto_amount"] == "10.00"
    assert data["max_escalate_amount"] == "25.00"

