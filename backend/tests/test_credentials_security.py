import os
import subprocess
import sys
from decimal import Decimal
import pytest
from starlette.testclient import TestClient

from backend.config import (
    Config,
    KNOWN_INSECURE_PASSWORDS,
    KNOWN_INSECURE_AGENT_KEYS,
    KNOWN_INSECURE_USERNAMES,
    DEFAULT_DEMO_JUDGE_USERNAME,
    DEFAULT_DEMO_JUDGE_PASSWORD,
    DEFAULT_DEMO_AGENT_KEY,
)
from backend.db import Database
from backend.fake_adapter import FakePaypalAdapter
from backend.main import create_app
from backend.auth import hash_password, hash_agent_key, verify_password


# ---------------------------------------------------------------------------
# 1. Startup & Import-time Fail-closed Validation
# ---------------------------------------------------------------------------

def test_subprocess_import_production_fails_before_database_creation(tmp_path):
    """
    Subprocess test: Importing backend.main under production mode with missing/insecure credentials
    must fail closed at module level before creating or seeding the SQLite database.
    """
    db_file = tmp_path / "uncreated_production.db"
    assert not db_file.exists()

    env = os.environ.copy()
    env["APP_ENV"] = "production"
    env["VAULTLINE_USE_REAL_PAYPAL"] = "true"
    env["VAULTLINE_DB_PATH"] = str(db_file)
    env.pop("VAULTLINE_JUDGE_PASSWORD", None)
    env.pop("VAULTLINE_AGENT_KEY", None)
    env.pop("VAULTLINE_DEMO_MODE", None)

    cmd = [sys.executable, "-c", "import backend.main"]
    res = subprocess.run(cmd, env=env, capture_output=True, text=True)

    assert res.returncode != 0, f"Import should have failed, but succeeded. STDOUT: {res.stdout}"
    assert "Security initialization failed" in res.stderr
    assert "VAULTLINE_JUDGE_PASSWORD" in res.stderr or "VAULTLINE_AGENT_KEY" in res.stderr
    assert not db_file.exists(), "Database file must NOT be created when startup validation fails!"


def test_production_fails_closed_when_judge_password_missing(monkeypatch, tmp_path):
    """Production mode must reject missing VAULTLINE_JUDGE_PASSWORD before DB creation."""
    db_file = tmp_path / "uncreated.db"
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("VAULTLINE_USE_REAL_PAYPAL", "false")
    monkeypatch.setenv("VAULTLINE_JUDGE_USERNAME", "judge_prod_merchant")
    monkeypatch.setenv("VAULTLINE_AGENT_KEY", "prod_agent_secure_key_987654321")
    monkeypatch.delenv("VAULTLINE_JUDGE_PASSWORD", raising=False)
    monkeypatch.delenv("VAULTLINE_DEMO_MODE", raising=False)
    monkeypatch.setenv("VAULTLINE_DB_PATH", str(db_file))

    with pytest.raises(RuntimeError) as exc_info:
        create_app(adapter=FakePaypalAdapter())
    assert "VAULTLINE_JUDGE_PASSWORD must be explicitly configured" in str(exc_info.value)
    assert not db_file.exists(), "Database file must not be created on validation failure."


def test_production_fails_closed_when_agent_key_missing(monkeypatch, tmp_path):
    """Production mode must reject missing VAULTLINE_AGENT_KEY before DB creation."""
    db_file = tmp_path / "uncreated.db"
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("VAULTLINE_USE_REAL_PAYPAL", "false")
    monkeypatch.setenv("VAULTLINE_JUDGE_USERNAME", "judge_prod_merchant")
    monkeypatch.setenv("VAULTLINE_JUDGE_PASSWORD", "prod_judge_password_super_secure_123!")
    monkeypatch.delenv("VAULTLINE_AGENT_KEY", raising=False)
    monkeypatch.delenv("VAULTLINE_DEMO_MODE", raising=False)
    monkeypatch.setenv("VAULTLINE_DB_PATH", str(db_file))

    with pytest.raises(RuntimeError) as exc_info:
        create_app(adapter=FakePaypalAdapter())
    assert "VAULTLINE_AGENT_KEY must be explicitly configured" in str(exc_info.value)
    assert not db_file.exists(), "Database file must not be created on validation failure."


def test_production_fails_closed_when_judge_username_missing_or_blank(monkeypatch, tmp_path):
    """Production mode must reject missing or blank VAULTLINE_JUDGE_USERNAME before DB creation."""
    db_file = tmp_path / "uncreated.db"
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("VAULTLINE_USE_REAL_PAYPAL", "false")
    monkeypatch.delenv("VAULTLINE_JUDGE_USERNAME", raising=False)
    monkeypatch.setenv("VAULTLINE_JUDGE_PASSWORD", "prod_judge_password_super_secure_123!")
    monkeypatch.setenv("VAULTLINE_AGENT_KEY", "prod_agent_secure_key_987654321")
    monkeypatch.delenv("VAULTLINE_DEMO_MODE", raising=False)
    monkeypatch.setenv("VAULTLINE_DB_PATH", str(db_file))

    with pytest.raises(RuntimeError) as exc_info:
        create_app(adapter=FakePaypalAdapter())
    assert "VAULTLINE_JUDGE_USERNAME must be explicitly configured" in str(exc_info.value)
    assert not db_file.exists(), "Database file must not be created on validation failure."


