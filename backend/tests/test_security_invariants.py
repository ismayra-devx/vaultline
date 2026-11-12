import pytest
from decimal import Decimal
from backend.fake_adapter import FakePaypalAdapter
from backend.services import VaultlineService

def test_invariant_1_float_rejected_in_schema(client, agent_headers):
    # Sending raw float 10.0 or 10.5 must fail at schema level
    res = client.post(
        "/api/agent/refund-requests",
        json={"capture_id": "CAP_A", "amount": 10.0, "currency": "USD", "action": "refund"},
        headers=agent_headers,
    )
    assert res.status_code == 422
    assert "float" in res.text.lower() or "decimal" in res.text.lower()

def test_invariant_2_forbid_extra_identity_fields(client, agent_headers):
    for bad_field in ("merchant_id", "agent_id", "user_id"):
        res = client.post(
            "/api/agent/refund-requests",
            json={
                "capture_id": "CAP_A",
                "amount": "5.00",
                "currency": "USD",
                "action": "refund",
                bad_field: "spoofed_id",
            },
            headers=agent_headers,
        )
        assert res.status_code == 422

def test_invariant_3_cross_credential_isolation(client, agent_headers, merchant_headers):
    # Agent key forbidden on all merchant endpoints
    assert client.get("/api/merchant/requests", headers=agent_headers).status_code == 403
    assert client.get("/api/merchant/audit", headers=agent_headers).status_code == 403

    # Merchant token forbidden on all agent endpoints
    assert (
        client.post(
            "/api/agent/refund-requests",
            json={"capture_id": "CAP_A", "amount": "5.00", "currency": "USD", "action": "refund"},
            headers=merchant_headers,
        ).status_code
        == 403
    )
    assert client.get("/api/agent/policy", headers=merchant_headers).status_code == 403

def test_invariant_5_ai_error_never_allows(client, agent_headers):
    # If an AI risk flag is present on an otherwise $5 ALLOW request, it must become ESCALATE
    res = client.post(
        "/api/agent/refund-requests",
        json={
            "capture_id": "CAP_A",
            "amount": "5.00",
            "currency": "USD",
            "action": "refund",
            "ai_risk_flag": True,
            "ai_risk_explanation": "Model anomaly score 0.95",
        },
        headers=agent_headers,
    )
    assert res.status_code == 200
    data = res.json()
    assert data["decision"] == "ESCALATE"
    assert data["approval_required"] is True

def test_timeout_gives_unknown_then_reconcile_reuses_same_request_id(
    service, fake_adapter, test_config
):
    # Set fake adapter to timeout
    fake_adapter.set_timeout(True)

    # Agent creates a $5 refund which tries to auto-execute
    resp = service.create_refund_request(
        agent_id=test_config.agent_id,
        capture_id="CAP_A",
        amount=Decimal("5.00"),
        currency="USD",
        action="refund",
    )
    assert resp.decision == "ALLOW"
    assert resp.execution_status == "UNKNOWN"

    # Execution record exists and has a stable paypal_request_id
    conn = service.db.get_connection()
    cursor = conn.execute(
        "SELECT paypal_request_id, status FROM executions WHERE request_id = ?",
        (resp.request_id,),
    )
    row = cursor.fetchone()
    assert row is not None
    paypal_req_id = row[0]
    assert row[1] == "UNKNOWN"

    # Reconcile after clearing timeout
    fake_adapter.set_timeout(False)
    recon_resp = service.reconcile_request(resp.request_id)

    assert recon_resp.execution_status == "COMPLETED"
    # Replay verified that same paypal_request_id was used
    cursor = conn.execute(
        "SELECT paypal_request_id, status FROM executions WHERE request_id = ?",
        (resp.request_id,),
    )
    recon_row = cursor.fetchone()
    assert recon_row[0] == paypal_req_id
    assert recon_row[1] == "COMPLETED"

def test_http_2xx_without_completed_is_not_success(service, fake_adapter, test_config):
    # Configure adapter to return PENDING (HTTP 200 equivalent in sandbox)
    fake_adapter.set_configured_result("PENDING")

    resp = service.create_refund_request(
        agent_id=test_config.agent_id,
        capture_id="CAP_A",
        amount=Decimal("5.00"),
        currency="USD",
        action="refund",
    )
    # Must NOT be completed
    assert resp.execution_status == "PENDING"
    assert resp.decision == "ALLOW"

    # Configure adapter to return FAILED
    fake_adapter.set_configured_result("FAILED")
    resp_failed = service.create_refund_request(
        agent_id=test_config.agent_id,
        capture_id="CAP_A",
        amount=Decimal("4.00"),
        currency="USD",
        action="refund",
    )
    assert resp_failed.execution_status == "FAILED"

def test_every_rejection_writes_audit_event(client, agent_headers, merchant_headers):
    # Request blocked: amount $250
    client.post(
        "/api/agent/refund-requests",
        json={"capture_id": "CAP_A", "amount": "250.00", "currency": "USD", "action": "refund"},
        headers=agent_headers,
    )

    # Request escalated then denied
    esc_res = client.post(
        "/api/agent/refund-requests",
        json={"capture_id": "CAP_A", "amount": "15.00", "currency": "USD", "action": "refund"},
        headers=agent_headers,
    )
    req_id = esc_res.json()["request_id"]
    client.post(f"/api/merchant/requests/{req_id}/deny", headers=merchant_headers)

    # Inspect audit events
    audit_res = client.get("/api/merchant/audit", headers=merchant_headers)
    assert audit_res.status_code == 200
    events = audit_res.json()["events"]

    event_types = [e["event_type"] for e in events]
    assert "REJECTION" in event_types
    assert "DENIAL" in event_types

