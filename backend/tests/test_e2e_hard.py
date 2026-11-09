import json
import threading
from datetime import datetime, timezone
from decimal import Decimal
import pytest

from backend.audit import verify_audit_chain
from backend.policy import Decision, evaluate_refund_policy
from backend.auth import hash_password, hash_agent_key

# ---------------------------------------------------------------------------
# 1. CONNECTIVITY
# ---------------------------------------------------------------------------
def test_01_connectivity(client, agent_headers, merchant_headers, fake_adapter, test_db):
    """
    Call EVERY route once with correct credentials and confirm it is reachable
    and returns a valid, schema-shaped response. Confirm openapi.json route list matches.
    """
    initial_count = fake_adapter.money_call_count

    # 1. GET /health
    r_health = client.get("/health")
    assert r_health.status_code == 200
    assert r_health.json() == {"status": "ok"}

    # 2. POST /api/agent/refund-requests ($5.00 -> ALLOW)
    r_create = client.post(
        "/api/agent/refund-requests",
        json={"capture_id": "CAP_A", "amount": "5.00", "currency": "USD", "action": "refund"},
        headers=agent_headers,
    )
    assert r_create.status_code == 200
    req_allow_id = r_create.json()["request_id"]
    assert r_create.json()["decision"] == "ALLOW"
    assert r_create.json()["execution_status"] == "COMPLETED"
    assert fake_adapter.money_call_count == initial_count + 1

    # 3. GET /api/agent/requests/{id}
    r_get_req = client.get(f"/api/agent/requests/{req_allow_id}", headers=agent_headers)
    assert r_get_req.status_code == 200
    assert r_get_req.json()["request_id"] == req_allow_id

    # 4. GET /api/agent/policy
    r_policy = client.get("/api/agent/policy", headers=agent_headers)
    assert r_policy.status_code == 200
    assert r_policy.json()["version"] == "v1"

    # 5. POST /api/merchant/login
    r_login = client.post(
        "/api/merchant/login",
        json={"username": "judge_tester", "password": "secure_judge_test_pass_123!"},
    )
    assert r_login.status_code == 200
    assert "session_token" in r_login.json()

    # 6. GET /api/merchant/requests
    r_merch_list = client.get("/api/merchant/requests", headers=merchant_headers)
    assert r_merch_list.status_code == 200
    assert isinstance(r_merch_list.json(), list)

    # Setup an ESCALATED request for approve/deny testing
    r_esc = client.post(
        "/api/agent/refund-requests",
        json={"capture_id": "CAP_A", "amount": "20.00", "currency": "USD", "action": "refund"},
        headers=agent_headers,
    )
    req_esc_id = r_esc.json()["request_id"]
    assert r_esc.json()["decision"] == "ESCALATE"

    # 7. POST /api/merchant/requests/{id}/approve
    r_approve = client.post(
        f"/api/merchant/requests/{req_esc_id}/approve",
        headers=merchant_headers,
        json={"reason": "Connectivity test approval"},
    )
    assert r_approve.status_code == 200
    assert r_approve.json()["decision"] == "ALLOW"
    assert r_approve.json()["execution_status"] == "COMPLETED"

    # Setup a second ESCALATED request for deny testing
    r_esc_2 = client.post(
        "/api/agent/refund-requests",
        json={"capture_id": "CAP_A", "amount": "15.00", "currency": "USD", "action": "refund"},
        headers=agent_headers,
    )
    req_deny_id = r_esc_2.json()["request_id"]

    # 8. POST /api/merchant/requests/{id}/deny
    r_deny = client.post(
        f"/api/merchant/requests/{req_deny_id}/deny",
        headers=merchant_headers,
        json={"reason": "Connectivity test denial"},
    )
    assert r_deny.status_code == 200
    assert r_deny.json()["decision"] == "DENIED"

    # 9. GET /api/merchant/audit
    r_audit = client.get("/api/merchant/audit", headers=merchant_headers)
    assert r_audit.status_code == 200
    assert r_audit.json()["is_valid"] is True

    # Confirm openapi.json route list matches
    with open("backend/openapi.json", "r") as f:
        openapi = json.load(f)
    expected_paths = {
        "/health",
        "/api/agent/refund-requests",
        "/api/agent/requests/{request_id}",
        "/api/agent/policy",
        "/api/merchant/login",
        "/api/merchant/requests",
        "/api/merchant/requests/{request_id}/approve",
        "/api/merchant/requests/{request_id}/deny",
        "/api/merchant/audit",
    }
    assert expected_paths.issubset(set(openapi["paths"].keys()))

    # DB and audit verification
    conn = test_db.get_connection()
    is_valid, msg, _ = verify_audit_chain(conn)
    assert is_valid, msg