def test_production_fails_closed_with_insecure_judge_username(monkeypatch, tmp_path):
    """Production mode must reject insecure placeholder usernames before DB creation."""
    db_file = tmp_path / "uncreated.db"
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("VAULTLINE_USE_REAL_PAYPAL", "false")
    monkeypatch.setenv("VAULTLINE_JUDGE_USERNAME", "admin")
    monkeypatch.setenv("VAULTLINE_JUDGE_PASSWORD", "prod_judge_password_super_secure_123!")
    monkeypatch.setenv("VAULTLINE_AGENT_KEY", "prod_agent_secure_key_987654321")
    monkeypatch.delenv("VAULTLINE_DEMO_MODE", raising=False)
    monkeypatch.setenv("VAULTLINE_DB_PATH", str(db_file))

    with pytest.raises(RuntimeError) as exc_info:
        create_app(adapter=FakePaypalAdapter())
    assert "cannot use insecure placeholder or default administrative name 'admin'" in str(exc_info.value)
    assert not db_file.exists(), "Database file must not be created on validation failure."


def test_production_fails_closed_with_known_default_judge_password(monkeypatch, tmp_path):
    """Production mode must reject known default password before DB creation."""
    db_file = tmp_path / "uncreated.db"
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("VAULTLINE_USE_REAL_PAYPAL", "false")
    monkeypatch.setenv("VAULTLINE_JUDGE_USERNAME", "judge_prod_merchant")
    monkeypatch.setenv("VAULTLINE_JUDGE_PASSWORD", "vaultline_judge_password_123!")
    monkeypatch.setenv("VAULTLINE_AGENT_KEY", "prod_agent_secure_key_987654321")
    monkeypatch.delenv("VAULTLINE_DEMO_MODE", raising=False)
    monkeypatch.setenv("VAULTLINE_DB_PATH", str(db_file))

    with pytest.raises(RuntimeError) as exc_info:
        create_app(adapter=FakePaypalAdapter())
    assert "matches a known insecure default" in str(exc_info.value)
    assert not db_file.exists(), "Database file must not be created on validation failure."


def test_production_fails_closed_with_known_default_agent_key(monkeypatch, tmp_path):
    """Production mode must reject known default agent key before DB creation."""
    db_file = tmp_path / "uncreated.db"
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("VAULTLINE_USE_REAL_PAYPAL", "false")
    monkeypatch.setenv("VAULTLINE_JUDGE_USERNAME", "judge_prod_merchant")
    monkeypatch.setenv("VAULTLINE_JUDGE_PASSWORD", "prod_judge_password_super_secure_123!")
    monkeypatch.setenv("VAULTLINE_AGENT_KEY", "vl_agent_secret_key_abc123")
    monkeypatch.delenv("VAULTLINE_DEMO_MODE", raising=False)
    monkeypatch.setenv("VAULTLINE_DB_PATH", str(db_file))

    with pytest.raises(RuntimeError) as exc_info:
        create_app(adapter=FakePaypalAdapter())
    assert "matches a known insecure default" in str(exc_info.value)
    assert not db_file.exists(), "Database file must not be created on validation failure."


def test_real_paypal_fails_closed_with_default_credentials(monkeypatch, tmp_path):
    """Real PayPal mode must fail closed even when APP_ENV is development if secrets are default."""
    db_file = tmp_path / "uncreated.db"
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("VAULTLINE_USE_REAL_PAYPAL", "true")
    monkeypatch.setenv("PAYPAL_CLIENT_ID", "test_id")
    monkeypatch.setenv("PAYPAL_CLIENT_SECRET", "test_sec")
    monkeypatch.setenv("PAYPAL_BASE_URL", "https://api-m.sandbox.paypal.com")
    monkeypatch.setenv("VAULTLINE_JUDGE_USERNAME", "judge_merchant")
    monkeypatch.setenv("VAULTLINE_JUDGE_PASSWORD", "vaultline_judge_password_123!")
    monkeypatch.setenv("VAULTLINE_AGENT_KEY", "vl_agent_secret_key_abc123")
    monkeypatch.delenv("VAULTLINE_DEMO_MODE", raising=False)
    monkeypatch.setenv("VAULTLINE_DB_PATH", str(db_file))

    with pytest.raises(RuntimeError) as exc_info:
        create_app()
    assert "Security initialization failed" in str(exc_info.value)
    assert not db_file.exists()


def test_hosted_deployment_rejects_fake_paypal_mode(monkeypatch, tmp_path):
    """Hosted hackathon/judge deployment must require real PayPal mode."""
    db_file = tmp_path / "uncreated.db"
    monkeypatch.setenv("VAULTLINE_DEPLOYMENT", "hosted")
    monkeypatch.setenv("VAULTLINE_USE_REAL_PAYPAL", "false")
    monkeypatch.setenv("VAULTLINE_DB_PATH", str(db_file))

    with pytest.raises(RuntimeError) as exc_info:
        create_app(adapter=FakePaypalAdapter())
    assert "Fake adapter mode is strictly prohibited in the hosted judge environment" in str(exc_info.value)
    assert not db_file.exists()


def test_contradictory_demo_mode_and_real_paypal_fails(monkeypatch, tmp_path):
    """Demo mode and real PayPal adapter are strictly contradictory."""
    db_file = tmp_path / "uncreated.db"
    monkeypatch.setenv("VAULTLINE_DEMO_MODE", "true")
    monkeypatch.setenv("VAULTLINE_USE_REAL_PAYPAL", "true")
    monkeypatch.setenv("VAULTLINE_DB_PATH", str(db_file))

    with pytest.raises(RuntimeError) as exc_info:
        create_app()
    assert "Contradictory configuration" in str(exc_info.value)
    assert not db_file.exists()


