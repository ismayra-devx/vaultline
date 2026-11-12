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

    def seed_system_defaults(self, config: Config) -> None:
        """Seed baseline merchant and policy v1 records required by foreign keys and policy engine."""
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

        # 2. Seed policy v1
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
                "2026-01-01T00:00:00Z",
            ),
        )

    def seed_demo_captures(self, merchant_id: str) -> None:
        """Seed fake demo captures (CAP_A, CAP_B, CAP_C). Only permitted in fake/demo mode."""
        conn = self.get_connection()
        now_iso = datetime.now(timezone.utc).isoformat()
        captures_to_seed = [
            ("CAP_A", "300.00"),
            ("CAP_B", "300.00"),
            ("CAP_C", "300.00"),
        ]
        for cap_id, orig_amt in captures_to_seed:
            conn.execute(
                """
                INSERT OR IGNORE INTO captures (id, merchant_id, original_amount, currency, refunded_amount, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (cap_id, merchant_id, orig_amt, "USD", "0.00", "COMPLETED", now_iso),
            )

    def seed_credentials(
        self,
        config: Config,
        is_production: bool = False,
        is_real_paypal: bool = False,
        is_demo_mode: bool = False,
    ) -> None:
        """
        Identity-safe, atomic synchronization of merchant user and agent credentials.
        - Never silently replaces an existing merchant user's password or agent key.
        - Never modifies unrelated users or agents.
        - Only rotates known seeded default identities after verifying ownership, role, status and known default secret.
        - If custom credentials exist, verifies configured credentials match stored credentials; fails closed on mismatch.
        - Verifies configured agent key authenticates against intended active agent.
        - Entire migration is atomic: rolls back on any error.
        """
        from backend.auth import hash_password, verify_password, hash_agent_key
        from backend.config import KNOWN_INSECURE_PASSWORDS, KNOWN_INSECURE_AGENT_KEYS

        if not (config.agent_id or "").strip():
            raise RuntimeError(
                "Security initialization failed: VAULTLINE_AGENT_ID must be explicitly configured and non-empty. "
                "Selecting an arbitrary agent is strictly prohibited."
            )

        conn = self.get_connection()
        now_iso = datetime.now(timezone.utc).isoformat()
        new_pwd_hash = hash_password(config.judge_password)
        new_key_hash = hash_agent_key(config.agent_key)
        insecure_key_hashes = [hash_agent_key(k) for k in KNOWN_INSECURE_AGENT_KEYS]

        try:
            conn.execute("BEGIN TRANSACTION")

            # ---------------------------------------------------------
            # 1. Identity-safe Merchant User synchronization
            # ---------------------------------------------------------
            cursor = conn.execute(
                "SELECT id, merchant_id, username, password_hash, role FROM merchant_users WHERE username = ?",
                (config.judge_username,),
            )
            existing_user = cursor.fetchone()

            if existing_user:
                # User with configured username already exists in database
                if existing_user["merchant_id"] != config.merchant_id:
                    raise RuntimeError(
                        f"Configured judge username '{config.judge_username}' belongs to merchant "
                        f"'{existing_user['merchant_id']}', not '{config.merchant_id}'."
                    )
                if existing_user["role"] not in ("judge", "admin"):
                    raise RuntimeError(
                        f"Existing merchant user '{config.judge_username}' has role "
                        f"'{existing_user['role']}', but 'judge' or 'admin' is required."
                    )

                is_known_default = (
                    existing_user["id"] == "user_judge_default"
                    and any(verify_password(p, existing_user["password_hash"]) for p in KNOWN_INSECURE_PASSWORDS)
                )

                if is_known_default:
                    # Safe to rotate the known seeded default identity
                    conn.execute(
                        "UPDATE merchant_users SET password_hash = ? WHERE id = ?",
                        (new_pwd_hash, existing_user["id"]),
                    )
                else:
                    # Custom user: verify configured password matches stored credentials
                    if not verify_password(config.judge_password, existing_user["password_hash"]):
                        raise RuntimeError(
                            f"Configured credentials do not match stored credentials for existing "
                            f"merchant user '{config.judge_username}'. Password cannot be silently overwritten."
                        )
            else:
                # Username not found. Check if the legacy default judge user exists
                cursor = conn.execute("SELECT id, merchant_id, username, password_hash, role FROM merchant_users WHERE id = 'user_judge_default'")
                default_user = cursor.fetchone()
                if default_user:
                    # Only rotate if it is the verified known default identity
                    is_verified_default = (
                        default_user["merchant_id"] == config.merchant_id
                        and default_user["role"] == "judge"
                        and any(verify_password(p, default_user["password_hash"]) for p in KNOWN_INSECURE_PASSWORDS)
                    )
                    if is_verified_default:
                        conn.execute(
                            "UPDATE merchant_users SET username = ?, password_hash = ? WHERE id = 'user_judge_default'",
                            (config.judge_username, new_pwd_hash),
                        )
                    else:
                        raise RuntimeError(
                            "Cannot reassign user_judge_default: user is not in known default state."
                        )
                else:
                    # No existing user; insert initial judge record
                    conn.execute(
                        """
                        INSERT INTO merchant_users (id, merchant_id, username, password_hash, role, created_at)
                        VALUES (?, ?, ?, ?, 'judge', ?)
                        """,
                        ("user_judge_default", config.merchant_id, config.judge_username, new_pwd_hash, now_iso),
                    )

            # ---------------------------------------------------------
            # 2. Identity-safe Agent synchronization
            # ---------------------------------------------------------
            cursor = conn.execute("SELECT id, merchant_id, status, api_key_hash FROM agents WHERE id = ?", (config.agent_id,))
            existing_agent = cursor.fetchone()

            if existing_agent:
                if existing_agent["merchant_id"] != config.merchant_id:
                    raise RuntimeError(
                        f"Configured agent '{config.agent_id}' belongs to merchant "
                        f"'{existing_agent['merchant_id']}', not '{config.merchant_id}'."
                    )
                # Safeguard: Do not reactivate inactive or revoked agents
                if existing_agent["status"] != "active":
                    raise RuntimeError(
                        f"Configured agent '{config.agent_id}' is '{existing_agent['status']}'. "
                        "Inactive or revoked agents cannot be used or reactivated."
                    )

                is_known_default_agent = (
                    existing_agent["id"] == "agent_default"
                    and existing_agent["api_key_hash"] in insecure_key_hashes
                )

                if is_known_default_agent:
                    # Safe to rotate the known seeded default agent
                    conn.execute(
                        "UPDATE agents SET api_key_hash = ? WHERE id = ?",
                        (new_key_hash, existing_agent["id"]),
                    )
                else:
                    # Custom agent: verify configured key matches stored key
                    if existing_agent["api_key_hash"] != new_key_hash:
                        raise RuntimeError(
                            f"Configured agent key does not match stored key for existing agent "
                            f"'{config.agent_id}'. Stored agent key cannot be silently overwritten."
                        )
            else:
                # Agent ID not found. Ensure key is not already in use by another agent
                cursor = conn.execute("SELECT id, merchant_id, status FROM agents WHERE api_key_hash = ?", (new_key_hash,))
                conflicting_agent = cursor.fetchone()
                if conflicting_agent:
                    raise RuntimeError(
                        f"Configured agent key is already assigned to another agent '{conflicting_agent['id']}'."
                    )

                # Check if legacy default agent exists
                cursor = conn.execute("SELECT id, merchant_id, status, api_key_hash FROM agents WHERE id = 'agent_default'")
                default_agent = cursor.fetchone()
                if default_agent and config.agent_id == "agent_default":
                    if default_agent["status"] == "active" and default_agent["api_key_hash"] in insecure_key_hashes:
                        conn.execute(
                            "UPDATE agents SET api_key_hash = ? WHERE id = 'agent_default'",
                            (new_key_hash,),
                        )
                    else:
                        raise RuntimeError(
                            "Cannot rotate agent_default: agent is not in known default active state."
                        )
                else:
                    conn.execute(
                        """
                        INSERT INTO agents (id, merchant_id, name, api_key_hash, status, created_at)
                        VALUES (?, ?, 'RefundAgent_1', ?, 'active', ?)
                        """,
                        (config.agent_id, config.merchant_id, new_key_hash, now_iso),
                    )

            # Safeguard: Revoke any remaining active agents using known insecure/default keys
            for bad_hash in insecure_key_hashes:
                conn.execute(
                    "UPDATE agents SET status = 'revoked' WHERE api_key_hash = ? AND status = 'active' AND id != ?",
                    (bad_hash, config.agent_id),
                )

            # ---------------------------------------------------------
            # 3. Post-migration Authentication Verification
            # ---------------------------------------------------------
            cursor = conn.execute(
                "SELECT id, status FROM agents WHERE api_key_hash = ? AND status = 'active' AND merchant_id = ?",
                (new_key_hash, config.merchant_id),
            )
            active_auth_agent = cursor.fetchone()
            if not active_auth_agent or active_auth_agent["id"] != config.agent_id:
                raise RuntimeError(
                    f"Authentication verification failed: Configured agent key does not authenticate "
                    f"against the intended active agent '{config.agent_id}'."
                )

            # In production or real PayPal mode, verify no active accounts retain known defaults
            if is_production or is_real_paypal:
                for bad_hash in insecure_key_hashes:
                    cursor = conn.execute("SELECT id FROM agents WHERE api_key_hash = ? AND status = 'active'", (bad_hash,))
                    if cursor.fetchone():
                        raise RuntimeError(
                            "Security initialization failed: Active agent with known default key hash "
                            "remains in database."
                        )
                cursor = conn.execute("SELECT id, username, password_hash FROM merchant_users")
                for u in cursor.fetchall():
                    if any(verify_password(p, u["password_hash"]) for p in KNOWN_INSECURE_PASSWORDS):
                        raise RuntimeError(
                            f"Security initialization failed: Merchant user '{u['username']}' retains a known default password."
                        )

            conn.commit()
        except Exception:
            conn.rollback()
            raise

    def seed_defaults(
        self,
        config: Config,
        demo_mode: Optional[bool] = None,
        *,
        is_production: Optional[bool] = None,
        is_real_paypal: Optional[bool] = None,
    ) -> None:
        """Coordinating entrypoint preserving backwards-compatibility with test fixtures.

        Preserves legacy positional signature: `seed_defaults(config, demo_mode)`.
        Keyword arguments `is_production` and `is_real_paypal` allow explicit deployment checks.
        """
        import os
        if is_production is None:
            is_production = os.getenv("APP_ENV", "").strip().lower() in ("production", "prod")
        if is_real_paypal is None:
            is_real_paypal = os.getenv("VAULTLINE_USE_REAL_PAYPAL", "").strip().lower() in ("true", "1", "yes")
        if demo_mode is None:
            is_demo = (
                config.demo_mode
                or os.getenv("VAULTLINE_DEMO_MODE", "").strip().lower() in ("true", "1", "yes")
            )
            demo_mode = is_demo and not is_real_paypal and not is_production

        self.seed_system_defaults(config)
        self.seed_credentials(
            config,
            is_production=is_production,
            is_real_paypal=is_real_paypal,
            is_demo_mode=demo_mode,
        )
        if demo_mode:
            self.seed_demo_captures(config.merchant_id)

    def close(self) -> None:
        if hasattr(self._local, "conn") and self._local.conn is not None:
            self._local.conn.close()
            self._local.conn = None