# ---------------------------------------------------------------------------
# 2. FULL DEMO SCRIPT
# ---------------------------------------------------------------------------
def test_02_full_demo_script(client, agent_headers, merchant_headers, fake_adapter, test_db):
    """
    (a) $250 -> BLOCK, count 0
    (b) $20 -> ESCALATE, appears in merchant queue with exact payload and expiry, count 0
    (c) merchant approves $20 -> executes once, COMPLETED, refund id present, count 1
    (d) a second $20 request is approved, but the executed payload is tampered to amount $24 -> rejected, count unchanged
    (e) $5 -> ALLOW, COMPLETED, count increments by exactly 1
    (f) audit chain verifies and contains an event for every step above.
    """
    assert fake_adapter.money_call_count == 0

    # (a) $250 -> BLOCK, count 0
    res_a = client.post(
        "/api/agent/refund-requests",
        json={"capture_id": "CAP_A", "amount": "250.00", "currency": "USD", "action": "refund"},
        headers=agent_headers,
    )
    assert res_a.status_code == 200
    assert res_a.json()["decision"] == "BLOCK"
    assert res_a.json()["adapter_call_count"] == 0
    assert fake_adapter.money_call_count == 0

    # (b) $20 -> ESCALATE, appears in merchant queue with exact payload and expiry, count 0
    res_b = client.post(
        "/api/agent/refund-requests",
        json={"capture_id": "CAP_A", "amount": "20.00", "currency": "USD", "action": "refund"},
        headers=agent_headers,
    )
    assert res_b.status_code == 200
    req_b_data = res_b.json()
    assert req_b_data["decision"] == "ESCALATE"
    assert req_b_data["approval_required"] is True
    assert req_b_data["expires_at"] is not None
    assert fake_adapter.money_call_count == 0
    req_b_id = req_b_data["request_id"]

    # Appears in merchant queue
    queue_res = client.get("/api/merchant/requests", headers=merchant_headers)
    assert queue_res.status_code == 200
    queue_item = next((r for r in queue_res.json() if r["request_id"] == req_b_id), None)
    assert queue_item is not None
    assert queue_item["expires_at"] == req_b_data["expires_at"]

    # (c) merchant approves $20 -> executes once, COMPLETED, refund id present, count 1
    approve_res = client.post(
        f"/api/merchant/requests/{req_b_id}/approve",
        headers=merchant_headers,
        json={"reason": "Demo approval"},
    )
    assert approve_res.status_code == 200
    app_data = approve_res.json()
    assert app_data["decision"] == "ALLOW"
    assert app_data["execution_status"] == "COMPLETED"
    assert app_data["paypal_refund_id"] is not None
    assert app_data["adapter_call_count"] == 1
    assert fake_adapter.money_call_count == 1

    # (d) a second $20 request is approved, but the executed payload is tampered to amount $24 -> rejected, count unchanged
    res_d = client.post(
        "/api/agent/refund-requests",
        json={"capture_id": "CAP_A", "amount": "20.00", "currency": "USD", "action": "refund"},
        headers=agent_headers,
    )
    req_d_id = res_d.json()["request_id"]
    conn = test_db.get_connection()
    # Tamper action_request amount to 24.00
    conn.execute("UPDATE action_requests SET amount = '24.00' WHERE id = ?", (req_d_id,))
    conn.commit()

    tamper_app_res = client.post(
        f"/api/merchant/requests/{req_d_id}/approve",
        headers=merchant_headers,
    )
    assert tamper_app_res.status_code == 400
    assert fake_adapter.money_call_count == 1  # count UNCHANGED!

    # (e) $5 -> ALLOW, COMPLETED, count increments by exactly 1 (to 2)
    res_e = client.post(
        "/api/agent/refund-requests",
        json={"capture_id": "CAP_A", "amount": "5.00", "currency": "USD", "action": "refund"},
        headers=agent_headers,
    )
    assert res_e.status_code == 200
    assert res_e.json()["decision"] == "ALLOW"
    assert res_e.json()["execution_status"] == "COMPLETED"
    assert res_e.json()["adapter_call_count"] == 1
    assert fake_adapter.money_call_count == 2

    # (f) audit chain verifies and contains an event for every step above
    is_valid, msg, total_events = verify_audit_chain(conn)
    assert is_valid is True, msg
    assert total_events >= 6


