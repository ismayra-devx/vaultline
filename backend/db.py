import hashlib
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Optional
from backend.config import Config

SCHEMA_SQL = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;
PRAGMA busy_timeout = 5000;

CREATE TABLE IF NOT EXISTS merchants (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS merchant_users (
    id TEXT PRIMARY KEY,
    merchant_id TEXT NOT NULL REFERENCES merchants(id),
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agents (
    id TEXT PRIMARY KEY,
    merchant_id TEXT NOT NULL REFERENCES merchants(id),
    name TEXT NOT NULL,
    api_key_hash TEXT UNIQUE NOT NULL,
    status TEXT NOT NULL, -- 'active', 'revoked', 'inactive'
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS policies (
    id TEXT PRIMARY KEY,
    version TEXT NOT NULL,
    merchant_id TEXT NOT NULL REFERENCES merchants(id),
    max_auto_amount TEXT NOT NULL,
    max_escalate_amount TEXT NOT NULL,
    currency TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS captures (
    id TEXT PRIMARY KEY,
    merchant_id TEXT NOT NULL REFERENCES merchants(id),
    original_amount TEXT NOT NULL,
    currency TEXT NOT NULL,
    refunded_amount TEXT NOT NULL,
    status TEXT NOT NULL, -- 'COMPLETED', 'PARTIALLY_REFUNDED', 'REFUNDED'
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS action_requests (
    id TEXT PRIMARY KEY,
    merchant_id TEXT NOT NULL REFERENCES merchants(id),
    agent_id TEXT NOT NULL REFERENCES agents(id),
    action TEXT NOT NULL,
    capture_id TEXT NOT NULL,
    amount TEXT NOT NULL,
    currency TEXT NOT NULL,
    ai_risk_flag INTEGER NOT NULL DEFAULT 0,
    ai_risk_explanation TEXT,
    decision TEXT NOT NULL, -- 'ALLOW', 'ESCALATE', 'BLOCK', 'DENIED'
    reasons TEXT NOT NULL, -- JSON string array
    risk_summary TEXT NOT NULL,
    approval_required INTEGER NOT NULL DEFAULT 0,
    policy_version TEXT NOT NULL,
    expires_at TEXT,
    status TEXT NOT NULL, -- 'DECIDED', 'EXECUTED', 'REJECTED', 'DENIED'
    reason TEXT,
    client_request_id TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS approvals (
    id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL REFERENCES action_requests(id),
    merchant_id TEXT NOT NULL REFERENCES merchants(id),
    agent_id TEXT NOT NULL REFERENCES agents(id),
    action TEXT NOT NULL,
    capture_id TEXT NOT NULL REFERENCES captures(id),
    amount TEXT NOT NULL,
    currency TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    nonce TEXT UNIQUE NOT NULL,
    canonical_hash TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    consumed INTEGER NOT NULL DEFAULT 0,
    consumed_at TEXT,
    consumed_by_user_id TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS executions (
    id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL REFERENCES action_requests(id),
    paypal_request_id TEXT UNIQUE NOT NULL,
    capture_id TEXT NOT NULL,
    amount TEXT NOT NULL,
    currency TEXT NOT NULL,
    status TEXT NOT NULL, -- 'PENDING', 'COMPLETED', 'FAILED', 'UNKNOWN'
    paypal_refund_id TEXT,
    adapter_call_count INTEGER NOT NULL DEFAULT 0,
    error_message TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    prev_hash TEXT NOT NULL,
    event_type TEXT NOT NULL,
    request_id TEXT,
    merchant_id TEXT,
    actor_type TEXT NOT NULL,
    actor_id TEXT,
    payload TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    hash TEXT NOT NULL
);
"""

class Database:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._local = threading.local()

    def get_connection(self) -> sqlite3.Connection:
        if not hasattr(self._local, "conn") or self._local.conn is None:
            conn = sqlite3.connect(
                self.db_path,
                timeout=10.0,
                check_same_thread=False,
                isolation_level=None,  # Autocommit mode for granular transaction control
            )
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode = WAL;")
            conn.execute("PRAGMA foreign_keys = ON;")
            conn.execute("PRAGMA busy_timeout = 5000;")
            self._local.conn = conn
        return self._local.conn

    def init_schema(self) -> None:
        conn = self.get_connection()
        conn.executescript(SCHEMA_SQL)
        # Migrate existing table if columns are missing
        try:
            conn.execute("ALTER TABLE action_requests ADD COLUMN reason TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE action_requests ADD COLUMN client_request_id TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_client_req ON action_requests(agent_id, client_request_id) WHERE client_request_id IS NOT NULL;")
        except sqlite3.OperationalError:
            pass

    def seed_defaults(self, config: Config, seed_demo_captures: bool = True) -> None:
        conn = self.get_connection()
        now_iso = datetime.now(timezone.utc).isoformat()

        # 1. Seed merchant
        conn.execute(
            """
            INSERT OR IGNORE INTO merchants (id, name, created_at)
            VALUES (?, ?, ?)
            """,
            (config.merchant_id, "Acme Corporation", now_iso),
        )

        # 2. Seed judge user
        from backend.auth import hash_password
        pwd_hash = hash_password(config.judge_password)
        conn.execute(
            """
            INSERT OR IGNORE INTO merchant_users (id, merchant_id, username, password_hash, role, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            ("user_judge_default", config.merchant_id, config.judge_username, pwd_hash, "judge", now_iso),
        )

        # 3. Seed agent
        agent_key_hash = hashlib.sha256(config.agent_key.encode("utf-8")).hexdigest()
        conn.execute(
            """
            INSERT OR IGNORE INTO agents (id, merchant_id, name, api_key_hash, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (config.agent_id, config.merchant_id, "RefundAgent_1", agent_key_hash, "active", now_iso),
        )

        # 4. Seed policy v1
        conn.execute(
            """
            INSERT OR IGNORE INTO policies (id, version, merchant_id, max_auto_amount, max_escalate_amount, currency, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "pol_v1",
                "v1",
                config.merchant_id,
                str(config.max_auto_amount),
                str(config.max_escalate_amount),
                config.currency,
                now_iso,
            ),
        )

        # 5. Seed captures A, B, C of $300 each
        captures_to_seed = [
            ("CAP_A", "300.00"),
            ("CAP_B", "300.00"),
            ("CAP_C", "300.00"),
        ]
        # Demo captures only exist in the fake adapter; real sandbox captures are registered explicitly.
        for cap_id, orig_amt in (captures_to_seed if seed_demo_captures else []):
            conn.execute(
                """
                INSERT OR IGNORE INTO captures (id, merchant_id, original_amount, currency, refunded_amount, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (cap_id, config.merchant_id, orig_amt, "USD", "0.00", "COMPLETED", now_iso),
            )

    def rotate_seeded_credentials(self, config: Config) -> None:
        """Hosted mode: make the seeded agent key / judge password match the current env.

        seed_defaults uses INSERT OR IGNORE, so without this a persisted database would keep
        whatever credentials it was first created with (e.g. the public demo defaults).
        """
        from backend.auth import hash_password
        conn = self.get_connection()
        conn.execute(
            "UPDATE agents SET api_key_hash = ? WHERE id = ?",
            (hashlib.sha256(config.agent_key.encode("utf-8")).hexdigest(), config.agent_id),
        )
        conn.execute(
            "UPDATE merchant_users SET password_hash = ? WHERE merchant_id = ? AND username = ?",
            (hash_password(config.judge_password), config.merchant_id, config.judge_username),
        )
        conn.commit()

    def close(self) -> None:
        if hasattr(self._local, "conn") and self._local.conn is not None:
            self._local.conn.close()
            self._local.conn = None

