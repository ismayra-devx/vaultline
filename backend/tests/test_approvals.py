import threading
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import pytest

from backend.approvals import (
    compute_canonical_hash,
    create_approval_record,
    consume_and_verify_approval,
    ApprovalVerificationError,
    TamperDetectedError,
    ExpiredApprovalError,
    NonceAlreadyConsumedError,
)

def test_canonical_json_hash_deterministic():
    h1 = compute_canonical_hash(
        merchant_id="merch_1",
        agent_id="agent_1",
        action="refund",
        capture_id="CAP_A",
        amount="20.00",
        currency="USD",
        policy_version="v1",
        nonce="nonce_12345",
        expires_at="2026-11-09T18:00:00Z",
    )
    h2 = compute_canonical_hash(
        merchant_id="merch_1",
        agent_id="agent_1",
        action="refund",
        capture_id="CAP_A",
        amount="20.00",
        currency="USD",
        policy_version="v1",
        nonce="nonce_12345",
        expires_at="2026-11-09T18:00:00Z",
    )
    assert h1 == h2
    assert len(h1) == 64

def test_approval_creation_and_successful_consume(test_db):
    conn = test_db.get_connection()
    # Insert an action request
    conn.execute(
        """
        INSERT INTO action_requests (
            id, merchant_id, agent_id, action, capture_id, amount, currency,
            ai_risk_flag, decision, reasons, risk_summary, approval_required,
            policy_version, status, created_at
        ) VALUES (
            'req_1', 'merch_test_1', 'agent_test_1', 'refund', 'CAP_A', '20.00', 'USD',
            0, 'ESCALATE', '[]', 'Escalated', 1, 'v1', 'DECIDED', ?
        )
        """,
        (datetime.now(timezone.utc).isoformat(),),
    )
    conn.commit()

    approval_id = create_approval_record(
        conn=conn,
        request_id="req_1",
        merchant_id="merch_test_1",
        agent_id="agent_test_1",
        action="refund",
        capture_id="CAP_A",
        amount=Decimal("20.00"),
        currency="USD",
        policy_version="v1",
        expiry_seconds=900,
    )
    assert approval_id

    # Verify and consume
    appr = consume_and_verify_approval(
        conn=conn,
        request_id="req_1",
        merchant_id="merch_test_1",
        user_id="judge_1",
        user_role="judge",
    )
    assert appr["consumed"] == 1

def test_tampered_amount_detected(test_db):
    conn = test_db.get_connection()
    now_iso = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO action_requests (
            id, merchant_id, agent_id, action, capture_id, amount, currency,
            ai_risk_flag, decision, reasons, risk_summary, approval_required,
            policy_version, status, created_at
        ) VALUES (
            'req_tamper_amt', 'merch_test_1', 'agent_test_1', 'refund', 'CAP_A', '20.00', 'USD',
            0, 'ESCALATE', '[]', 'Escalated', 1, 'v1', 'DECIDED', ?
        )
        """,
        (now_iso,),
    )
    conn.commit()

    create_approval_record(
        conn=conn,
        request_id="req_tamper_amt",
        merchant_id="merch_test_1",
        agent_id="agent_test_1",
        action="refund",
        capture_id="CAP_A",
        amount=Decimal("20.00"),
        currency="USD",
        policy_version="v1",
        expiry_seconds=900,
    )

    # Tamper action_request amount to 24.00
    conn.execute("UPDATE action_requests SET amount = '24.00' WHERE id = 'req_tamper_amt'")
    conn.commit()

    with pytest.raises(TamperDetectedError):
        consume_and_verify_approval(
            conn=conn,
            request_id="req_tamper_amt",
            merchant_id="merch_test_1",
            user_id="judge_1",
            user_role="judge",
        )

def test_tampered_capture_id_detected(test_db):
    conn = test_db.get_connection()
    now_iso = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO action_requests (
            id, merchant_id, agent_id, action, capture_id, amount, currency,
            ai_risk_flag, decision, reasons, risk_summary, approval_required,
            policy_version, status, created_at
        ) VALUES (
            'req_tamper_cap', 'merch_test_1', 'agent_test_1', 'refund', 'CAP_A', '20.00', 'USD',
            0, 'ESCALATE', '[]', 'Escalated', 1, 'v1', 'DECIDED', ?
        )
        """,
        (now_iso,),
    )
    conn.commit()

    create_approval_record(
        conn=conn,
        request_id="req_tamper_cap",
        merchant_id="merch_test_1",
        agent_id="agent_test_1",
        action="refund",
        capture_id="CAP_A",
        amount=Decimal("20.00"),
        currency="USD",
        policy_version="v1",
        expiry_seconds=900,
    )

    # Tamper capture_id to CAP_B
    conn.execute("UPDATE action_requests SET capture_id = 'CAP_B' WHERE id = 'req_tamper_cap'")
    conn.commit()

    with pytest.raises(TamperDetectedError):
        consume_and_verify_approval(
            conn=conn,
            request_id="req_tamper_cap",
            merchant_id="merch_test_1",
            user_id="judge_1",
            user_role="judge",
        )