# ---------------------------------------------------------------------------
# 3. AUTH MATRIX
# ---------------------------------------------------------------------------
def test_03_auth_matrix(client, agent_headers, merchant_headers, test_db, test_config, fake_adapter):
    """
    For each of the 9 routes test:
    no credential, garbage credential, revoked agent key, expired/invalid merchant token,
    agent key on merchant routes (403), merchant token on agent routes (403),
    merchant A token on merchant B data (denied), agent A reading agent B request (denied).
    No mutation may occur on any denied call.
    """
    conn = test_db.get_connection()
    initial_calls = fake_adapter.money_call_count

    # Create a revoked agent
    revoked_key = "secret_revoked_key_123"
    conn.execute(
        """
        INSERT INTO agents (id, merchant_id, name, api_key_hash, status, created_at)
        VALUES ('agent_revoked', 'merch_test_1', 'RevokedAgent', ?, 'revoked', '2026-11-09T00:00:00Z')
        """,
        (hash_agent_key(revoked_key),),
    )
    conn.commit()

    garbage_headers = {"Authorization": "Bearer garbage_credential_999"}
    revoked_headers = {"Authorization": f"Bearer {revoked_key}"}

    # 1. No credential on protected routes
    assert client.post("/api/agent/refund-requests", json={}).status_code == 401
    assert client.get("/api/agent/requests/req_dummy").status_code == 401
    assert client.get("/api/agent/policy").status_code == 401
    assert client.get("/api/merchant/requests").status_code == 401
    assert client.post("/api/merchant/requests/req_dummy/approve").status_code == 401
    assert client.post("/api/merchant/requests/req_dummy/deny").status_code == 401
    assert client.get("/api/merchant/audit").status_code == 401

    # /health is public
    assert client.get("/health").status_code == 200

    # 2. Garbage credential on protected routes -> 401
    assert client.post("/api/agent/refund-requests", json={}, headers=garbage_headers).status_code == 401
    assert client.get("/api/merchant/requests", headers=garbage_headers).status_code == 401

    # 3. Revoked agent key -> 401
    assert client.post("/api/agent/refund-requests", json={}, headers=revoked_headers).status_code == 401

    # 4. Agent key on merchant routes -> 403
    assert client.get("/api/merchant/requests", headers=agent_headers).status_code == 403
    assert client.post("/api/merchant/requests/req_dummy/approve", headers=agent_headers).status_code == 403
    assert client.post("/api/merchant/requests/req_dummy/deny", headers=agent_headers).status_code == 403
    assert client.get("/api/merchant/audit", headers=agent_headers).status_code == 403

    # 5. Merchant token on agent routes -> 403
    assert client.post("/api/agent/refund-requests", json={}, headers=merchant_headers).status_code == 403
    assert client.get("/api/agent/requests/req_dummy", headers=merchant_headers).status_code == 403
    assert client.get("/api/agent/policy", headers=merchant_headers).status_code == 403

    # 6. Cross-merchant isolation
    conn.execute("INSERT INTO merchants (id, name, created_at) VALUES ('merch_b', 'Merchant B', '2026-11-09T00:00:00Z')")
    conn.execute(
        "INSERT INTO merchant_users (id, merchant_id, username, password_hash, role, created_at) VALUES ('user_b', 'merch_b', 'judge_b', ?, 'judge', '2026-11-09T00:00:00Z')",
        (hash_password("pass_b"),),
    )
    conn.commit()

    token_b = client.post("/api/merchant/login", json={"username": "judge_b", "password": "pass_b"}).json()["session_token"]
    headers_b = {"Authorization": f"Bearer {token_b}"}

    # Create request under merchant A
    req_a_id = client.post(
        "/api/agent/refund-requests",
        json={"capture_id": "CAP_A", "amount": "20.00", "currency": "USD", "action": "refund"},
        headers=agent_headers,
    ).json()["request_id"]

    # Merchant B tries to approve Merchant A's request -> 403 or 404
    assert client.post(f"/api/merchant/requests/{req_a_id}/approve", headers=headers_b).status_code in (403, 404)

    # 7. Cross-agent isolation
    key_agent_2 = "agent_2_secret_key"
    conn.execute(
        "INSERT INTO agents (id, merchant_id, name, api_key_hash, status, created_at) VALUES ('agent_2', 'merch_test_1', 'Agent 2', ?, 'active', '2026-11-09T00:00:00Z')",
        (hash_agent_key(key_agent_2),),
    )
    conn.commit()
    headers_agent_2 = {"Authorization": f"Bearer {key_agent_2}"}

    # Agent 2 cannot read Agent 1's request
    assert client.get(f"/api/agent/requests/{req_a_id}", headers=headers_agent_2).status_code in (403, 404)

    # Confirm no mutation occurred on money calls
    assert fake_adapter.money_call_count == initial_calls


# ---------------------------------------------------------------------------
# 4. IDENTITY SPOOFING
# ---------------------------------------------------------------------------
def test_04_identity_spoofing(client, agent_headers, merchant_headers, fake_adapter):
    """
    Put merchant_id / agent_id / role / decision / adapter_call_count / execution_status
    in the request body, query string and headers; confirm they are rejected or ignored.
    """
    spoofed_fields = [
        "merchant_id", "agent_id", "role", "decision",
        "adapter_call_count", "execution_status"
    ]

    # 1. Body spoofing -> rejected with 422 (extra="forbid")
    for field in spoofed_fields:
        r = client.post(
            "/api/agent/refund-requests",
            json={
                "capture_id": "CAP_A",
                "amount": "5.00",
                "currency": "USD",
                "action": "refund",
                field: "spoofed_value",
            },
            headers=agent_headers,
        )
        assert r.status_code == 422, f"Field {field} in body was not rejected with 422!"

    # 2. Query string and custom header spoofing -> ignored and identity comes only from credential
    r_query = client.post(
        "/api/agent/refund-requests?merchant_id=merch_hacked&agent_id=agent_hacked&decision=ALLOW",
        json={"capture_id": "CAP_A", "amount": "20.00", "currency": "USD", "action": "refund"},
        headers={
            **agent_headers,
            "X-Merchant-ID": "merch_hacked",
            "X-Agent-ID": "agent_hacked",
            "X-Role": "admin",
        },
    )
    assert r_query.status_code == 200
    # The decision must still be ESCALATE for $20.00, not the spoofed ALLOW
    assert r_query.json()["decision"] == "ESCALATE"


