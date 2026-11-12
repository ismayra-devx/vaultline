import os
os.environ.setdefault("VAULTLINE_USE_REAL_PAYPAL", "false")
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("VAULTLINE_JUDGE_USERNAME", "judge_test_runner")
os.environ.setdefault("VAULTLINE_JUDGE_PASSWORD", "secure_judge_test_pass_12345!")
os.environ.setdefault("VAULTLINE_AGENT_KEY", "test_agent_secret_key_123456789")
import tempfile
import pytest
from decimal import Decimal
from fastapi.testclient import TestClient

from backend.config import Config
from backend.db import Database
from backend.fake_adapter import FakePaypalAdapter
from backend.services import VaultlineService
from backend.main import create_app

@pytest.fixture
def temp_db_path():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    if os.path.exists(path):
        try:
            os.remove(path)
        except OSError:
            pass
    # Clean up SQLite WAL / SHM files if left
    for ext in ("-wal", "-shm"):
        wal_file = path + ext
        if os.path.exists(wal_file):
            try:
                os.remove(wal_file)
            except OSError:
                pass

@pytest.fixture
def test_config(temp_db_path):
    return Config(
        db_path=temp_db_path,
        merchant_id="merch_test_1",
        judge_username="judge_tester",
        judge_password="secure_judge_test_pass_123!",
        agent_id="agent_test_1",
        agent_key="test_agent_secret_key_789",
        demo_mode=True,
    )

@pytest.fixture
def test_db(test_config):
    db = Database(test_config.db_path)
    db.init_schema()
    db.seed_defaults(test_config)
    yield db
    db.close()

@pytest.fixture
def fake_adapter():
    adapter = FakePaypalAdapter()
    # Pre-seed captures matching DB seeds
    adapter.seed_capture("CAP_A", Decimal("300.00"), "USD", "COMPLETED")
    adapter.seed_capture("CAP_B", Decimal("300.00"), "USD", "COMPLETED")
    adapter.seed_capture("CAP_C", Decimal("300.00"), "USD", "COMPLETED")
    return adapter

@pytest.fixture
def service(test_db, fake_adapter, test_config):
    return VaultlineService(db=test_db, adapter=fake_adapter, config=test_config)

@pytest.fixture
def app(test_db, fake_adapter, test_config, service):
    return create_app(config=test_config, db=test_db, adapter=fake_adapter, service=service)

@pytest.fixture
def client(app):
    return TestClient(app)

@pytest.fixture
def agent_headers(test_config):
    return {"Authorization": f"Bearer {test_config.agent_key}"}

@pytest.fixture
def merchant_token(client, test_config):
    res = client.post(
        "/api/merchant/login",
        json={
            "username": test_config.judge_username,
            "password": test_config.judge_password,
        },
    )
    assert res.status_code == 200, f"Login failed: {res.text}"
    return res.json()["session_token"]

@pytest.fixture
def merchant_headers(merchant_token):
    return {"Authorization": f"Bearer {merchant_token}"}

