import os
import pytest
from decimal import Decimal
from fastapi.testclient import TestClient
from backend.main import create_app
from backend.config import Config
from backend.fake_adapter import FakePaypalAdapter

@pytest.fixture
def integration_app(tmp_path):
    db_file = str(tmp_path / "frontend_integration.db")
    cfg = Config(
        db_path=db_file,
        merchant_id="merch_test_1",
        judge_username="judge_merchant",
        judge_password="vaultline_judge_password_123!",
        agent_key="test_agent_secret_key_789",
        max_auto_amount=Decimal("10.00"),
        max_escalate_amount=Decimal("25.00"),
        demo_mode=True,
    )
    adapter = FakePaypalAdapter()
    adapter.seed_capture("CAP_A", Decimal("300.00"), "USD", "COMPLETED")
    return create_app(config=cfg, adapter=adapter)

def test_frontend_backend_integration_full_lifecycle(integration_app):
    client = TestClient(integration_app)

    # 1. Verify CORS Preflight from frontend dev origin
    cors_resp = client.options(
        "/api/v1/auth/login",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "Content-Type",
        },
    )
    assert cors_resp.status_code == 200
    assert cors_resp.headers.get("access-control-allow-origin") == "http://localhost:5173"

    # 2. Verify Merchant Login
    login_resp = client.post(
        "/api/v1/auth/login",
        json={"username": "judge_merchant", "password": "vaultline_judge_password_123!"},
        headers={"Origin": "http://localhost:5173"},
    )
    assert login_resp.status_code == 200
    session_data = login_resp.json()
    assert "session_token" in session_data
    assert session_data["username"] == "judge_merchant"
    assert session_data["role"] == "judge"
    session_token = session_data["session_token"]
    merchant_headers = {
        "Authorization": f"Bearer {session_token}",
        "Origin": "http://localhost:5173",
    }

    # 3. Verify Initial Dashboard Summary & Data
    summary_resp = client.get("/api/v1/dashboard/summary", headers=merchant_headers)
    assert summary_resp.status_code == 200
    summary = summary_resp.json()
    assert summary["total_requests"] == 0
    assert summary["pending_approvals"] == 0

    refunds_resp = client.get("/api/v1/dashboard/refunds?limit=200", headers=merchant_headers)
    assert refunds_resp.status_code == 200
    assert refunds_resp.json()["items"] == []

    reqs_resp = client.get("/api/merchant/requests", headers=merchant_headers)
    assert reqs_resp.status_code == 200
    assert reqs_resp.json() == []

    # 4. Agent submits requests ($5 auto-allow, $20 escalate, $50 block)
    agent_headers = {"Authorization": "Bearer test_agent_secret_key_789"}
    
    # $5.00 -> ALLOW (Auto-approved)
    r_allow = client.post(
        "/api/v1/refunds",
        json={"capture_id": "CAP_A", "amount": "5.00", "currency": "USD", "reason": "Customer discount error"},
        headers=agent_headers,
    )
    assert r_allow.status_code == 200
    allow_data = r_allow.json()
    assert allow_data["decision"] == "ALLOW"
    assert allow_data["execution_status"] == "COMPLETED"
    assert allow_data["paypal_refund_id"] is not None

    # $20.00 -> ESCALATE (Awaiting judge approval)
    r_esc = client.post(
        "/api/v1/refunds",
        json={"capture_id": "CAP_A", "amount": "20.00", "currency": "USD", "reason": "Partial satisfaction credit"},
        headers=agent_headers,
    )
    assert r_esc.status_code == 200
    esc_data = r_esc.json()
    assert esc_data["decision"] == "ESCALATE"
    assert esc_data["approval_required"] is True
    assert esc_data["approval_id"] is not None
    approval_id = esc_data["approval_id"]

    # $50.00 -> BLOCK (Exceeds cap)
    r_block = client.post(
        "/api/v1/refunds",
        json={"capture_id": "CAP_A", "amount": "50.00", "currency": "USD", "reason": "Unauthorized refund claim"},
        headers=agent_headers,
    )
    assert r_block.status_code == 200
    assert r_block.json()["decision"] == "BLOCK"

    # 5. Verify Frontend Dashboard Data & Request Lists
    summary_resp2 = client.get("/api/v1/dashboard/summary", headers=merchant_headers)
    assert summary_resp2.status_code == 200
    summary2 = summary_resp2.json()
    assert summary2["total_requests"] == 3
    assert summary2["allowed"] == 1
    assert summary2["escalated"] == 1
    assert summary2["blocked"] == 1
    assert summary2["pending_approvals"] == 1
    assert summary2["completed_refunds"] == 1

    refunds_list_resp = client.get("/api/v1/dashboard/refunds?limit=200", headers=merchant_headers)
    assert refunds_list_resp.status_code == 200
    refund_items = refunds_list_resp.json()["items"]
    assert len(refund_items) == 3

    merchant_reqs_resp = client.get("/api/merchant/requests", headers=merchant_headers)
    assert merchant_reqs_resp.status_code == 200
    merchant_reqs = merchant_reqs_resp.json()
    assert len(merchant_reqs) == 3
    
    # Locate escalated item and verify approval_id is present
    escalated_item = next(item for item in merchant_reqs if item["decision"] == "ESCALATE")
    assert escalated_item["approval_id"] == approval_id
    assert escalated_item["status"] == "AWAITING_APPROVAL"

    # 6. Verify Approvals Action (/api/v1/approvals/{approval_id}/approve)
    approve_resp = client.post(
        f"/api/v1/approvals/{approval_id}/approve",
        json={"reason": "Approved by judge"},
        headers=merchant_headers,
    )
    assert approve_resp.status_code == 200
    approved_data = approve_resp.json()
    assert approved_data["decision"] == "ALLOW"
    assert approved_data["status"] in ("APPROVED", "COMPLETED")
    assert approved_data["execution_status"] == "COMPLETED"
    assert approved_data["paypal_refund_id"] is not None

    # Summary updated: pending_approvals is now 0, completed_refunds is now 2
    summary_resp3 = client.get("/api/v1/dashboard/summary", headers=merchant_headers)
    summary3 = summary_resp3.json()
    assert summary3["pending_approvals"] == 0
    assert summary3["completed_refunds"] == 2

    # 7. Verify Audit History (/api/v1/audit)
    audit_resp = client.get("/api/v1/audit", headers=merchant_headers)
    assert audit_resp.status_code == 200
    audit_data = audit_resp.json()
    assert audit_data["is_valid"] is True
    assert "intact" in audit_data["message"].lower()
    assert audit_data["total_events"] > 0
    events = audit_data["events"]
    event_types = [e["event_type"] for e in events]
    assert "DECISION" in event_types
    assert "APPROVAL_PENDING" in event_types
    assert "APPROVAL" in event_types
    assert "EXECUTION" in event_types
