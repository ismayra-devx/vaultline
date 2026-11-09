from decimal import Decimal
import pytest
from backend.policy import evaluate_refund_policy, Decision

def test_ladder_boundaries():
    # Capture with $300 balance
    capture = {
        "id": "CAP_A",
        "merchant_id": "merch_1",
        "original_amount": "300.00",
        "refunded_amount": "0.00",
        "currency": "USD",
        "status": "COMPLETED",
    }
    agent = {
        "id": "agent_1",
        "merchant_id": "merch_1",
        "status": "active",
    }

    # 1. Boundary: $5.00 -> ALLOW
    res_5 = evaluate_refund_policy(
        agent=agent,
        capture=capture,
        amount=Decimal("5.00"),
        currency="USD",
        action="refund",
        ai_risk_flag=False,
    )
    assert res_5.decision == Decision.ALLOW
    assert not res_5.approval_required

    # 2. Boundary: $10.00 -> ALLOW
    res_10 = evaluate_refund_policy(
        agent=agent,
        capture=capture,
        amount=Decimal("10.00"),
        currency="USD",
        action="refund",
        ai_risk_flag=False,
    )
    assert res_10.decision == Decision.ALLOW
    assert not res_10.approval_required

    # 3. Boundary: $10.01 -> ESCALATE
    res_10_01 = evaluate_refund_policy(
        agent=agent,
        capture=capture,
        amount=Decimal("10.01"),
        currency="USD",
        action="refund",
        ai_risk_flag=False,
    )
    assert res_10_01.decision == Decision.ESCALATE
    assert res_10_01.approval_required

    # 4. Boundary: $25.00 -> ESCALATE
    res_25 = evaluate_refund_policy(
        agent=agent,
        capture=capture,
        amount=Decimal("25.00"),
        currency="USD",
        action="refund",
        ai_risk_flag=False,
    )
    assert res_25.decision == Decision.ESCALATE
    assert res_25.approval_required

    # 5. Boundary: $25.01 -> BLOCK
    res_25_01 = evaluate_refund_policy(
        agent=agent,
        capture=capture,
        amount=Decimal("25.01"),
        currency="USD",
        action="refund",
        ai_risk_flag=False,
    )
    assert res_25_01.decision == Decision.BLOCK
    assert not res_25_01.approval_required
    assert any("25.00" in r for r in res_25_01.reasons)

    # 6. Boundary: $250.00 -> BLOCK
    res_250 = evaluate_refund_policy(
        agent=agent,
        capture=capture,
        amount=Decimal("250.00"),
        currency="USD",
        action="refund",
        ai_risk_flag=False,
    )
    assert res_250.decision == Decision.BLOCK
    assert not res_250.approval_required

def test_hard_block_conditions():
    capture = {
        "id": "CAP_A",
        "merchant_id": "merch_1",
        "original_amount": "300.00",
        "refunded_amount": "0.00",
        "currency": "USD",
        "status": "COMPLETED",
    }
    agent = {
        "id": "agent_1",
        "merchant_id": "merch_1",
        "status": "active",
    }

    # 1. Inactive agent
    res = evaluate_refund_policy(
        agent={**agent, "status": "inactive"},
        capture=capture,
        amount=Decimal("5.00"),
        currency="USD",
        action="refund",
    )
    assert res.decision == Decision.BLOCK
    assert any("inactive" in r.lower() for r in res.reasons)

    # 2. Revoked agent
    res = evaluate_refund_policy(
        agent={**agent, "status": "revoked"},
        capture=capture,
        amount=Decimal("5.00"),
        currency="USD",
        action="refund",
    )
    assert res.decision == Decision.BLOCK
    assert any("revoked" in r.lower() for r in res.reasons)

    # 3. Action not permitted
    res = evaluate_refund_policy(
        agent=agent,
        capture=capture,
        amount=Decimal("5.00"),
        currency="USD",
        action="payout",
    )
    assert res.decision == Decision.BLOCK
    assert any("action" in r.lower() for r in res.reasons)

    # 4. Non-USD currency
    res = evaluate_refund_policy(
        agent=agent,
        capture=capture,
        amount=Decimal("5.00"),
        currency="EUR",
        action="refund",
    )
    assert res.decision == Decision.BLOCK
    assert any("currency" in r.lower() or "usd" in r.lower() for r in res.reasons)

    # 5. Amount <= 0 (zero and negative)
    res_zero = evaluate_refund_policy(
        agent=agent,
        capture=capture,
        amount=Decimal("0.00"),
        currency="USD",
        action="refund",
    )
    assert res_zero.decision == Decision.BLOCK
    assert any("greater than zero" in r.lower() for r in res_zero.reasons)

    res_neg = evaluate_refund_policy(
        agent=agent,
        capture=capture,
        amount=Decimal("-5.00"),
        currency="USD",
        action="refund",
    )
    assert res_neg.decision == Decision.BLOCK
    assert any("greater than zero" in r.lower() for r in res_neg.reasons)

    # 6. Wrong merchant or capture ownership
    res_wrong_merch = evaluate_refund_policy(
        agent=agent,
        capture={**capture, "merchant_id": "merch_other"},
        amount=Decimal("5.00"),
        currency="USD",
        action="refund",
    )
    assert res_wrong_merch.decision == Decision.BLOCK
    assert any("belong" in r.lower() or "ownership" in r.lower() for r in res_wrong_merch.reasons)

    # 7. Capture not refundable (status not COMPLETED or PARTIALLY_REFUNDED)
    res_not_ref = evaluate_refund_policy(
        agent=agent,
        capture={**capture, "status": "FAILED"},
        amount=Decimal("5.00"),
        currency="USD",
        action="refund",
    )
    assert res_not_ref.decision == Decision.BLOCK
    assert any("not refundable" in r.lower() for r in res_not_ref.reasons)

    # 8. Amount greater than remaining refundable
    partially_refunded_cap = {
        **capture,
        "original_amount": "100.00",
        "refunded_amount": "95.00",  # remaining is $5.00
    }
    res_exceeds = evaluate_refund_policy(
        agent=agent,
        capture=partially_refunded_cap,
        amount=Decimal("6.00"),
        currency="USD",
        action="refund",
    )
    assert res_exceeds.decision == Decision.BLOCK
    assert any("remaining" in r.lower() or "exceeds" in r.lower() for r in res_exceeds.reasons)