def test_contradictory_demo_mode_and_production_fails(monkeypatch, tmp_path):
    """Demo mode and production APP_ENV are strictly contradictory."""
    db_file = tmp_path / "uncreated.db"
    monkeypatch.setenv("VAULTLINE_DEMO_MODE", "true")
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("VAULTLINE_USE_REAL_PAYPAL", "false")
    monkeypatch.setenv("VAULTLINE_DB_PATH", str(db_file))

    with pytest.raises(RuntimeError) as exc_info:
        create_app(adapter=FakePaypalAdapter())
    assert "Contradictory configuration" in str(exc_info.value)
    assert not db_file.exists()


def test_unrecognized_app_env_fails_closed(monkeypatch, tmp_path):
    """Unrecognized APP_ENV value must fail startup immediately."""
    db_file = tmp_path / "uncreated.db"
    monkeypatch.setenv("APP_ENV", "unsupported_staging_env")
    monkeypatch.setenv("VAULTLINE_USE_REAL_PAYPAL", "false")
    monkeypatch.setenv("VAULTLINE_DB_PATH", str(db_file))

    with pytest.raises(RuntimeError) as exc_info:
        create_app(adapter=FakePaypalAdapter())
    assert "Invalid APP_ENV value: 'unsupported_staging_env'" in str(exc_info.value)
    assert not db_file.exists()


# ---------------------------------------------------------------------------
# 2. Demo Mode Isolation & Capture Seeding Rules
# ---------------------------------------------------------------------------

def test_missing_credentials_outside_explicit_demo_mode(monkeypatch, tmp_path):
    """Regression test: Missing credentials outside explicit demo mode must fail closed."""
    db_file = tmp_path / "uncreated.db"
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.delenv("VAULTLINE_DEMO_MODE", raising=False)
    monkeypatch.setenv("VAULTLINE_USE_REAL_PAYPAL", "false")
    monkeypatch.setenv("VAULTLINE_DB_PATH", str(db_file))
    monkeypatch.setenv("VAULTLINE_JUDGE_USERNAME", "judge_test")
    monkeypatch.delenv("VAULTLINE_JUDGE_PASSWORD", raising=False)
    monkeypatch.setenv("VAULTLINE_AGENT_KEY", "agent_key_test_123")

    with pytest.raises(RuntimeError) as exc_info:
        create_app(adapter=FakePaypalAdapter())
    assert "VAULTLINE_JUDGE_PASSWORD must be explicitly configured outside demo mode" in str(exc_info.value)
    assert not db_file.exists()


def test_development_mode_without_demo_mode_requires_credentials(monkeypatch, tmp_path):
    """Regression test: APP_ENV=development must not automatically enable demo credentials."""
    db_file = tmp_path / "uncreated.db"
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.delenv("VAULTLINE_DEMO_MODE", raising=False)
    monkeypatch.setenv("VAULTLINE_USE_REAL_PAYPAL", "false")
    monkeypatch.setenv("VAULTLINE_DB_PATH", str(db_file))
    monkeypatch.setenv("VAULTLINE_JUDGE_USERNAME", "judge_dev")
    monkeypatch.setenv("VAULTLINE_JUDGE_PASSWORD", "strong_judge_dev_password_123!")
    monkeypatch.delenv("VAULTLINE_AGENT_KEY", raising=False)

    with pytest.raises(RuntimeError) as exc_info:
        create_app(adapter=FakePaypalAdapter())
    assert "VAULTLINE_AGENT_KEY must be explicitly configured outside demo mode" in str(exc_info.value)
    assert not db_file.exists()


def test_explicit_demo_mode_with_fake_adapter(monkeypatch, tmp_path):
    """Regression test: Explicit demo mode with fake adapter succeeds and populates demo fixtures."""
    db_file = str(tmp_path / "explicit_demo.db")
    monkeypatch.setenv("VAULTLINE_DEMO_MODE", "true")
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("VAULTLINE_USE_REAL_PAYPAL", "false")
    monkeypatch.setenv("VAULTLINE_DB_PATH", db_file)
    monkeypatch.delenv("VAULTLINE_JUDGE_USERNAME", raising=False)
    monkeypatch.delenv("VAULTLINE_JUDGE_PASSWORD", raising=False)
    monkeypatch.delenv("VAULTLINE_AGENT_KEY", raising=False)

    app = create_app(adapter=FakePaypalAdapter())
    assert app.state.config.judge_username == DEFAULT_DEMO_JUDGE_USERNAME
    assert app.state.config.judge_password == DEFAULT_DEMO_JUDGE_PASSWORD
    assert app.state.config.agent_key == DEFAULT_DEMO_AGENT_KEY

    conn = app.state.db.get_connection()
    captures = [row["id"] for row in conn.execute("SELECT id FROM captures ORDER BY id").fetchall()]
    assert captures == ["CAP_A", "CAP_B", "CAP_C"]
    app.state.db.close()


