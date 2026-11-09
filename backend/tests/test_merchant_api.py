from decimal import Decimal
import pytest

def test_merchant_login_success_and_failure(client, test_config):
    # Success
    res = client.post(
        "/api/merchant/login",
        json={
            "username": test_config.judge_username,
            "password": test_config.judge_password,
        },
    )
    assert res.status_code == 200
    data = res.json()
    assert "session_token" in data
    assert data["role"] == "judge"

    # Bad password
    res_bad = client.post(
        "/api/merchant/login",
        json={
            "username": test_config.judge_username,
            "password": "wrong_password",
        },
    )
    assert res_bad.status_code == 401

def test_agent_key_gets_403_on_every_merchant_route(client, agent_headers):
    # 1. GET /api/merchant/requests
    r1 = client.get("/api/merchant/requests", headers=agent_headers)
    assert r1.status_code == 403

    # 2. POST /api/merchant/requests/req_dummy/approve
    r2 = client.post("/api/merchant/requests/req_dummy/approve", headers=agent_headers)
    assert r2.status_code == 403

    # 3. POST /api/merchant/requests/req_dummy/deny
    r3 = client.post("/api/merchant/requests/req_dummy/deny", headers=agent_headers)
    assert r3.status_code == 403

    # 4. GET /api/merchant/audit
    r4 = client.get("/api/merchant/audit", headers=agent_headers)
    assert r4.status_code == 403

def test_twenty_dollar_escalate_then_approve_executes_once(
    client, agent_headers, merchant_headers, fake_adapter
):
    # Step 1: Agent creates $20.00 refund request -> ESCALATE
    create_res = client.post(
        "/api/agent/refund-requests",
        json={"capture_id": "CAP_A", "amount": "20.00", "currency": "USD", "action": "refund"},
        headers=agent_headers,
    )
    assert create_res.status_code == 200
    req_data = create_res.json()
    assert req_data["decision"] == "ESCALATE"
    assert req_data["approval_required"] is True
    assert fake_adapter.money_call_count == 0
    req_id = req_data["request_id"]

    # Step 2: Merchant sees it in GET /api/merchant/requests
    list_res = client.get("/api/merchant/requests", headers=merchant_headers)
    assert list_res.status_code == 200
    items = list_res.json()
    assert any(item["request_id"] == req_id for item in items)

    # Step 3: Merchant approves the request
    approve_res = client.post(
        f"/api/merchant/requests/{req_id}/approve",
        headers=merchant_headers,
        json={"reason": "Customer called support and verified receipt"},
    )
    assert approve_res.status_code == 200, approve_res.text
    approve_data = approve_res.json()

    assert approve_data["decision"] == "ALLOW"
    assert approve_data["execution_status"] == "COMPLETED"
    assert approve_data["paypal_refund_id"] is not None
    assert approve_data["adapter_call_count"] == 1
    assert fake_adapter.money_call_count == 1

    # Step 4: Re-approving must fail (replayed nonce) and NOT move money again
    re_approve = client.post(
        f"/api/merchant/requests/{req_id}/approve",
        headers=merchant_headers,
    )
    assert re_approve.status_code in (400, 409)
    assert fake_adapter.money_call_count == 1

def test_merchant_denial_writes_audit_and_prevents_adapter_call(
    client, agent_headers, merchant_headers, fake_adapter
):
    # Agent creates $15 refund request -> ESCALATE
    create_res = client.post(
        "/api/agent/refund-requests",
        json={"capture_id": "CAP_A", "amount": "15.00", "currency": "USD", "action": "refund"},
        headers=agent_headers,
    )
    req_id = create_res.json()["request_id"]

    # Merchant denies
    deny_res = client.post(
        f"/api/merchant/requests/{req_id}/deny",
        headers=merchant_headers,
        json={"reason": "Suspected friendly fraud"},
    )
    assert deny_res.status_code == 200
    deny_data = deny_res.json()
    assert deny_data["decision"] == "DENIED"
    assert fake_adapter.money_call_count == 0

    # Merchant audit log should contain the DENIAL event
    audit_res = client.get("/api/merchant/audit", headers=merchant_headers)
    assert audit_res.status_code == 200
    audit_data = audit_res.json()
    assert any(e["event_type"] == "DENIAL" for e in audit_data["events"])