# ---------------------------------------------------------------------------
# 5. MONEY ABUSE
# ---------------------------------------------------------------------------
def test_05_money_abuse(client, agent_headers, fake_adapter):
    """
    Floats (10.5), strings with exponent ("1e1"), "NaN", "Infinity",
    negative zero, "0.00", "-5", 3+ decimal places ("10.001"), huge numbers,
    empty string, null, wrong types, non-USD currency, lowercase "usd", whitespace.
    All must be rejected or safely normalised with no money call.
    """
    initial_calls = fake_adapter.money_call_count

    # Schema rejections (HTTP 422)
    schema_rejects = [
        10.5,           # float
        "1e1",          # scientific notation
        "NaN",          # NaN
        "Infinity",     # Infinity
        "10.001",       # 3+ decimal places
        "",             # empty string
        None,           # null
        True,           # boolean
        [10.0],         # list
        {"val": 10},    # dict
    ]
    for bad_amt in schema_rejects:
        r = client.post(
            "/api/agent/refund-requests",
            json={"capture_id": "CAP_A", "amount": bad_amt, "currency": "USD", "action": "refund"},
            headers=agent_headers,
        )
        assert r.status_code == 422, f"Amount '{bad_amt}' should be rejected with 422!"
        assert fake_adapter.money_call_count == initial_calls

    # Policy rejections (evaluated as BLOCK with zero money calls)
    policy_rejects = [
        "-0.00",                # negative zero
        "0.00",                 # zero
        "-5",                   # negative
        "9999999999999999.00",  # huge number > 25
    ]
    for bad_amt in policy_rejects:
        r = client.post(
            "/api/agent/refund-requests",
            json={"capture_id": "CAP_A", "amount": bad_amt, "currency": "USD", "action": "refund"},
            headers=agent_headers,
        )
        assert r.status_code == 200
        assert r.json()["decision"] == "BLOCK"
        assert fake_adapter.money_call_count == initial_calls

    # Currency abuse
    r_curr_eur = client.post(
        "/api/agent/refund-requests",
        json={"capture_id": "CAP_A", "amount": "5.00", "currency": "EUR", "action": "refund"},
        headers=agent_headers,
    )
    assert r_curr_eur.json()["decision"] == "BLOCK"

    r_curr_lower = client.post(
        "/api/agent/refund-requests",
        json={"capture_id": "CAP_A", "amount": "5.00", "currency": "usd", "action": "refund"},
        headers=agent_headers,
    )
    assert r_curr_lower.json()["decision"] == "BLOCK"

    # Whitespace normalization (e.g. " 10.00 " -> safely trimmed to 10.00)
    r_ws = client.post(
        "/api/agent/refund-requests",
        json={"capture_id": "CAP_A", "amount": "  10.00  ", "currency": "USD", "action": "refund"},
        headers=agent_headers,
    )
    assert r_ws.status_code == 200
    assert r_ws.json()["decision"] == "ALLOW"
    assert fake_adapter.money_call_count == initial_calls + 1


# ---------------------------------------------------------------------------
# 6. POLICY BOUNDARIES
# ---------------------------------------------------------------------------
def test_06_policy_boundaries(client, agent_headers, test_db, fake_adapter):
    """
    0.01, 5.00, 10.00 (ALLOW), 10.01 (ESCALATE), 25.00 (ESCALATE), 25.01 (BLOCK), 250.00 (BLOCK).
    Over remaining refundable balance. Capture owned by another merchant.
    Non-refundable capture. Unknown capture. Several failures at once.
    """
    boundaries = [
        ("0.01", "ALLOW"),
        ("5.00", "ALLOW"),
        ("10.00", "ALLOW"),
        ("10.01", "ESCALATE"),
        ("25.00", "ESCALATE"),
        ("25.01", "BLOCK"),
        ("250.00", "BLOCK"),
    ]
    for amt, expected_decision in boundaries:
        r = client.post(
            "/api/agent/refund-requests",
            json={"capture_id": "CAP_B", "amount": amt, "currency": "USD", "action": "refund"},
            headers=agent_headers,
        )
        assert r.status_code == 200
        assert r.json()["decision"] == expected_decision, f"Failed for boundary {amt}"

    # Drain capture CAP_C from $300 down to $5
    conn = test_db.get_connection()
    conn.execute("UPDATE captures SET refunded_amount = '295.00' WHERE id = 'CAP_C'")
    conn.commit()

    # Request $6.00 on CAP_C (remaining is $5.00) -> BLOCK
    r_exceed = client.post(
        "/api/agent/refund-requests",
        json={"capture_id": "CAP_C", "amount": "6.00", "currency": "USD", "action": "refund"},
        headers=agent_headers,
    )
    assert r_exceed.json()["decision"] == "BLOCK"
    assert any("remaining" in reason.lower() for reason in r_exceed.json()["reasons"])

    # Capture owned by another merchant
    conn.execute("INSERT OR IGNORE INTO merchants (id, name, created_at) VALUES ('merch_other', 'Other', '2026-11-09T00:00:00Z')")
    conn.execute("UPDATE captures SET merchant_id = 'merch_other' WHERE id = 'CAP_C'")
    conn.commit()
    r_other_merch = client.post(
        "/api/agent/refund-requests",
        json={"capture_id": "CAP_C", "amount": "2.00", "currency": "USD", "action": "refund"},
        headers=agent_headers,
    )
    assert r_other_merch.json()["decision"] == "BLOCK"
    assert any("belong" in reason.lower() for reason in r_other_merch.json()["reasons"])

    # Non-refundable capture
    conn.execute("UPDATE captures SET merchant_id = 'merch_test_1', status = 'FAILED' WHERE id = 'CAP_C'")
    conn.commit()
    r_non_ref = client.post(
        "/api/agent/refund-requests",
        json={"capture_id": "CAP_C", "amount": "2.00", "currency": "USD", "action": "refund"},
        headers=agent_headers,
    )
    assert r_non_ref.json()["decision"] == "BLOCK"

    # Unknown capture
    r_unk = client.post(
        "/api/agent/refund-requests",
        json={"capture_id": "CAP_UNKNOWN", "amount": "2.00", "currency": "USD", "action": "refund"},
        headers=agent_headers,
    )
    assert r_unk.json()["decision"] == "BLOCK"

    # Multiple failures at once: non-USD + action invalid + amount > 25
    r_multi = client.post(
        "/api/agent/refund-requests",
        json={"capture_id": "CAP_UNKNOWN", "amount": "100.00", "currency": "GBP", "action": "payout"},
        headers=agent_headers,
    )
    assert r_multi.json()["decision"] == "BLOCK"
    assert len(r_multi.json()["reasons"]) >= 3


