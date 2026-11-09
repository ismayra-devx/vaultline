import pytest
from decimal import Decimal
from starlette.testclient import TestClient

from backend.config import Config
from backend.db import Database
from backend.fake_adapter import FakePaypalAdapter
from backend.adapter_interface import PayPalAdapter, CaptureResult, RefundResult
from backend.services import VaultlineService
from backend.main import create_app

@pytest.fixture
def app_and_deps(tmp_path):
    db_file = str(tmp_path / "test_v1.db")
    cfg = Config(
        db_path=db_file,
        merchant_id="merch_default",
        judge_username="judge_merchant",
        judge_password="vaultline_judge_password_123!",
        agent_id="agent_default",
        agent_key="vl_agent_secret_key_abc123",
        max_auto_amount=Decimal("10.00"),
        max_escalate_amount=Decimal("25.00"),
        currency="USD",
        approval_expiry_seconds=900,
    )
    db = Database(db_file)
    db.init_schema()
    db.seed_defaults(cfg)

    adapter = FakePaypalAdapter()
    adapter.seed_capture("CAP_A", Decimal("300.00"), "USD", "COMPLETED")
    adapter.seed_capture("CAP_B", Decimal("300.00"), "USD", "COMPLETED")

    service = VaultlineService(db=db, adapter=adapter, config=cfg)
    app = create_app(config=cfg, db=db, adapter=adapter, service=service)
    client = TestClient(app)

    return {
        "client": client,
        "adapter": adapter,
        "service": service,
        "cfg": cfg,
        "db": db,
    }

def get_merchant_token(client: TestClient) -> str:
    res = client.post(
        "/api/v1/auth/login",
        json={"username": "judge_merchant", "password": "vaultline_judge_password_123!"},
    )
    assert res.status_code == 200
    return res.json()["session_token"]

def test_paypal_adapter_protocol_and_dataclasses():
    adapter = FakePaypalAdapter()
    assert isinstance(adapter, PayPalAdapter)

    cap = CaptureResult(capture_id="CAP_1", status="COMPLETED", amount=Decimal("300.00"), currency="USD")
    assert cap.capture_id == "CAP_1"
    assert cap["id"] == "CAP_1"
    assert cap["amount"]["value"] == "300.00"
    assert cap.get("status") == "COMPLETED"

    ref = RefundResult(refund_id="REF_1", capture_id="CAP_1", status="COMPLETED", amount=Decimal("10.00"), currency="USD")
    assert ref.refund_id == "REF_1"
    assert ref["id"] == "REF_1"
    assert ref["status"] == "COMPLETED"
    assert ref.get("id") == "REF_1"

def test_v1_refund_allow_execution(app_and_deps):
    client = app_and_deps["client"]
    adapter = app_and_deps["adapter"]

    # $5.00 -> ALLOW -> executes immediately
    res = client.post(
        "/api/v1/refunds",
        headers={"X-Agent-Key": "vl_agent_secret_key_abc123"},
        json={
            "capture_id": "CAP_A",
            "amount": "5.00",
            "currency": "USD",
            "reason": "Defective item return",
            "client_request_id": "client_req_001",
        },
    )
    assert res.status_code == 200
    data = res.json()
    assert data["decision"] == "ALLOW"
    assert data["status"] == "COMPLETED"
    assert data["execution_status"] == "COMPLETED"
    assert data["amount"] == "5.00"
    assert data["currency"] == "USD"
    assert data["refund_id"].startswith("REF_")
    assert data["client_request_id"] == "client_req_001"
    assert adapter.money_call_count == 1

def test_v1_refund_idempotency_by_client_request_id(app_and_deps):
    client = app_and_deps["client"]
    adapter = app_and_deps["adapter"]

    # Call 1: $5.00 with unique client_request_id
    res1 = client.post(
        "/api/v1/refunds",
        headers={"X-Agent-Key": "vl_agent_secret_key_abc123"},
        json={
            "capture_id": "CAP_A",
            "amount": "5.00",
            "currency": "USD",
            "client_request_id": "idempotent_key_42",
        },
    )
    assert res1.status_code == 200
    data1 = res1.json()
    assert adapter.money_call_count == 1

    # Call 2: Identical payload with same client_request_id -> returns same record, count unchanged
    res2 = client.post(
        "/api/v1/refunds",
        headers={"X-Agent-Key": "vl_agent_secret_key_abc123"},
        json={
            "capture_id": "CAP_A",
            "amount": "5.00",
            "currency": "USD",
            "client_request_id": "idempotent_key_42",
        },
    )
    assert res2.status_code == 200
    data2 = res2.json()
    assert data1["request_id"] == data2["request_id"]
    assert data1["refund_id"] == data2["refund_id"]
    assert adapter.money_call_count == 1  # No duplicate execution!

    # Call 3: Same client_request_id with DIFFERENT amount -> HTTP 409 Conflict
    res3 = client.post(
        "/api/v1/refunds",
        headers={"X-Agent-Key": "vl_agent_secret_key_abc123"},
        json={
            "capture_id": "CAP_A",
            "amount": "7.00",
            "currency": "USD",
            "client_request_id": "idempotent_key_42",
        },
    )
    assert res3.status_code == 409
    assert adapter.money_call_count == 1

