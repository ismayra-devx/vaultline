import hashlib
import hmac
import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional, Tuple
from fastapi import Request, HTTPException, status
import sqlite3

# PBKDF2 Password Hashing
def hash_password(password: str, salt: Optional[bytes] = None) -> str:
    if salt is None:
        salt = os.urandom(16)
    key = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 100_000)
    return f"{salt.hex()}:{key.hex()}"

def verify_password(password: str, hashed_value: str) -> bool:
    try:
        salt_hex, key_hex = hashed_value.split(":")
        salt = bytes.fromhex(salt_hex)
        expected_key = bytes.fromhex(key_hex)
        computed_key = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 100_000)
        return hmac.compare_digest(expected_key, computed_key)
    except Exception:
        return False

def hash_agent_key(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()

@dataclass
class AgentIdentity:
    agent_id: str
    merchant_id: str
    name: str
    status: str

@dataclass
class MerchantSession:
    token: str
    merchant_id: str
    user_id: str
    username: str
    role: str
    expires_at: datetime

# Active session store
_sessions: Dict[str, MerchantSession] = {}

def create_merchant_session(
    merchant_id: str,
    user_id: str,
    username: str,
    role: str,
    expiry_hours: int = 4,
) -> MerchantSession:
    token = f"sess_{secrets.token_urlsafe(32)}"
    expires_at = datetime.now(timezone.utc) + timedelta(hours=expiry_hours)
    session = MerchantSession(
        token=token,
        merchant_id=merchant_id,
        user_id=user_id,
        username=username,
        role=role,
        expires_at=expires_at,
    )
    _sessions[token] = session
    return session

def get_merchant_session_by_token(token: str) -> Optional[MerchantSession]:
    session = _sessions.get(token)
    if not session:
        return None
    if datetime.now(timezone.utc) > session.expires_at:
        del _sessions[token]
        return None
    return session

def extract_credential(request: Request) -> Optional[str]:
    # Check Authorization header (Bearer ...)
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        return auth_header[7:].strip()

    # Check custom headers
    if "X-Agent-Key" in request.headers:
        return request.headers["X-Agent-Key"].strip()
    if "X-Session-Token" in request.headers:
        return request.headers["X-Session-Token"].strip()
    return None

def is_agent_key(conn: sqlite3.Connection, token: str) -> Tuple[bool, Optional[sqlite3.Row]]:
    token_hash = hash_agent_key(token)
    cursor = conn.execute(
        "SELECT id, merchant_id, name, status FROM agents WHERE api_key_hash = ?",
        (token_hash,),
    )
    row = cursor.fetchone()
    if row:
        return True, row
    return False, None

def require_agent_auth(request: Request) -> AgentIdentity:
    token = extract_credential(request)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing agent credentials.",
        )

    # Invariant 3: Check if credential is a merchant session token
    if get_merchant_session_by_token(token) is not None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Merchant sessions are forbidden on agent endpoints.",
        )

    db = request.app.state.db
    conn = db.get_connection()
    is_agent, row = is_agent_key(conn, token)
    if not is_agent:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid agent credentials.",
        )

    # Invariant check: revoked key gets 401
    if row["status"] == "revoked":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Agent API key has been revoked.",
        )

    return AgentIdentity(
        agent_id=row["id"],
        merchant_id=row["merchant_id"],
        name=row["name"],
        status=row["status"],
    )

def require_merchant_auth(request: Request) -> MerchantSession:
    token = extract_credential(request)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing merchant session credentials.",
        )

    db = request.app.state.db
    conn = db.get_connection()

    # Invariant 3: An agent key gets 403 on every /api/merchant/* route
    is_agent, _ = is_agent_key(conn, token)
    if is_agent:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Agent credentials cannot access merchant endpoints.",
        )

    session = get_merchant_session_by_token(token)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired merchant session.",
        )

    return session

def require_judge_role(session: MerchantSession) -> None:
    if session.role not in ("judge", "admin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"User role '{session.role}' is not authorized. Must be judge or admin.",
        )

