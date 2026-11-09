import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple
import sqlite3
from backend.config import redact_sensitive_data

class AuditVerificationError(Exception):
    pass

def compute_event_hash(
    prev_hash: str,
    event_type: str,
    request_id: Optional[str],
    merchant_id: Optional[str],
    actor_type: str,
    actor_id: Optional[str],
    payload_json: str,
    timestamp: str,
) -> str:
    """Computes SHA-256 digest over canonical event string."""
    raw = (
        f"{prev_hash}|{event_type}|{request_id or ''}|{merchant_id or ''}|"
        f"{actor_type}|{actor_id or ''}|{payload_json}|{timestamp}"
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()

def record_audit_event(
    conn: sqlite3.Connection,
    event_type: str,
    actor_type: str,
    actor_id: Optional[str] = None,
    request_id: Optional[str] = None,
    merchant_id: Optional[str] = None,
    payload: Optional[Dict[str, Any]] = None,
) -> int:
    """
    Appends an event to the tamper-evident audit hash chain.
    Redacts all secrets before serialization.
    """
    safe_payload = redact_sensitive_data(payload or {})
    payload_json = json.dumps(safe_payload, sort_keys=True, separators=(",", ":"))
    now_iso = datetime.now(timezone.utc).isoformat()

    # Fetch last event's hash to form the chain
    cursor = conn.execute("SELECT hash FROM audit_events ORDER BY id DESC LIMIT 1")
    row = cursor.fetchone()
    prev_hash = row[0] if row else "GENESIS"

    event_hash = compute_event_hash(
        prev_hash=prev_hash,
        event_type=event_type,
        request_id=request_id,
        merchant_id=merchant_id,
        actor_type=actor_type,
        actor_id=actor_id,
        payload_json=payload_json,
        timestamp=now_iso,
    )

    insert_cur = conn.execute(
        """
        INSERT INTO audit_events (
            prev_hash, event_type, request_id, merchant_id,
            actor_type, actor_id, payload, timestamp, hash
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            prev_hash,
            event_type,
            request_id,
            merchant_id,
            actor_type,
            actor_id,
            payload_json,
            now_iso,
            event_hash,
        ),
    )
    conn.commit()
    return insert_cur.lastrowid

def verify_audit_chain(conn: sqlite3.Connection) -> Tuple[bool, str, int]:
    """
    Verifies the integrity of the audit hash chain.
    Returns (is_valid, message, total_events).
    Note: Audit log is tamper-evident, not immutable.
    """
    cursor = conn.execute("SELECT * FROM audit_events ORDER BY id ASC")
    rows = cursor.fetchall()

    if not rows:
        return True, "Audit log is empty.", 0

    expected_prev_hash = "GENESIS"
    for idx, row in enumerate(rows):
        # 1. Verify link to previous event
        if row["prev_hash"] != expected_prev_hash:
            return (
                False,
                f"Chain link broken at row id {row['id']}: expected prev_hash "
                f"'{expected_prev_hash}', got '{row['prev_hash']}'.",
                len(rows),
            )

        # 2. Recompute hash
        recomputed_hash = compute_event_hash(
            prev_hash=row["prev_hash"],
            event_type=row["event_type"],
            request_id=row["request_id"],
            merchant_id=row["merchant_id"],
            actor_type=row["actor_type"],
            actor_id=row["actor_id"],
            payload_json=row["payload"],
            timestamp=row["timestamp"],
        )

        if recomputed_hash != row["hash"]:
            return (
                False,
                f"Hash mismatch at row id {row['id']}: record content altered "
                f"(expected hash '{recomputed_hash}', stored '{row['hash']}').",
                len(rows),
            )

        expected_prev_hash = row["hash"]

    return True, "Audit chain is valid and verified successfully (tamper-evident integrity intact).", len(rows)