def test_demo_captures_not_seeded_in_production(monkeypatch, tmp_path):
    """Production mode must NEVER seed demo captures CAP_A, CAP_B, CAP_C."""
    db_file = str(tmp_path / "prod.db")
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("VAULTLINE_USE_REAL_PAYPAL", "false")
    monkeypatch.setenv("VAULTLINE_JUDGE_USERNAME", "judge_prod_tester")
    monkeypatch.setenv("VAULTLINE_JUDGE_PASSWORD", "prod_judge_secure_password_999!")
    monkeypatch.setenv("VAULTLINE_AGENT_KEY", "prod_agent_secret_key_8888888888")
    monkeypatch.delenv("VAULTLINE_DEMO_MODE", raising=False)
    monkeypatch.setenv("VAULTLINE_DB_PATH", db_file)

    cfg = Config(
        db_path=db_file,
        merchant_id="merch_prod_1",
        judge_username="judge_prod_tester",
        judge_password="prod_judge_secure_password_999!",
        agent_id="agent_prod_1",
        agent_key="prod_agent_secret_key_8888888888",
    )
    app = create_app(config=cfg, adapter=FakePaypalAdapter())
    db = app.state.db

    conn = db.get_connection()
    cursor = conn.execute("SELECT id FROM captures")
    captures = cursor.fetchall()
    assert len(captures) == 0, f"Captures should be empty in production, found: {captures}"
    db.close()


def test_demo_captures_seeded_in_explicit_demo_mode(monkeypatch, tmp_path):
    """Explicit demo mode must seed demo captures CAP_A, CAP_B, CAP_C for developer experience."""
    db_file = str(tmp_path / "demo.db")
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("VAULTLINE_DEMO_MODE", "true")
    monkeypatch.setenv("VAULTLINE_USE_REAL_PAYPAL", "false")
    monkeypatch.setenv("VAULTLINE_DB_PATH", db_file)

    cfg = Config(
        db_path=db_file,
        merchant_id="merch_demo_1",
        judge_username="judge_demo",
        judge_password="demo_password_123!",
        agent_id="agent_demo_1",
        agent_key="demo_agent_key_456",
        demo_mode=True,
    )
    app = create_app(config=cfg, adapter=FakePaypalAdapter())
    db = app.state.db

    conn = db.get_connection()
    cursor = conn.execute("SELECT id FROM captures ORDER BY id")
    ids = [row["id"] for row in cursor.fetchall()]
    assert ids == ["CAP_A", "CAP_B", "CAP_C"]
    db.close()


# ---------------------------------------------------------------------------
# 3. Database Identity-Safe Credential Synchronization & Invariants
# ---------------------------------------------------------------------------

def test_existing_database_identity_safe_rotation(tmp_path):
    """
    Safeguard 1: Identity-safe rotation of known seeded default identities.
    Updates password and agent key without altering primary key IDs or reactivating revoked accounts.
    """
    db_file = str(tmp_path / "rot.db")
    db = Database(db_file)
    db.init_schema()

    # Pre-populate database with default demo credentials
    default_cfg = Config(
        db_path=db_file,
        merchant_id="merch_rot_1",
        judge_username="judge_merchant",
        judge_password="vaultline_judge_password_123!",
        agent_id="agent_default",
        agent_key="vl_agent_secret_key_abc123",
        demo_mode=True,
    )
    db.seed_system_defaults(default_cfg)
    db.seed_credentials(default_cfg, is_demo_mode=True)

    conn = db.get_connection()
    user_before = conn.execute("SELECT id, username, password_hash FROM merchant_users WHERE id = 'user_judge_default'").fetchone()
    agent_before = conn.execute("SELECT id, api_key_hash FROM agents WHERE id = 'agent_default'").fetchone()
    assert user_before is not None
    assert agent_before is not None
    initial_user_id = user_before["id"]
    initial_agent_id = agent_before["id"]

    # Now rotate credentials in config to strong custom secrets
    rotated_cfg = Config(
        db_path=db_file,
        merchant_id="merch_rot_1",
        judge_username="judge_merchant",
        judge_password="new_strong_judge_pass_999!",
        agent_id="agent_default",
        agent_key="new_strong_agent_key_777777777",
        demo_mode=False,
    )
    db.seed_credentials(rotated_cfg, is_demo_mode=False)

    user_after = conn.execute("SELECT id, username, password_hash FROM merchant_users WHERE id = 'user_judge_default'").fetchone()
    agent_after = conn.execute("SELECT id, api_key_hash FROM agents WHERE id = 'agent_default'").fetchone()

    # Identity-safe verification: IDs MUST NOT change
    assert user_after["id"] == initial_user_id
    assert agent_after["id"] == initial_agent_id

    # Password hash MUST be updated
    assert user_after["password_hash"] != user_before["password_hash"]
    assert verify_password("new_strong_judge_pass_999!", user_after["password_hash"])
    assert not verify_password("vaultline_judge_password_123!", user_after["password_hash"])

    # Agent key hash MUST be updated
    assert agent_after["api_key_hash"] != agent_before["api_key_hash"]
    assert agent_after["api_key_hash"] == hash_agent_key("new_strong_agent_key_777777777")
    db.close()