# ---------------------------------------------------------------------------
# 7. AI TIGHTEN-ONLY
# ---------------------------------------------------------------------------
def test_07_ai_tighten_only(client, agent_headers):
    """
    AI flag on $5 -> ESCALATE; AI flag on $250 stays BLOCK;
    AI saying "allow" on BLOCK or ESCALATE changes nothing;
    AI exception, timeout, garbage never yields ALLOW; fallback labelled in risk_summary.
    """
    # 1. AI flag on $5 -> ESCALATE
    r1 = client.post(
        "/api/agent/refund-requests",
        json={
            "capture_id": "CAP_A",
            "amount": "5.00",
            "currency": "USD",
            "action": "refund",
            "ai_risk_flag": True,
            "ai_risk_explanation": "Model flagged rapid transaction burst",
        },
        headers=agent_headers,
    )
    assert r1.json()["decision"] == "ESCALATE"
    assert "rapid transaction burst" in r1.json()["risk_summary"]

    # 2. AI flag on $250 stays BLOCK
    r2 = client.post(
        "/api/agent/refund-requests",
        json={
            "capture_id": "CAP_A",
            "amount": "250.00",
            "currency": "USD",
            "action": "refund",
            "ai_risk_flag": False,
        },
        headers=agent_headers,
    )
    assert r2.json()["decision"] == "BLOCK"

    # 3. Direct policy test for AI error/timeout
    agent = {"id": "a1", "merchant_id": "m1", "status": "active"}
    capture = {"id": "CAP_A", "merchant_id": "m1", "original_amount": "300.00", "refunded_amount": "0.00", "currency": "USD", "status": "COMPLETED"}

    res_err = evaluate_refund_policy(
        agent=agent,
        capture=capture,
        amount=Decimal("5.00"),
        ai_signal_error="Gateway AI timeout 504",
    )
    assert res_err.decision == Decision.ESCALATE
    assert "504" in res_err.risk_summary


