from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, List, Optional

class Decision(str, Enum):
    ALLOW = "ALLOW"
    ESCALATE = "ESCALATE"
    BLOCK = "BLOCK"

@dataclass
class PolicyEvaluationResult:
    decision: Decision
    reasons: List[str] = field(default_factory=list)
    risk_summary: str = ""
    approval_required: bool = False

def evaluate_refund_policy(
    agent: Optional[Dict[str, Any]],
    capture: Optional[Dict[str, Any]],
    amount: Decimal,
    currency: str = "USD",
    action: str = "refund",
    ai_risk_flag: bool = False,
    ai_risk_explanation: Optional[str] = None,
    ai_signal_error: Optional[str] = None,
) -> PolicyEvaluationResult:
    """
    Evaluates the Policy Ladder:
    Strictest result wins. Record ALL failure reasons.
    - BLOCK:
        malformed schema; inactive or revoked agent; action not permitted;
        non-USD; amount <= 0; wrong merchant or capture ownership;
        capture not refundable; amount greater than remaining refundable;
        amount > $25.
    - ESCALATE: amount > $10 and <= $25, with all hard checks passing.
    - ALLOW: amount <= $10, no risk flag.
    - AI risk flag: ALLOW becomes ESCALATE, nothing else. Never loosens BLOCK or ESCALATE.
    """
    reasons: List[str] = []

    # 1. Agent checks
    if agent is None:
        reasons.append("Agent record not found.")
    else:
        status = agent.get("status")
        if status == "inactive":
            reasons.append("Agent is currently inactive.")
        elif status == "revoked":
            reasons.append("Agent credentials have been revoked.")
        elif status != "active":
            reasons.append(f"Agent status '{status}' is not permitted.")

    # 2. Action check
    if action != "refund":
        reasons.append(f"Action '{action}' is not permitted. Only 'refund' is allowed.")

    # 3. Currency check
    if currency != "USD":
        reasons.append(f"Currency '{currency}' is not supported. Vaultline MVP supports USD only.")

    # 4. Amount <= 0 check
    if amount <= Decimal("0"):
        reasons.append(f"Amount ${amount} must be greater than zero.")

    # 5. Capture ownership and balance checks
    if capture is None:
        reasons.append("Capture transaction not found.")
    else:
        if agent and capture.get("merchant_id") != agent.get("merchant_id"):
            reasons.append("Capture transaction does not belong to the authenticated merchant.")

        cap_status = capture.get("status")
        if cap_status not in ("COMPLETED", "PARTIALLY_REFUNDED"):
            reasons.append(f"Capture status '{cap_status}' is not refundable.")

        try:
            orig = Decimal(str(capture.get("original_amount", "0")))
            ref = Decimal(str(capture.get("refunded_amount", "0")))
            remaining = orig - ref
            if remaining <= Decimal("0"):
                reasons.append("Capture has no remaining refundable balance.")
            elif amount > remaining:
                reasons.append(
                    f"Requested amount ${amount} exceeds remaining refundable balance ${remaining}."
                )
        except Exception:
            reasons.append("Malformed capture balance data.")

    # 6. Upper policy limit check ($25.00)
    if amount > Decimal("25.00"):
        reasons.append(f"Amount ${amount} exceeds maximum allowed policy limit ($25.00).")

    # Ladder Evaluation
    if reasons:
        # Hard BLOCK
        return PolicyEvaluationResult(
            decision=Decision.BLOCK,
            reasons=reasons,
            risk_summary=f"Request blocked by policy: {'; '.join(reasons)}",
            approval_required=False,
        )

    # All hard checks pass: test middle and lower rungs
    if amount > Decimal("10.00") and amount <= Decimal("25.00"):
        decision = Decision.ESCALATE
        reasons.append("Amount is between $10.01 and $25.00; requires merchant approval.")
        risk_summary = "Escalated for human judge approval due to amount threshold."
        approval_required = True
    else:
        # amount <= $10.00
        decision = Decision.ALLOW
        reasons.append("Amount is <= $10.00 with all checks passing.")
        risk_summary = "Low risk auto-approved refund."
        approval_required = False

    # 7. AI risk signal evaluation:
    # Rule 5: An AI risk signal can only tighten ALLOW to ESCALATE.
    # AI error, timeout or garbage output never becomes ALLOW. It never loosens BLOCK or ESCALATE.
    has_ai_risk = ai_risk_flag or bool(ai_signal_error)
    if decision == Decision.ALLOW and has_ai_risk:
        decision = Decision.ESCALATE
        approval_required = True
        explanation = ai_signal_error or ai_risk_explanation or "Risk signal triggered"
        reasons.append(f"Tightened to ESCALATE by AI risk signal: {explanation}")
        risk_summary = f"Escalated due to AI risk signal: {explanation}"

    return PolicyEvaluationResult(
        decision=decision,
        reasons=reasons,
        risk_summary=risk_summary,
        approval_required=approval_required,
    )