def test_existing_custom_judge_credentials_matching_and_mismatching(tmp_path):
    """
    Regression test: Existing custom judge credentials matching succeed;
    mismatching credentials fail closed and cannot be silently replaced.
    """
    db_file = str(tmp_path / "custom_judge.db")
    db = Database(db_file)
    db.init_schema()
    conn = db.get_connection()
    now_iso = "2026-11-12T00:00:00Z"

    conn.execute("INSERT INTO merchants (id, name, created_at) VALUES ('merch_custom', 'Acme', ?)", (now_iso,))
    custom_pwd_hash = hash_password("alice_original_secret_pass_123!")
    conn.execute(
        "INSERT INTO merchant_users (id, merchant_id, username, password_hash, role, created_at) VALUES ('user_alice', 'merch_custom', 'judge_alice', ?, 'judge', ?)",
        (custom_pwd_hash, now_iso),
    )
    agent_hash = hash_agent_key("key_alice_agent_999")
    conn.execute(
        "INSERT INTO agents (id, merchant_id, name, api_key_hash, status, created_at) VALUES ('agent_alice', 'merch_custom', 'AgentAlice', ?, 'active', ?)",
        (agent_hash, now_iso),
    )
    conn.commit()

    # 1. Matching custom credentials -> succeeds
    matching_cfg = Config(
        db_path=db_file,
        merchant_id="merch_custom",
        judge_username="judge_alice",
        judge_password="alice_original_secret_pass_123!",
        agent_id="agent_alice",
        agent_key="key_alice_agent_999",
        demo_mode=False,
    )
    db.seed_credentials(matching_cfg, is_demo_mode=False)

    # 2. Mismatching judge password -> fails closed
    mismatch_pwd_cfg = Config(
        db_path=db_file,
        merchant_id="merch_custom",
        judge_username="judge_alice",
        judge_password="wrong_password_attempt_999!",
        agent_id="agent_alice",
        agent_key="key_alice_agent_999",
        demo_mode=False,
    )
    with pytest.raises(RuntimeError) as exc_info:
        db.seed_credentials(mismatch_pwd_cfg, is_demo_mode=False)
    assert "Configured credentials do not match stored credentials for existing merchant user 'judge_alice'" in str(exc_info.value)

    # 3. Mismatching agent key -> fails closed
    mismatch_key_cfg = Config(
        db_path=db_file,
        merchant_id="merch_custom",
        judge_username="judge_alice",
        judge_password="alice_original_secret_pass_123!",
        agent_id="agent_alice",
        agent_key="wrong_agent_key_attempt_999",
        demo_mode=False,
    )
    with pytest.raises(RuntimeError) as exc_info:
        db.seed_credentials(mismatch_key_cfg, is_demo_mode=False)
    assert "Configured agent key does not match stored key for existing agent 'agent_alice'" in str(exc_info.value)
    db.close()


def test_username_conflict_with_unrelated_merchant_user(tmp_path):
    """Regression test: Username conflicts with unrelated merchant users must fail closed."""
    db_file = str(tmp_path / "conflict.db")
    db = Database(db_file)
    db.init_schema()
    conn = db.get_connection()
    now_iso = "2026-11-12T00:00:00Z"

    # Pre-seed merchant A and user
    conn.execute("INSERT INTO merchants (id, name, created_at) VALUES ('merch_A', 'Merchant A', ?)", (now_iso,))
    conn.execute("INSERT INTO merchants (id, name, created_at) VALUES ('merch_B', 'Merchant B', ?)", (now_iso,))
    conn.execute(
        "INSERT INTO merchant_users (id, merchant_id, username, password_hash, role, created_at) VALUES ('user_shared', 'merch_A', 'judge_bob', ?, 'judge', ?)",
        (hash_password("pass_bob_123!"), now_iso),
    )
    conn.commit()

    # Try to configure judge_bob for merchant B
    cfg = Config(
        db_path=db_file,
        merchant_id="merch_B",
        judge_username="judge_bob",
        judge_password="pass_bob_123!",
        agent_id="agent_b",
        agent_key="agent_key_b_123",
        demo_mode=False,
    )
    with pytest.raises(RuntimeError) as exc_info:
        db.seed_credentials(cfg, is_demo_mode=False)
    assert "Configured judge username 'judge_bob' belongs to merchant 'merch_A', not 'merch_B'" in str(exc_info.value)
    db.close()


def test_agent_conflicts_inactive_and_revoked(tmp_path):
    """Regression test: Inactive or revoked agents cannot be reactivated or used; key conflicts reject."""
    db_file = str(tmp_path / "agent_status.db")
    db = Database(db_file)
    db.init_schema()
    conn = db.get_connection()
    now_iso = "2026-11-12T00:00:00Z"

    conn.execute("INSERT INTO merchants (id, name, created_at) VALUES ('merch_1', 'Merchant 1', ?)", (now_iso,))
    conn.execute(
        "INSERT INTO merchant_users (id, merchant_id, username, password_hash, role, created_at) VALUES ('user_1', 'merch_1', 'judge_1', ?, 'judge', ?)",
        (hash_password("pass_judge_1!"), now_iso),
    )
    # 1. Inactive agent
    conn.execute(
        "INSERT INTO agents (id, merchant_id, name, api_key_hash, status, created_at) VALUES ('agent_inactive', 'merch_1', 'Inactive', ?, 'inactive', ?)",
        (hash_agent_key("key_inactive_123"), now_iso),
    )
    # 2. Revoked agent
    conn.execute(
        "INSERT INTO agents (id, merchant_id, name, api_key_hash, status, created_at) VALUES ('agent_revoked', 'merch_1', 'Revoked', ?, 'revoked', ?)",
        (hash_agent_key("key_revoked_123"), now_iso),
    )
    # 3. Active existing agent with key Carol
    conn.execute(
        "INSERT INTO agents (id, merchant_id, name, api_key_hash, status, created_at) VALUES ('agent_carol', 'merch_1', 'Carol', ?, 'active', ?)",
        (hash_agent_key("key_carol_unique"), now_iso),
    )
    conn.commit()

    # Attempt to use inactive agent
    cfg_inactive = Config(
        db_path=db_file,
        merchant_id="merch_1",
        judge_username="judge_1",
        judge_password="pass_judge_1!",
        agent_id="agent_inactive",
        agent_key="new_key_inactive_attempt",
        demo_mode=False,
    )
    with pytest.raises(RuntimeError) as exc_inactive:
        db.seed_credentials(cfg_inactive, is_demo_mode=False)
    assert "Configured agent 'agent_inactive' is 'inactive'" in str(exc_inactive.value)

    # Attempt to use revoked agent
    cfg_revoked = Config(
        db_path=db_file,
        merchant_id="merch_1",
        judge_username="judge_1",
        judge_password="pass_judge_1!",
        agent_id="agent_revoked",
        agent_key="new_key_revoked_attempt",
        demo_mode=False,
    )
    with pytest.raises(RuntimeError) as exc_revoked:
        db.seed_credentials(cfg_revoked, is_demo_mode=False)
    assert "Configured agent 'agent_revoked' is 'revoked'" in str(exc_revoked.value)

    # Attempt to assign key_carol_unique to a new agent agent_dave (key conflict)
    cfg_conflict = Config(
        db_path=db_file,
        merchant_id="merch_1",
        judge_username="judge_1",
        judge_password="pass_judge_1!",
        agent_id="agent_dave",
        agent_key="key_carol_unique",
        demo_mode=False,
    )
    with pytest.raises(RuntimeError) as exc_conflict:
        db.seed_credentials(cfg_conflict, is_demo_mode=False)
    assert "Configured agent key is already assigned to another agent 'agent_carol'" in str(exc_conflict.value)
    db.close()