def test_v1_refund_escalate_and_merchant_approval_flow(app_and_deps):
    client = app_and_deps["client"]
    adapter = app_and_deps["adapter"]

    # 1. Agent submits $20.00 -> ESCALATE
    res = client.post(
        "/api/v1/refunds",
        headers={"X-Agent-Key": "vl_agent_secret_key_abc123"},
        json={
            "capture_id": "CAP_A",
            "amount": "20.00",
            "currency": "USD",
            "reason": "Late shipment refund",
        },
    )
    assert res.status_code == 200
    data = res.json()
    assert data["decision"] == "ESCALATE"
    assert data["status"] == "AWAITING_APPROVAL"
    assert data["approval_id"] is not None
    assert adapter.money_call_count == 0
    req_id = data["request_id"]
    approval_id = data["approval_id"]

    # 2. Agent checks status via GET /api/v1/refunds/{request_id}
    res_status = client.get(
        f"/api/v1/refunds/{req_id}",
        headers={"X-Agent-Key": "vl_agent_secret_key_abc123"},
    )
    assert res_status.status_code == 200
    assert res_status.json()["status"] == "AWAITING_APPROVAL"

    # 3. Agent checks policy via GET /api/v1/agents/me/policy
    res_pol = client.get(
        "/api/v1/agents/me/policy",
        headers={"X-Agent-Key": "vl_agent_secret_key_abc123"},
    )
    assert res_pol.status_code == 200
    assert res_pol.json()["version"] == "v1"

    # 4. Merchant logs in and checks pending approvals queue
    token = get_merchant_token(client)
    res_queue = client.get(
        "/api/v1/approvals",
        headers={"X-Session-Token": token},
    )
    assert res_queue.status_code == 200
    pending_items = res_queue.json()
    assert len(pending_items) >= 1
    assert any(item["request_id"] == req_id for item in pending_items)

    # 5. Merchant approves request using approval_id
    res_approve = client.post(
        f"/api/v1/approvals/{approval_id}/approve",
        headers={"X-Session-Token": token},
        json={"reason": "Customer is VIP"},
    )
    assert res_approve.status_code == 200
    appr_data = res_approve.json()
    assert appr_data["decision"] == "ALLOW"
    assert appr_data["status"] == "COMPLETED"
    assert appr_data["execution_status"] == "COMPLETED"
    assert appr_data["refund_id"].startswith("REF_")
    assert adapter.money_call_count == 1

def test_v1_refund_block_zero_calls(app_and_deps):
    client = app_and_deps["client"]
    adapter = app_and_deps["adapter"]

    # $250 -> BLOCK -> adapter calls must be 0
    res = client.post(
        "/api/v1/refunds",
        headers={"X-Agent-Key": "vl_agent_secret_key_abc123"},
        json={
            "capture_id": "CAP_A",
            "amount": "250.00",
            "currency": "USD",
        },
    )
    assert res.status_code == 200
    data = res.json()
    assert data["decision"] == "BLOCK"
    assert data["status"] == "BLOCKED"
    assert data["execution_status"] == "NOT_STARTED"
    assert adapter.money_call_count == 0

def test_v1_dashboard_and_audit(app_and_deps):
    client = app_and_deps["client"]
    token = get_merchant_token(client)

    # Submit an ALLOW and a BLOCK
    client.post(
        "/api/v1/refunds",
        headers={"X-Agent-Key": "vl_agent_secret_key_abc123"},
        json={"capture_id": "CAP_B", "amount": "5.00", "currency": "USD"},
    )
    client.post(
        "/api/v1/refunds",
        headers={"X-Agent-Key": "vl_agent_secret_key_abc123"},
        json={"capture_id": "CAP_B", "amount": "100.00", "currency": "USD"},
    )

    # GET /api/v1/dashboard/summary
    res_summary = client.get(
        "/api/v1/dashboard/summary",
        headers={"X-Session-Token": token},
    )
    assert res_summary.status_code == 200
    summary = res_summary.json()
    assert summary["total_requests"] >= 2
    assert summary["allowed"] >= 1
    assert summary["blocked"] >= 1

    # GET /api/v1/dashboard/refunds
    res_list = client.get(
        "/api/v1/dashboard/refunds?limit=10&offset=0",
        headers={"X-Session-Token": token},
    )
    assert res_list.status_code == 200
    refunds_data = res_list.json()
    assert refunds_data["total"] >= 2
    assert len(refunds_data["items"]) >= 2
    assert any(item["decision"] == "BLOCK" and item["risk_level"] == "HIGH" for item in refunds_data["items"])

    # GET /api/v1/dashboard/risk
    res_risk = client.get(
        "/api/v1/dashboard/risk",
        headers={"X-Session-Token": token},
    )
    assert res_risk.status_code == 200
    risk = res_risk.json()
    assert risk["high_risk_count"] >= 1
    assert risk["low_risk_count"] >= 1

    # GET /api/v1/audit
    res_audit = client.get(
        "/api/v1/audit",
        headers={"X-Session-Token": token},
    )
    assert res_audit.status_code == 200
    audit = res_audit.json()
    assert audit["is_valid"] is True
    assert "valid" in audit["message"].lower()

def test_v1_auth_boundary_separation(app_and_deps):
    client = app_and_deps["client"]
    token = get_merchant_token(client)

    # 1. Agent key cannot access merchant dashboard or approvals -> 403
    res1 = client.get(
        "/api/v1/approvals",
        headers={"X-Agent-Key": "vl_agent_secret_key_abc123"},
    )
    assert res1.status_code == 403

    res2 = client.get(
        "/api/v1/dashboard/summary",
        headers={"X-Agent-Key": "vl_agent_secret_key_abc123"},
    )
    assert res2.status_code == 403

    # 2. Merchant token cannot submit refund on agent route -> 403
    res3 = client.post(
        "/api/v1/refunds",
        headers={"X-Session-Token": token},
        json={"capture_id": "CAP_A", "amount": "5.00", "currency": "USD"},
    )
    assert res3.status_code == 403