def test_cross_merchant_access_denied(client, test_db, agent_headers, merchant_headers):
    # Create request under merchant 1
    create_res = client.post(
        "/api/agent/refund-requests",
        json={"capture_id": "CAP_A", "amount": "20.00", "currency": "USD", "action": "refund"},
        headers=agent_headers,
    )
    req_id = create_res.json()["request_id"]

    # Seed Merchant 2 and User 2 in DB
    conn = test_db.get_connection()
    conn.execute(
        "INSERT INTO merchants (id, name, created_at) VALUES ('merch_2', 'Beta Corp', '2026-11-09T00:00:00Z')"
    )
    from backend.auth import hash_password
    pw_hash = hash_password("judge2pass")
    conn.execute(
        """
        INSERT INTO merchant_users (id, merchant_id, username, password_hash, role, created_at)
        VALUES ('user_judge_2', 'merch_2', 'judge_merchant_2', ?, 'judge', '2026-11-09T00:00:00Z')
        """,
        (pw_hash,),
    )
    conn.commit()

    # Login as merchant 2
    login_res = client.post(
        "/api/merchant/login",
        json={"username": "judge_merchant_2", "password": "judge2pass"},
    )
    m2_token = login_res.json()["session_token"]
    m2_headers = {"Authorization": f"Bearer {m2_token}"}

    # Merchant 2 cannot see Merchant 1's request in list
    list_res = client.get("/api/merchant/requests", headers=m2_headers)
    assert list_res.status_code == 200
    assert not any(r["request_id"] == req_id for r in list_res.json())

    # Merchant 2 cannot approve Merchant 1's request -> 404 or 403
    approve_res = client.post(f"/api/merchant/requests/{req_id}/approve", headers=m2_headers)
    assert approve_res.status_code in (403, 404)

def test_tampered_payload_rejected_via_api_with_zero_adapter_calls(
    client, test_db, agent_headers, merchant_headers, fake_adapter
):
    # 1. Tampered amount (e.g. 24 instead of 20)
    create_res = client.post(
        "/api/agent/refund-requests",
        json={"capture_id": "CAP_A", "amount": "20.00", "currency": "USD", "action": "refund"},
        headers=agent_headers,
    )
    req_id_1 = create_res.json()["request_id"]
    conn = test_db.get_connection()
    conn.execute("UPDATE action_requests SET amount = '24.00' WHERE id = ?", (req_id_1,))
    conn.commit()

    initial_calls = fake_adapter.money_call_count
    approve_tamper_amt = client.post(f"/api/merchant/requests/{req_id_1}/approve", headers=merchant_headers)
    assert approve_tamper_amt.status_code == 400
    assert fake_adapter.money_call_count == initial_calls

    # 2. Tampered capture_id
    create_res_2 = client.post(
        "/api/agent/refund-requests",
        json={"capture_id": "CAP_A", "amount": "20.00", "currency": "USD", "action": "refund"},
        headers=agent_headers,
    )
    req_id_2 = create_res_2.json()["request_id"]
    conn.execute("UPDATE action_requests SET capture_id = 'CAP_B' WHERE id = ?", (req_id_2,))
    conn.commit()

    approve_tamper_cap = client.post(f"/api/merchant/requests/{req_id_2}/approve", headers=merchant_headers)
    assert approve_tamper_cap.status_code == 400
    assert fake_adapter.money_call_count == initial_calls

    # 3. Tampered policy_version
    create_res_3 = client.post(
        "/api/agent/refund-requests",
        json={"capture_id": "CAP_A", "amount": "20.00", "currency": "USD", "action": "refund"},
        headers=agent_headers,
    )
    req_id_3 = create_res_3.json()["request_id"]
    conn.execute("UPDATE action_requests SET policy_version = 'v2' WHERE id = ?", (req_id_3,))
    conn.commit()

    approve_tamper_pol = client.post(f"/api/merchant/requests/{req_id_3}/approve", headers=merchant_headers)
    assert approve_tamper_pol.status_code == 400
    assert fake_adapter.money_call_count == initial_calls

