import hashlib
import json
import secrets
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, Optional
import sqlite3

class ApprovalVerificationError(Exception):
    """Base error for approval verification failures."""
    pass

class CrossMerchantAccessError(ApprovalVerificationError):
    """Raised when an action request belongs to a different merchant."""
    pass

class TamperDetectedError(ApprovalVerificationError):
    """Raised when canonical hash or parameters do not match stored approval."""
    pass

class ExpiredApprovalError(ApprovalVerificationError):
    """Raised when an approval is expired."""
    pass

class NonceAlreadyConsumedError(ApprovalVerificationError):
    """Raised when a nonce was already consumed (replay attack)."""
    pass

def compute_canonical_hash(
    merchant_id: str,
    agent_id: str,
    action: str,
    capture_id: str,
    amount: str,
    currency: str,
    policy_version: str,
    nonce: str,
    expires_at: str,
) -> str:
    """Computes SHA-256 of canonical JSON (sorted keys, decimal strings)."""
    payload = {
        "action": action,
        "agent_id": agent_id,
        "amount": str(amount),
        "capture_id": capture_id,
        "currency": currency,
        "expires_at": expires_at,
        "merchant_id": merchant_id,
        "nonce": nonce,
        "policy_version": policy_version,
    }
    canonical_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()

def create_approval_record(
    conn: sqlite3.Connection,
    request_id: str,
    merchant_id: str,
    agent_id: str,
    action: str,
    capture_id: str,
    amount: Decimal,
    currency: str,
    policy_version: str,
    expiry_seconds: int = 900,
) -> str:
    """Creates a bound approval record with a one-time nonce and canonical hash."""
    approval_id = f"appr_{secrets.token_hex(12)}"
    nonce = f"nonce_{secrets.token_hex(16)}"
    now = datetime.now(timezone.utc)
    expires_at = (now + timedelta(seconds=expiry_seconds)).isoformat()
    now_iso = now.isoformat()

    canonical_hash = compute_canonical_hash(
        merchant_id=merchant_id,
        agent_id=agent_id,
        action=action,
        capture_id=capture_id,
        amount=str(amount),
        currency=currency,
        policy_version=policy_version,
        nonce=nonce,
        expires_at=expires_at,
    )

    conn.execute(
        """
        INSERT INTO approvals (
            id, request_id, merchant_id, agent_id, action, capture_id,
            amount, currency, policy_version, nonce, canonical_hash,
            expires_at, consumed, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
        """,
        (
            approval_id,
            request_id,
            merchant_id,
            agent_id,
            action,
            capture_id,
            str(amount),
            currency,
            policy_version,
            nonce,
            canonical_hash,
            expires_at,
            now_iso,
        ),
    )
    conn.commit()
    return approval_id

def consume_and_verify_approval(
    conn: sqlite3.Connection,
    request_id: str,
    merchant_id: str,
    user_id: str,
    user_role: str,
) -> Dict[str, Any]:
    """
    Verifies merchant role, recomputes canonical hash, checks expiry,
    consumes the nonce with ONE conditional UPDATE WHERE consumed=0 AND not expired,
    and re-validates identity, ownership, capture state, amount, currency, and policy version.
    """
    if user_role not in ("judge", "admin"):
        raise ApprovalVerificationError(f"User role '{user_role}' is not authorized to approve.")

    # 1. Fetch action_request
    cursor = conn.execute(
        "SELECT * FROM action_requests WHERE id = ?",
        (request_id,),
    )
    req = cursor.fetchone()
    if not req:
        raise ApprovalVerificationError(f"Action request '{request_id}' not found.")

    if req["merchant_id"] != merchant_id:
        raise CrossMerchantAccessError("Request does not belong to the merchant.")

    if req["decision"] != "ESCALATE":
        raise ApprovalVerificationError(
            f"Cannot approve request with decision '{req['decision']}'. Only ESCALATE requests can be approved."
        )

    # 2. Fetch approval record
    cursor = conn.execute(
        "SELECT * FROM approvals WHERE request_id = ?",
        (request_id,),
    )
    approval = cursor.fetchone()
    if not approval:
        raise ApprovalVerificationError(f"No approval record found for request '{request_id}'.")

    # 3. Recompute and verify canonical hash
    expected_hash = compute_canonical_hash(
        merchant_id=req["merchant_id"],
        agent_id=req["agent_id"],
        action=req["action"],
        capture_id=req["capture_id"],
        amount=req["amount"],
        currency=req["currency"],
        policy_version=req["policy_version"],
        nonce=approval["nonce"],
        expires_at=approval["expires_at"],
    )

    if approval["canonical_hash"] != expected_hash:
        raise TamperDetectedError("Canonical hash mismatch: request parameters or approval record tampered.")

    # Also verify explicit fields between approval and action_request
    if (
        approval["amount"] != req["amount"]
        or approval["capture_id"] != req["capture_id"]
        or approval["policy_version"] != req["policy_version"]
        or approval["merchant_id"] != req["merchant_id"]
    ):
        raise TamperDetectedError("Tamper detected: approval attributes do not match action request.")

    # 4. Check expiry
    now = datetime.now(timezone.utc)
    expiry_dt = datetime.fromisoformat(approval["expires_at"])
    if now >= expiry_dt:
        raise ExpiredApprovalError("Approval has expired.")

    # 5. One conditional UPDATE inside transaction: WHERE consumed=0 AND expires_at > now
    now_iso = now.isoformat()
    update_cursor = conn.execute(
        """
        UPDATE approvals
        SET consumed = 1, consumed_at = ?, consumed_by_user_id = ?
        WHERE id = ? AND consumed = 0 AND expires_at > ?
        """,
        (now_iso, user_id, approval["id"], now_iso),
    )
    conn.commit()

    if update_cursor.rowcount != 1:
        raise NonceAlreadyConsumedError("Approval nonce already consumed or expired.")

    # 6. Re-validate identity, ownership, capture state, amount, currency, and current policy version
    # Check current policy version in database
    pol_cur = conn.execute(
        "SELECT version FROM policies WHERE merchant_id = ? ORDER BY rowid DESC LIMIT 1",
        (merchant_id,),
    )
    current_pol = pol_cur.fetchone()
    if not current_pol or current_pol["version"] != req["policy_version"]:
        raise TamperDetectedError("Current policy version mismatch. Policy configuration has changed.")

    # Check capture state
    cap_cur = conn.execute(
        "SELECT * FROM captures WHERE id = ?",
        (req["capture_id"],),
    )
    cap = cap_cur.fetchone()
    if not cap:
        raise ApprovalVerificationError("Associated capture transaction not found.")
    if cap["merchant_id"] != merchant_id:
        raise ApprovalVerificationError("Capture transaction does not belong to merchant.")
    if cap["status"] not in ("COMPLETED", "PARTIALLY_REFUNDED"):
        raise ApprovalVerificationError(f"Capture status '{cap['status']}' is not refundable.")

    remaining = Decimal(cap["original_amount"]) - Decimal(cap["refunded_amount"])
    if Decimal(req["amount"]) > remaining:
        raise ApprovalVerificationError("Requested amount exceeds remaining refundable balance.")

    # Return refreshed approval record
    refreshed_cur = conn.execute("SELECT * FROM approvals WHERE id = ?", (approval["id"],))
    return dict(refreshed_cur.fetchone())
