import json
import pytest
from backend.audit import (
    record_audit_event,
    verify_audit_chain,
    AuditVerificationError,
)

def test_audit_hash_chain_creation_and_verification(test_db):
    conn = test_db.get_connection()

    # Record 3 events
    record_audit_event(
        conn=conn,
        event_type="DECISION",
        actor_type="agent",
        actor_id="agent_1",
        request_id="req_1",
        merchant_id="merch_1",
        payload={"decision": "ALLOW", "amount": "5.00"},
    )

    record_audit_event(
        conn=conn,
        event_type="EXECUTION",
        actor_type="system",
        actor_id="vaultline_gateway",
        request_id="req_1",
        merchant_id="merch_1",
        payload={"status": "COMPLETED", "paypal_refund_id": "REF_123"},
    )

    record_audit_event(
        conn=conn,
        event_type="APPROVAL",
        actor_type="merchant_user",
        actor_id="judge_1",
        request_id="req_2",
        merchant_id="merch_1",
        payload={"action": "approve", "reason": "Verified with customer"},
    )

    is_valid, msg, count = verify_audit_chain(conn)
    assert is_valid is True
    assert count == 3
    assert "valid" in msg.lower()

def test_tampered_audit_row_detected_by_verify(test_db):
    conn = test_db.get_connection()

    record_audit_event(
        conn=conn,
        event_type="DECISION",
        actor_type="agent",
        actor_id="agent_1",
        request_id="req_1",
        merchant_id="merch_1",
        payload={"decision": "ALLOW", "amount": "5.00"},
    )

    record_audit_event(
        conn=conn,
        event_type="EXECUTION",
        actor_type="system",
        actor_id="vaultline_gateway",
        request_id="req_1",
        merchant_id="merch_1",
        payload={"status": "COMPLETED"},
    )

    # Verify initially valid
    is_valid, _, _ = verify_audit_chain(conn)
    assert is_valid is True

    # Tamper row 1 payload
    conn.execute(
        "UPDATE audit_events SET payload = ? WHERE id = 1",
        (json.dumps({"decision": "ALLOW", "amount": "5000.00"}),),
    )
    conn.commit()

    is_valid, msg, _ = verify_audit_chain(conn)
    assert is_valid is False
    assert "verification failed" in msg.lower() or "mismatch" in msg.lower()

def test_audit_redacts_secrets(test_db):
    conn = test_db.get_connection()

    record_audit_event(
        conn=conn,
        event_type="LOGIN",
        actor_type="merchant_user",
        actor_id="judge_1",
        request_id=None,
        merchant_id="merch_1",
        payload={
            "username": "judge_merchant",
            "password": "super_secret_password",
            "api_key": "vl_secret_agent_key_123",
            "session_token": "sess_token_xyz987",
            "normal_data": "safe_value",
        },
    )

    cursor = conn.execute("SELECT payload FROM audit_events WHERE id = 1")
    row = cursor.fetchone()
    payload = json.loads(row[0])

    assert payload["password"] == "[REDACTED]"
    assert payload["api_key"] == "[REDACTED]"
    assert payload["session_token"] == "[REDACTED]"
    assert payload["normal_data"] == "safe_value"