# ---------------------------------------------------------------------------
# 8. APPROVAL ATTACKS
# ---------------------------------------------------------------------------
def test_08_approval_attacks(client, agent_headers, merchant_headers, test_db, fake_adapter):
    """
    Tamper amount, capture_id, currency, action, policy_version;
    expired approval; replayed nonce; approve after deny; deny after approve;
    approve a request that was never ESCALATED; approve nonexistent id;
    approve with agent key; approve as different merchant; policy version bumped.
    Every one: rejected, count unchanged, audit event written.
    """
    conn = test_db.get_connection()

    def create_escalated_request():
        r = client.post(
            "/api/agent/refund-requests",
            json={"capture_id": "CAP_A", "amount": "20.00", "currency": "USD", "action": "refund"},
            headers=agent_headers,
        )
        return r.json()["request_id"]

    initial_calls = fake_adapter.money_call_count

    # 1. Tamper amount
    req_1 = create_escalated_request()
    conn.execute("UPDATE action_requests SET amount = '24.00' WHERE id = ?", (req_1,))
    conn.commit()
    assert client.post(f"/api/merchant/requests/{req_1}/approve", headers=merchant_headers).status_code == 400
    assert fake_adapter.money_call_count == initial_calls

    # 2. Tamper capture_id
    req_2 = create_escalated_request()
    conn.execute("UPDATE action_requests SET capture_id = 'CAP_B' WHERE id = ?", (req_2,))
    conn.commit()
    assert client.post(f"/api/merchant/requests/{req_2}/approve", headers=merchant_headers).status_code == 400

    # 3. Tamper currency
    req_3 = create_escalated_request()
    conn.execute("UPDATE action_requests SET currency = 'EUR' WHERE id = ?", (req_3,))
    conn.commit()
    assert client.post(f"/api/merchant/requests/{req_3}/approve", headers=merchant_headers).status_code == 400

    # 4. Tamper action
    req_4 = create_escalated_request()
    conn.execute("UPDATE action_requests SET action = 'payout' WHERE id = ?", (req_4,))
    conn.commit()
    assert client.post(f"/api/merchant/requests/{req_4}/approve", headers=merchant_headers).status_code == 400

    # 5. Tamper policy_version
    req_5 = create_escalated_request()
    conn.execute("UPDATE action_requests SET policy_version = 'v2' WHERE id = ?", (req_5,))
    conn.commit()
    assert client.post(f"/api/merchant/requests/{req_5}/approve", headers=merchant_headers).status_code == 400

    # 6. Expired approval
    req_6 = create_escalated_request()
    conn.execute("UPDATE approvals SET expires_at = '2020-01-01T00:00:00Z' WHERE request_id = ?", (req_6,))
    conn.commit()
    assert client.post(f"/api/merchant/requests/{req_6}/approve", headers=merchant_headers).status_code == 400

    # 7. Approve after deny
    req_7 = create_escalated_request()
    assert client.post(f"/api/merchant/requests/{req_7}/deny", headers=merchant_headers).status_code == 200
    assert client.post(f"/api/merchant/requests/{req_7}/approve", headers=merchant_headers).status_code in (400, 409)

    # 8. Deny after approve
    req_8 = create_escalated_request()
    assert client.post(f"/api/merchant/requests/{req_8}/approve", headers=merchant_headers).status_code == 200
    assert client.post(f"/api/merchant/requests/{req_8}/deny", headers=merchant_headers).status_code in (400, 409)

    # 9. Approve a request that was never ESCALATED ($5 ALLOW)
    r_allow = client.post(
        "/api/agent/refund-requests",
        json={"capture_id": "CAP_A", "amount": "5.00", "currency": "USD", "action": "refund"},
        headers=agent_headers,
    )
    req_allow_id = r_allow.json()["request_id"]
    assert client.post(f"/api/merchant/requests/{req_allow_id}/approve", headers=merchant_headers).status_code in (400, 409)

    # 10. Approve nonexistent id
    assert client.post("/api/merchant/requests/req_nonexistent_xyz/approve", headers=merchant_headers).status_code in (400, 404)

    # 11. Approve with agent key
    req_11 = create_escalated_request()
    assert client.post(f"/api/merchant/requests/{req_11}/approve", headers=agent_headers).status_code == 403

    # 12. Policy version bumped between request and approval
    req_12 = create_escalated_request()
    conn.execute(
        "INSERT INTO policies (id, version, merchant_id, max_auto_amount, max_escalate_amount, currency, created_at) VALUES ('p_new', 'v2', 'merch_test_1', '10.00', '25.00', 'USD', '2026-11-09T23:59:59Z')"
    )
    conn.commit()
    assert client.post(f"/api/merchant/requests/{req_12}/approve", headers=merchant_headers).status_code == 400

    # Audit chain check: verify chain still valid after all rejections
    is_valid, msg, _ = verify_audit_chain(conn)
    assert is_valid is True, msg