def test_migration_rollback_after_simulated_failure(tmp_path):
    """Regression test: On any failure during credential migration, changes must be atomically rolled back."""
    db_file = str(tmp_path / "rollback.db")
    db = Database(db_file)
    db.init_schema()
    conn = db.get_connection()
    now_iso = "2026-11-12T00:00:00Z"

    # Pre-seed merchant and default judge
    conn.execute("INSERT INTO merchants (id, name, created_at) VALUES ('merch_rb', 'Acme', ?)", (now_iso,))
    orig_pwd_hash = hash_password("vaultline_judge_password_123!")
    conn.execute(
        "INSERT INTO merchant_users (id, merchant_id, username, password_hash, role, created_at) VALUES ('user_judge_default', 'merch_rb', 'judge_merchant', ?, 'judge', ?)",
        (orig_pwd_hash, now_iso),
    )
    # Pre-seed revoked agent to trigger failure in step 2
    conn.execute(
        "INSERT INTO agents (id, merchant_id, name, api_key_hash, status, created_at) VALUES ('agent_fail', 'merch_rb', 'FailAgent', ?, 'revoked', ?)",
        (hash_agent_key("key_fail_123"), now_iso),
    )
    conn.commit()

    # Configure new judge password (would succeed in step 1), but agent_id is agent_fail (will fail in step 2)
    cfg = Config(
        db_path=db_file,
        merchant_id="merch_rb",
        judge_username="judge_merchant",
        judge_password="brand_new_judge_pass_999!",
        agent_id="agent_fail",
        agent_key="some_key_456",
        demo_mode=False,
    )

    with pytest.raises(RuntimeError):
        db.seed_credentials(cfg, is_demo_mode=False)

    # Verify rollback: merchant_users password MUST NOT have been committed!
    user_row = conn.execute("SELECT password_hash FROM merchant_users WHERE id = 'user_judge_default'").fetchone()
    assert user_row["password_hash"] == orig_pwd_hash, "Transaction must rollback all changes on error!"
    db.close()


def test_no_changes_to_unrelated_users_or_agents(tmp_path):
    """Regression test: Unrelated users and agents must remain 100% untouched."""
    db_file = str(tmp_path / "unrelated.db")
    db = Database(db_file)
    db.init_schema()
    conn = db.get_connection()
    now_iso = "2026-11-12T00:00:00Z"

    conn.execute("INSERT INTO merchants (id, name, created_at) VALUES ('merch_1', 'Acme', ?)", (now_iso,))

    # Unrelated merchant user (uses a custom password)
    unrelated_pwd_hash = hash_password("unrelated_admin_password_xyz!")
    conn.execute(
        "INSERT INTO merchant_users (id, merchant_id, username, password_hash, role, created_at) VALUES ('user_unrelated_1', 'merch_1', 'admin_operations', ?, 'admin', ?)",
        (unrelated_pwd_hash, now_iso),
    )

    # Unrelated agent
    unrelated_key_hash = hash_agent_key("unrelated_agent_key_abc789")
    conn.execute(
        "INSERT INTO agents (id, merchant_id, name, api_key_hash, status, created_at) VALUES ('agent_unrelated_1', 'merch_1', 'POS_Agent', ?, 'active', ?)",
        (unrelated_key_hash, now_iso),
    )

    # Legacy default judge and default agent
    default_pwd_hash = hash_password("vaultline_judge_password_123!")
    conn.execute(
        "INSERT INTO merchant_users (id, merchant_id, username, password_hash, role, created_at) VALUES ('user_judge_default', 'merch_1', 'judge_merchant', ?, 'judge', ?)",
        (default_pwd_hash, now_iso),
    )
    default_key_hash = hash_agent_key("vl_agent_secret_key_abc123")
    conn.execute(
        "INSERT INTO agents (id, merchant_id, name, api_key_hash, status, created_at) VALUES ('agent_default', 'merch_1', 'DefaultAgent', ?, 'active', ?)",
        (default_key_hash, now_iso),
    )
    conn.commit()

    # Seed with new configured credentials for judge_merchant and agent_default
    cfg = Config(
        db_path=db_file,
        merchant_id="merch_1",
        judge_username="judge_merchant",
        judge_password="brand_new_judge_pass_555!",
        agent_id="agent_default",
        agent_key="brand_new_agent_key_666",
        demo_mode=False,
    )
    db.seed_credentials(cfg, is_demo_mode=False)

    # Unrelated user MUST NOT be changed or disabled
    u_row = conn.execute("SELECT password_hash, role FROM merchant_users WHERE id = 'user_unrelated_1'").fetchone()
    assert u_row["password_hash"] == unrelated_pwd_hash
    assert u_row["role"] == "admin"

    # Unrelated agent MUST remain active with identical key hash
    a_row = conn.execute("SELECT status, api_key_hash FROM agents WHERE id = 'agent_unrelated_1'").fetchone()
    assert a_row["status"] == "active"
    assert a_row["api_key_hash"] == unrelated_key_hash

    db.close()