def test_strictest_result_wins_and_records_all_reasons():
    # Multiple failures simultaneously:
    # Inactive agent + non-USD + amount > 25 + action invalid
    agent = {"id": "agent_1", "merchant_id": "merch_1", "status": "inactive"}
    capture = {
        "id": "CAP_A",
        "merchant_id": "merch_1",
        "original_amount": "300.00",
        "refunded_amount": "0.00",
        "currency": "USD",
        "status": "COMPLETED",
    }
    res = evaluate_refund_policy(
        agent=agent,
        capture=capture,
        amount=Decimal("100.00"),
        currency="GBP",
        action="transfer",
    )
    assert res.decision == Decision.BLOCK
    # Check that ALL failure reasons are recorded
    assert len(res.reasons) >= 4
    reason_text = " ".join(res.reasons).lower()
    assert "inactive" in reason_text
    assert "gbp" in reason_text or "currency" in reason_text
    assert "action" in reason_text
    assert "25.00" in reason_text

def test_ai_risk_signal_rules():
    agent = {"id": "agent_1", "merchant_id": "merch_1", "status": "active"}
    capture = {
        "id": "CAP_A",
        "merchant_id": "merch_1",
        "original_amount": "300.00",
        "refunded_amount": "0.00",
        "currency": "USD",
        "status": "COMPLETED",
    }

    # 1. ALLOW tightened to ESCALATE by AI risk flag
    res_tighten = evaluate_refund_policy(
        agent=agent,
        capture=capture,
        amount=Decimal("5.00"),
        currency="USD",
        action="refund",
        ai_risk_flag=True,
        ai_risk_explanation="Suspicious refund frequency detected",
    )
    assert res_tighten.decision == Decision.ESCALATE
    assert res_tighten.approval_required
    assert any("ai" in r.lower() or "risk" in r.lower() for r in res_tighten.reasons)

    # 2. AI error / timeout / garbage never becomes ALLOW
    res_ai_err = evaluate_refund_policy(
        agent=agent,
        capture=capture,
        amount=Decimal("5.00"),
        currency="USD",
        action="refund",
        ai_risk_flag=True,
        ai_signal_error="Timeout calling risk model",
    )
    assert res_ai_err.decision == Decision.ESCALATE
    assert res_ai_err.approval_required

    # 3. AI risk flag never loosens BLOCK
    res_block_with_ai = evaluate_refund_policy(
        agent=agent,
        capture=capture,
        amount=Decimal("50.00"),  # > 25 -> BLOCK
        currency="USD",
        action="refund",
        ai_risk_flag=False,  # Even if AI says safe, policy stays BLOCK
    )
    assert res_block_with_ai.decision == Decision.BLOCK

    # 4. AI risk flag never loosens ESCALATE
    res_esc_with_ai = evaluate_refund_policy(
        agent=agent,
        capture=capture,
        amount=Decimal("20.00"),  # 10..25 -> ESCALATE
        currency="USD",
        action="refund",
        ai_risk_flag=False,  # Even if AI says safe, stays ESCALATE
    )
    assert res_esc_with_ai.decision == Decision.ESCALATE