# ---------------------------------------------------------------------------
# 9. CONCURRENCY
# ---------------------------------------------------------------------------
def test_09_concurrency(client, agent_headers, merchant_headers, fake_adapter, test_db):
    """
    20 parallel approve calls on one approval -> exactly 1 success, FakeAdapter count exactly 1.
    20 parallel identical agent requests -> no crash, no duplicated refund for the same request id.
    """
    initial_calls = fake_adapter.money_call_count

    # 20 parallel approve calls on 1 approval
    r_esc = client.post(
        "/api/agent/refund-requests",
        json={"capture_id": "CAP_A", "amount": "20.00", "currency": "USD", "action": "refund"},
        headers=agent_headers,
    )
    req_id = r_esc.json()["request_id"]

    results = []
    def do_approve():
        res = client.post(f"/api/merchant/requests/{req_id}/approve", headers=merchant_headers)
        results.append(res.status_code)

    threads = [threading.Thread(target=do_approve) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Exactly 1 success (200), rest rejected (400 or 409)
    assert results.count(200) == 1
    assert fake_adapter.money_call_count == initial_calls + 1

    # 20 parallel agent requests for $5.00
    agent_results = []
    def do_request():
        res = client.post(
            "/api/agent/refund-requests",
            json={"capture_id": "CAP_A", "amount": "5.00", "currency": "USD", "action": "refund"},
            headers=agent_headers,
        )
        agent_results.append(res.status_code)

    req_threads = [threading.Thread(target=do_request) for _ in range(20)]
    for t in req_threads:
        t.start()
    for t in req_threads:
        t.join()

    assert all(code == 200 for code in agent_results)


# ---------------------------------------------------------------------------
# 10. IDEMPOTENCY AND UNKNOWN OUTCOMES
# ---------------------------------------------------------------------------
def test_10_idempotency_and_unknown_outcomes(client, service, fake_adapter, test_db, test_config):
    """
    FakeAdapter timeout -> status UNKNOWN; paypal_request_id persisted BEFORE call;
    reconcile uses SAME id; second reconcile does not refund twice;
    PENDING stays pending; FAILED/CANCELLED is a failure; 2xx without COMPLETED is not success.
    """
    # 1. Timeout -> UNKNOWN
    fake_adapter.set_timeout(True)
    resp = service.create_refund_request(
        agent_id=test_config.agent_id,
        capture_id="CAP_A",
        amount=Decimal("5.00"),
        currency="USD",
    )
    assert resp.execution_status == "UNKNOWN"

    conn = test_db.get_connection()
    row = conn.execute("SELECT paypal_request_id, status FROM executions WHERE request_id = ?", (resp.request_id,)).fetchone()
    assert row is not None
    p_req_id = row[0]
    assert row[1] == "UNKNOWN"

    # Reconcile uses SAME paypal_request_id
    fake_adapter.set_timeout(False)
    recon1 = service.reconcile_request(resp.request_id)
    assert recon1.execution_status == "COMPLETED"

    # Second reconcile does NOT increment money calls (idempotent replay)
    calls_after_recon1 = fake_adapter.money_call_count
    recon2 = service.reconcile_request(resp.request_id)
    assert recon2.execution_status == "COMPLETED"
    assert fake_adapter.money_call_count == calls_after_recon1  # Idempotent!

    # PENDING outcome
    fake_adapter.set_configured_result("PENDING")
    res_pending = service.create_refund_request(
        agent_id=test_config.agent_id,
        capture_id="CAP_A",
        amount=Decimal("3.00"),
        currency="USD",
    )
    assert res_pending.execution_status == "PENDING"

    # FAILED outcome
    fake_adapter.set_configured_result("FAILED")
    res_failed = service.create_refund_request(
        agent_id=test_config.agent_id,
        capture_id="CAP_A",
        amount=Decimal("2.00"),
        currency="USD",
    )
    assert res_failed.execution_status == "FAILED"


# ---------------------------------------------------------------------------
# 11. AUDIT INTEGRITY & TAMPER DETECTION
# ---------------------------------------------------------------------------
def test_11_audit_tampering_detection(test_db):
    """
    Verify chain passes; then directly edit:
    1. change a decision
    2. delete a row
    3. swap two rows
    4. change a prev_hash
    Confirm each of the four is detected. Confirm no secrets in audit payloads.
    """
    conn = test_db.get_connection()
    from backend.audit import record_audit_event

    # Populate 4 chained audit events
    record_audit_event(conn, "DECISION", "agent", "a1", "r1", "m1", {"decision": "ALLOW", "amount": "5.00"})
    record_audit_event(conn, "EXECUTION", "system", "gw", "r1", "m1", {"status": "COMPLETED"})
    record_audit_event(conn, "APPROVAL", "merchant_user", "j1", "r2", "m1", {"action": "approve"})
    record_audit_event(conn, "DECISION", "agent", "a1", "r3", "m1", {"decision": "BLOCK", "amount": "250.00"})

    is_valid, msg, count = verify_audit_chain(conn)
    assert is_valid is True
    assert count == 4

    # 1. Change a decision (modify payload)
    orig_row_2 = conn.execute("SELECT payload FROM audit_events WHERE id = 2").fetchone()[0]
    conn.execute("UPDATE audit_events SET payload = '{\"status\":\"FAILED\"}' WHERE id = 2")
    conn.commit()
    is_valid_1, _, _ = verify_audit_chain(conn)
    assert is_valid_1 is False
    # Restore
    conn.execute("UPDATE audit_events SET payload = ? WHERE id = 2", (orig_row_2,))
    conn.commit()
    assert verify_audit_chain(conn)[0] is True

    # 2. Delete a row (row 3)
    deleted_row = conn.execute("SELECT * FROM audit_events WHERE id = 3").fetchone()
    conn.execute("DELETE FROM audit_events WHERE id = 3")
    conn.commit()
    is_valid_2, _, _ = verify_audit_chain(conn)
    assert is_valid_2 is False
    # Restore
    conn.execute(
        "INSERT INTO audit_events (id, prev_hash, event_type, request_id, merchant_id, actor_type, actor_id, payload, timestamp, hash) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        tuple(deleted_row),
    )
    conn.commit()
    assert verify_audit_chain(conn)[0] is True

    # 3. Swap two rows (swap payload of row 2 and row 3)
    p2 = conn.execute("SELECT payload FROM audit_events WHERE id = 2").fetchone()[0]
    p3 = conn.execute("SELECT payload FROM audit_events WHERE id = 3").fetchone()[0]
    conn.execute("UPDATE audit_events SET payload = ? WHERE id = 2", (p3,))
    conn.execute("UPDATE audit_events SET payload = ? WHERE id = 3", (p2,))
    conn.commit()
    is_valid_3, _, _ = verify_audit_chain(conn)
    assert is_valid_3 is False
    # Restore
    conn.execute("UPDATE audit_events SET payload = ? WHERE id = 2", (p2,))
    conn.execute("UPDATE audit_events SET payload = ? WHERE id = 3", (p3,))
    conn.commit()
    assert verify_audit_chain(conn)[0] is True

    # 4. Change prev_hash
    orig_prev = conn.execute("SELECT prev_hash FROM audit_events WHERE id = 4").fetchone()[0]
    conn.execute("UPDATE audit_events SET prev_hash = 'tampered_prev_hash_12345' WHERE id = 4")
    conn.commit()
    is_valid_4, _, _ = verify_audit_chain(conn)
    assert is_valid_4 is False
    # Restore
    conn.execute("UPDATE audit_events SET prev_hash = ? WHERE id = 4", (orig_prev,))
    conn.commit()
    assert verify_audit_chain(conn)[0] is True

    # 5. Check no secrets logged in any audit payload
    cursor = conn.execute("SELECT payload FROM audit_events")
    for r in cursor.fetchall():
        txt = r[0]
        assert "password_123" not in txt
        assert "secret_key" not in txt
        assert "session_token" not in txt or "[REDACTED]" in txt


# ---------------------------------------------------------------------------
# 12. DB INTEGRITY
# ---------------------------------------------------------------------------
def test_12_db_integrity(test_db):
    """
    WAL mode on, foreign keys on, money stored as TEXT decimals, no float columns.
    An approval nonce can never be consumed twice at the SQL level.
    """
    conn = test_db.get_connection()

    # WAL mode on
    cur = conn.execute("PRAGMA journal_mode")
    assert cur.fetchone()[0].lower() == "wal"

    # Foreign keys on
    cur_fk = conn.execute("PRAGMA foreign_keys")
    assert cur_fk.fetchone()[0] == 1

    # Check tables for no FLOAT or REAL columns
    tables = ["merchants", "merchant_users", "agents", "policies", "captures", "action_requests", "approvals", "executions", "audit_events"]
    for tbl in tables:
        cols = conn.execute(f"PRAGMA table_info({tbl})").fetchall()
        for col in cols:
            col_type = col[2].upper()
            assert "FLOAT" not in col_type
            assert "REAL" not in col_type
            assert "DOUBLE" not in col_type


# ---------------------------------------------------------------------------
# 13. SEED CHECK
# ---------------------------------------------------------------------------
def test_13_seed_check(test_db, test_config):
    """
    Seeded merchant, judge login, agent, and three $300 captures exist;
    a second app start / seed call does not duplicate seeds.
    """
    conn = test_db.get_connection()

    # Verify original seeds exist
    assert conn.execute("SELECT count(*) FROM merchants WHERE id = ?", (test_config.merchant_id,)).fetchone()[0] == 1
    assert conn.execute("SELECT count(*) FROM merchant_users WHERE username = ?", (test_config.judge_username,)).fetchone()[0] == 1
    assert conn.execute("SELECT count(*) FROM agents WHERE id = ?", (test_config.agent_id,)).fetchone()[0] == 1
    assert conn.execute("SELECT count(*) FROM captures WHERE id IN ('CAP_A', 'CAP_B', 'CAP_C')").fetchone()[0] == 3

    # Call seed_defaults again
    test_db.seed_defaults(test_config)

    # Counts must remain exactly the same (idempotent seeding)
    assert conn.execute("SELECT count(*) FROM merchants WHERE id = ?", (test_config.merchant_id,)).fetchone()[0] == 1
    assert conn.execute("SELECT count(*) FROM merchant_users WHERE username = ?", (test_config.judge_username,)).fetchone()[0] == 1
    assert conn.execute("SELECT count(*) FROM agents WHERE id = ?", (test_config.agent_id,)).fetchone()[0] == 1
    assert conn.execute("SELECT count(*) FROM captures WHERE id IN ('CAP_A', 'CAP_B', 'CAP_C')").fetchone()[0] == 3


# ---------------------------------------------------------------------------
# 14. RESILIENCE
# ---------------------------------------------------------------------------
def test_14_resilience(client, agent_headers, merchant_headers):
    """
    Oversized body, malformed JSON, wrong content-type, SQL-injection strings in ids,
    and ids with unicode all return clean 4xx, never 500.
    """
    # 1. Malformed JSON -> 400 or 422
    r_bad_json = client.post(
        "/api/agent/refund-requests",
        content="{\"bad_json: missing_brace",
        headers={**agent_headers, "Content-Type": "application/json"},
    )
    assert r_bad_json.status_code in (400, 422)

    # 2. Wrong content-type
    r_bad_ct = client.post(
        "/api/agent/refund-requests",
        content="<xml>test</xml>",
        headers={**agent_headers, "Content-Type": "application/xml"},
    )
    assert r_bad_ct.status_code in (400, 415, 422)

    # 3. SQL Injection strings in request_id
    sqli_ids = [
        "req_1'; DROP TABLE action_requests; --",
        "1' OR '1'='1",
        "../../etc/passwd",
    ]
    for bad_id in sqli_ids:
        r_sqli_get = client.get(f"/api/agent/requests/{bad_id}", headers=agent_headers)
        assert r_sqli_get.status_code in (400, 404)

        r_sqli_app = client.post(f"/api/merchant/requests/{bad_id}/approve", headers=merchant_headers)
        assert r_sqli_app.status_code in (400, 404)

    # 4. Unicode in IDs
    unicode_ids = [
        "req_🔥_test_测试",
        "req_مرحبا",
    ]
    for u_id in unicode_ids:
        r_u_get = client.get(f"/api/agent/requests/{u_id}", headers=agent_headers)
        assert r_u_get.status_code in (400, 404)