def test_existing_database_revokes_legacy_default_agent_key_and_preserves_revoked_status(tmp_path):
    """
    Safeguard 1: Scan for known default agent keys, revoke them, and NEVER reactivate revoked agents.
    """
    db_file = str(tmp_path / "rev.db")
    db = Database(db_file)
    db.init_schema()

    conn = db.get_connection()
    now_iso = "2026-11-12T00:00:00Z"
    # Insert merchant
    conn.execute("INSERT INTO merchants (id, name, created_at) VALUES ('merch_1', 'Acme', ?)", (now_iso,))

    # Insert an agent using the known legacy default key
    legacy_default_hash = hash_agent_key("vl_agent_secret_key_abc123")
    conn.execute(
        "INSERT INTO agents (id, merchant_id, name, api_key_hash, status, created_at) VALUES ('agent_legacy', 'merch_1', 'Legacy', ?, 'active', ?)",
        (legacy_default_hash, now_iso),
    )

    # Insert a revoked custom agent (must NOT be reactivated)
    revoked_hash = hash_agent_key("revoked_key_123")
    conn.execute(
        "INSERT INTO agents (id, merchant_id, name, api_key_hash, status, created_at) VALUES ('agent_revoked', 'merch_1', 'Revoked', ?, 'revoked', ?)",
        (revoked_hash, now_iso),
    )

    # Insert an unrelated active custom agent (must NOT be overwritten)
    unrelated_hash = hash_agent_key("unrelated_custom_key_456")
    conn.execute(
        "INSERT INTO agents (id, merchant_id, name, api_key_hash, status, created_at) VALUES ('agent_unrelated', 'merch_1', 'Unrelated', ?, 'active', ?)",
        (unrelated_hash, now_iso),
    )
    conn.commit()

    # Now seed with new configured credentials for a new active agent
    cfg = Config(
        db_path=db_file,
        merchant_id="merch_1",
        judge_username="judge_tester",
        judge_password="secure_password_xyz123!",
        agent_id="agent_new_active",
        agent_key="new_valid_key_789",
        demo_mode=False,
    )
    db.seed_credentials(cfg, is_demo_mode=False)

    # 1. Legacy default agent MUST be revoked
    row_legacy = conn.execute("SELECT status FROM agents WHERE id = 'agent_legacy'").fetchone()
    assert row_legacy["status"] == "revoked"

    # 2. Revoked agent MUST remain revoked (not reactivated)
    row_revoked = conn.execute("SELECT status FROM agents WHERE id = 'agent_revoked'").fetchone()
    assert row_revoked["status"] == "revoked"

    # 3. Unrelated active custom agent MUST remain active and unchanged
    row_unrelated = conn.execute("SELECT status, api_key_hash FROM agents WHERE id = 'agent_unrelated'").fetchone()
    assert row_unrelated["status"] == "active"
    assert row_unrelated["api_key_hash"] == unrelated_hash

    # 4. New active agent is created and active
    row_new = conn.execute("SELECT status, api_key_hash FROM agents WHERE id = 'agent_new_active'").fetchone()
    assert row_new["status"] == "active"
    assert row_new["api_key_hash"] == hash_agent_key("new_valid_key_789")

    db.close()


def test_missing_agent_id_fails_closed_without_selecting_arbitrary_agent(tmp_path):
    """Point 3: Configured agent ID must be non-empty and identified unambiguously; arbitrary selection is blocked."""
    db_file = str(tmp_path / "empty_agent.db")
    db = Database(db_file)
    db.init_schema()

    cfg = Config(
        db_path=db_file,
        merchant_id="merch_1",
        judge_username="judge_tester",
        judge_password="secure_password_xyz123!",
        agent_id="",
        agent_key="new_valid_key_789",
        demo_mode=False,
    )
    with pytest.raises(RuntimeError) as exc_info:
        cfg.validate_credentials(is_demo_mode=False)
    assert "VAULTLINE_AGENT_ID must be explicitly configured and non-empty" in str(exc_info.value)

    with pytest.raises(RuntimeError) as exc_db:
        db.seed_credentials(cfg, is_demo_mode=False)
    assert "VAULTLINE_AGENT_ID must be explicitly configured and non-empty" in str(exc_db.value)
    db.close()