def test_tampered_policy_version_detected(test_db):
    conn = test_db.get_connection()
    now_iso = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO action_requests (
            id, merchant_id, agent_id, action, capture_id, amount, currency,
            ai_risk_flag, decision, reasons, risk_summary, approval_required,
            policy_version, status, created_at
        ) VALUES (
            'req_tamper_pol', 'merch_test_1', 'agent_test_1', 'refund', 'CAP_A', '20.00', 'USD',
            0, 'ESCALATE', '[]', 'Escalated', 1, 'v1', 'DECIDED', ?
        )
        """,
        (now_iso,),
    )
    conn.commit()

    create_approval_record(
        conn=conn,
        request_id="req_tamper_pol",
        merchant_id="merch_test_1",
        agent_id="agent_test_1",
        action="refund",
        capture_id="CAP_A",
        amount=Decimal("20.00"),
        currency="USD",
        policy_version="v1",
        expiry_seconds=900,
    )

    # Tamper policy_version to v2
    conn.execute("UPDATE action_requests SET policy_version = 'v2' WHERE id = 'req_tamper_pol'")
    conn.commit()

    with pytest.raises(TamperDetectedError):
        consume_and_verify_approval(
            conn=conn,
            request_id="req_tamper_pol",
            merchant_id="merch_test_1",
            user_id="judge_1",
            user_role="judge",
        )

def test_expired_nonce_rejected(test_db):
    conn = test_db.get_connection()
    now_iso = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO action_requests (
            id, merchant_id, agent_id, action, capture_id, amount, currency,
            ai_risk_flag, decision, reasons, risk_summary, approval_required,
            policy_version, status, created_at
        ) VALUES (
            'req_expired', 'merch_test_1', 'agent_test_1', 'refund', 'CAP_A', '20.00', 'USD',
            0, 'ESCALATE', '[]', 'Escalated', 1, 'v1', 'DECIDED', ?
        )
        """,
        (now_iso,),
    )
    conn.commit()

    # Create approval with -10 second expiry
    create_approval_record(
        conn=conn,
        request_id="req_expired",
        merchant_id="merch_test_1",
        agent_id="agent_test_1",
        action="refund",
        capture_id="CAP_A",
        amount=Decimal("20.00"),
        currency="USD",
        policy_version="v1",
        expiry_seconds=-10,
    )

    with pytest.raises(ExpiredApprovalError):
        consume_and_verify_approval(
            conn=conn,
            request_id="req_expired",
            merchant_id="merch_test_1",
            user_id="judge_1",
            user_role="judge",
        )

def test_replayed_nonce_rejected(test_db):
    conn = test_db.get_connection()
    now_iso = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO action_requests (
            id, merchant_id, agent_id, action, capture_id, amount, currency,
            ai_risk_flag, decision, reasons, risk_summary, approval_required,
            policy_version, status, created_at
        ) VALUES (
            'req_replay', 'merch_test_1', 'agent_test_1', 'refund', 'CAP_A', '20.00', 'USD',
            0, 'ESCALATE', '[]', 'Escalated', 1, 'v1', 'DECIDED', ?
        )
        """,
        (now_iso,),
    )
    conn.commit()

    create_approval_record(
        conn=conn,
        request_id="req_replay",
        merchant_id="merch_test_1",
        agent_id="agent_test_1",
        action="refund",
        capture_id="CAP_A",
        amount=Decimal("20.00"),
        currency="USD",
        policy_version="v1",
        expiry_seconds=900,
    )

    # First consume succeeds
    consume_and_verify_approval(
        conn=conn,
        request_id="req_replay",
        merchant_id="merch_test_1",
        user_id="judge_1",
        user_role="judge",
    )

    # Second consume must fail as replayed nonce
    with pytest.raises(NonceAlreadyConsumedError):
        consume_and_verify_approval(
            conn=conn,
            request_id="req_replay",
            merchant_id="merch_test_1",
            user_id="judge_1",
            user_role="judge",
        )

def test_concurrent_double_approve_gives_exactly_one_success(test_db):
    conn = test_db.get_connection()
    now_iso = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO action_requests (
            id, merchant_id, agent_id, action, capture_id, amount, currency,
            ai_risk_flag, decision, reasons, risk_summary, approval_required,
            policy_version, status, created_at
        ) VALUES (
            'req_concurrent', 'merch_test_1', 'agent_test_1', 'refund', 'CAP_A', '20.00', 'USD',
            0, 'ESCALATE', '[]', 'Escalated', 1, 'v1', 'DECIDED', ?
        )
        """,
        (now_iso,),
    )
    conn.commit()

    create_approval_record(
        conn=conn,
        request_id="req_concurrent",
        merchant_id="merch_test_1",
        agent_id="agent_test_1",
        action="refund",
        capture_id="CAP_A",
        amount=Decimal("20.00"),
        currency="USD",
        policy_version="v1",
        expiry_seconds=900,
    )

    results = []
    errors = []

    def attempt_approve():
        local_conn = test_db.get_connection()
        try:
            res = consume_and_verify_approval(
                conn=local_conn,
                request_id="req_concurrent",
                merchant_id="merch_test_1",
                user_id="judge_1",
                user_role="judge",
            )
            results.append(res)
        except Exception as e:
            errors.append(e)

    t1 = threading.Thread(target=attempt_approve)
    t2 = threading.Thread(target=attempt_approve)

    t1.start()
    t2.start()
    t1.join()
    t2.join()

    # Exactly one succeeded and one failed
    assert len(results) == 1, f"Expected exactly 1 success, got {len(results)}"
    assert len(errors) == 1, f"Expected exactly 1 error, got {len(errors)}"
    assert isinstance(errors[0], NonceAlreadyConsumedError)