def test_app_env_test_or_dev_does_not_enable_demo_mode_or_seed_captures(monkeypatch, tmp_path):
    """Point 1: APP_ENV=development, dev, or test must NOT automatically enable demo mode or seed demo captures."""
    for env_name in ("development", "dev", "test"):
        db_file = str(tmp_path / f"test_{env_name}.db")
        monkeypatch.setenv("APP_ENV", env_name)
        monkeypatch.delenv("VAULTLINE_DEMO_MODE", raising=False)
        monkeypatch.setenv("VAULTLINE_USE_REAL_PAYPAL", "false")
        monkeypatch.setenv("VAULTLINE_DB_PATH", db_file)
        monkeypatch.setenv("VAULTLINE_JUDGE_USERNAME", "judge_env_tester")
        monkeypatch.setenv("VAULTLINE_JUDGE_PASSWORD", "strong_password_env_12345!")
        monkeypatch.setenv("VAULTLINE_AGENT_KEY", "strong_agent_key_env_67890!")

        app = create_app(adapter=FakePaypalAdapter())
        assert app.state.config.demo_mode is False

        conn = app.state.db.get_connection()
        captures = conn.execute("SELECT id FROM captures").fetchall()
        assert len(captures) == 0, f"Demo captures must not be seeded under APP_ENV={env_name} without VAULTLINE_DEMO_MODE=true"
        app.state.db.close()


def test_production_startup_fails_if_unrelated_merchant_user_retains_known_default_password(monkeypatch, tmp_path):
    """Production startup must fail closed if any existing merchant user retains a known insecure default password."""
    db_file = str(tmp_path / "prod_insecure_user.db")
    db = Database(db_file)
    db.init_schema()

    conn = db.get_connection()
    now_iso = "2026-11-12T00:00:00Z"
    # Seed an unrelated merchant and an unrelated merchant user with a known insecure default password
    conn.execute("INSERT INTO merchants (id, name, created_at) VALUES ('merch_unrelated', 'Other Corp', ?)", (now_iso,))
    insecure_pwd_hash = hash_password(DEFAULT_DEMO_JUDGE_PASSWORD)
    conn.execute(
        """
        INSERT INTO merchant_users (id, merchant_id, username, password_hash, role, created_at)
        VALUES ('user_unrelated_admin', 'merch_unrelated', 'other_admin', ?, 'admin', ?)
        """,
        (insecure_pwd_hash, now_iso),
    )
    conn.commit()
    db.close()

    # Now attempt production startup via create_app with clean, strong primary credentials
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("VAULTLINE_USE_REAL_PAYPAL", "false")
    monkeypatch.delenv("VAULTLINE_DEMO_MODE", raising=False)
    monkeypatch.setenv("VAULTLINE_DB_PATH", db_file)
    monkeypatch.setenv("VAULTLINE_JUDGE_USERNAME", "judge_prod_primary")
    monkeypatch.setenv("VAULTLINE_JUDGE_PASSWORD", "strong_primary_judge_pass_99999!")
    monkeypatch.setenv("VAULTLINE_AGENT_KEY", "strong_primary_agent_key_99999!")

    cfg = Config(
        db_path=db_file,
        merchant_id="merch_prod_primary",
        judge_username="judge_prod_primary",
        judge_password="strong_primary_judge_pass_99999!",
        agent_id="agent_prod_primary",
        agent_key="strong_primary_agent_key_99999!",
        demo_mode=False,
    )

    with pytest.raises(RuntimeError) as exc_info:
        create_app(config=cfg, adapter=FakePaypalAdapter())
    assert "Security initialization failed: Merchant user 'other_admin' retains a known default password." in str(exc_info.value)


def test_production_startup_preserves_custom_unrelated_merchant_user(monkeypatch, tmp_path):
    """Production startup must succeed and leave unrelated custom merchant accounts completely untouched."""
    db_file = str(tmp_path / "prod_secure_custom_user.db")
    db = Database(db_file)
    db.init_schema()

    conn = db.get_connection()
    now_iso = "2026-11-12T00:00:00Z"
    conn.execute("INSERT INTO merchants (id, name, created_at) VALUES ('merch_unrelated', 'Other Corp', ?)", (now_iso,))
    custom_pwd_hash = hash_password("unique_strong_custom_password_xyz987!")
    conn.execute(
        """
        INSERT INTO merchant_users (id, merchant_id, username, password_hash, role, created_at)
        VALUES ('user_unrelated_custom', 'merch_unrelated', 'custom_user_1', ?, 'admin', ?)
        """,
        (custom_pwd_hash, now_iso),
    )
    conn.commit()
    db.close()

    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("VAULTLINE_USE_REAL_PAYPAL", "false")
    monkeypatch.delenv("VAULTLINE_DEMO_MODE", raising=False)
    monkeypatch.setenv("VAULTLINE_DB_PATH", db_file)
    monkeypatch.setenv("VAULTLINE_JUDGE_USERNAME", "judge_prod_primary")
    monkeypatch.setenv("VAULTLINE_JUDGE_PASSWORD", "strong_primary_judge_pass_99999!")
    monkeypatch.setenv("VAULTLINE_AGENT_KEY", "strong_primary_agent_key_99999!")

    cfg = Config(
        db_path=db_file,
        merchant_id="merch_prod_primary",
        judge_username="judge_prod_primary",
        judge_password="strong_primary_judge_pass_99999!",
        agent_id="agent_prod_primary",
        agent_key="strong_primary_agent_key_99999!",
        demo_mode=False,
    )

    app = create_app(config=cfg, adapter=FakePaypalAdapter())
    conn = app.state.db.get_connection()
    user_row = conn.execute("SELECT id, username, password_hash, role FROM merchant_users WHERE id = 'user_unrelated_custom'").fetchone()
    assert user_row is not None
    assert user_row["username"] == "custom_user_1"
    assert user_row["password_hash"] == custom_pwd_hash
    assert user_row["role"] == "admin"
    app.state.db.close()
