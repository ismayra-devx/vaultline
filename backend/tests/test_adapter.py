from decimal import Decimal
import pytest
from backend.fake_adapter import FakePaypalAdapter

def test_fake_adapter_basic_refund():
    adapter = FakePaypalAdapter()
    adapter.seed_capture("CAP_A", Decimal("300.00"), "USD", "COMPLETED")

    result = adapter.refund_capture(
        paypal_request_id="pp_req_1",
        capture_id="CAP_A",
        amount=Decimal("10.00"),
        currency="USD",
    )
    assert result["status"] == "COMPLETED"
    assert result["id"].startswith("REF_")
    assert adapter.money_call_count == 1

def test_fake_adapter_idempotency_by_request_id():
    adapter = FakePaypalAdapter()
    adapter.seed_capture("CAP_A", Decimal("300.00"), "USD", "COMPLETED")

    # Call 1
    res1 = adapter.refund_capture(
        paypal_request_id="pp_req_same_id",
        capture_id="CAP_A",
        amount=Decimal("10.00"),
        currency="USD",
    )
    assert adapter.money_call_count == 1

    # Call 2 with identical request ID (e.g. retry / reconcile)
    res2 = adapter.refund_capture(
        paypal_request_id="pp_req_same_id",
        capture_id="CAP_A",
        amount=Decimal("10.00"),
        currency="USD",
    )
    # Result must be identical
    assert res1["id"] == res2["id"]
    # Money call count MUST NOT increment (idempotent replay)
    assert adapter.money_call_count == 1

def test_fake_adapter_timeout_and_reconcile():
    adapter = FakePaypalAdapter()
    adapter.seed_capture("CAP_A", Decimal("300.00"), "USD", "COMPLETED")

    # Configure timeout
    adapter.set_timeout(True)

    with pytest.raises(TimeoutError):
        adapter.refund_capture(
            paypal_request_id="pp_req_timeout_1",
            capture_id="CAP_A",
            amount=Decimal("10.00"),
            currency="USD",
        )
    # The attempt occurred
    assert adapter.money_call_count == 1

    # Now clear timeout and reconcile with the SAME paypal_request_id
    adapter.set_timeout(False)
    res = adapter.refund_capture(
        paypal_request_id="pp_req_timeout_1",
        capture_id="CAP_A",
        amount=Decimal("10.00"),
        currency="USD",
    )
    # Refund is now returned
    assert res["status"] == "COMPLETED"

def test_fake_adapter_non_completed_statuses():
    adapter = FakePaypalAdapter()
    adapter.seed_capture("CAP_A", Decimal("300.00"), "USD", "COMPLETED")

    # PENDING status
    adapter.set_configured_result("PENDING")
    res_pending = adapter.refund_capture(
        paypal_request_id="pp_req_p1",
        capture_id="CAP_A",
        amount=Decimal("10.00"),
        currency="USD",
    )
    assert res_pending["status"] == "PENDING"

    # FAILED status
    adapter.set_configured_result("FAILED")
    res_failed = adapter.refund_capture(
        paypal_request_id="pp_req_f1",
        capture_id="CAP_A",
        amount=Decimal("10.00"),
        currency="USD",
    )
    assert res_failed["status"] == "FAILED"

def test_real_paypal_mode_fails_closed_on_import_error(monkeypatch):
    """When VAULTLINE_USE_REAL_PAYPAL=true, startup MUST fail if adapter cannot be imported, and FakePaypalAdapter is never instantiated."""
    import sys
    from backend.main import create_app

    monkeypatch.setenv("VAULTLINE_USE_REAL_PAYPAL", "true")
    monkeypatch.setitem(sys.modules, "backend.paypal.adapter", None)
    monkeypatch.setitem(sys.modules, "backend.paypal", None)

    fake_instantiated = []
    orig_init = FakePaypalAdapter.__init__
    def tracking_init(self, *args, **kwargs):
        fake_instantiated.append(True)
        return orig_init(self, *args, **kwargs)
    monkeypatch.setattr(FakePaypalAdapter, "__init__", tracking_init)

    with pytest.raises(RuntimeError) as exc_info:
        create_app()
    assert "Real PayPal mode requested" in str(exc_info.value)
    assert "failed to import PayPalSandboxAdapter" in str(exc_info.value)
    assert len(fake_instantiated) == 0, "FakePaypalAdapter must NEVER be instantiated when import fails!"

def test_real_paypal_mode_fails_closed_on_from_env_error(monkeypatch):
    """When VAULTLINE_USE_REAL_PAYPAL=true, startup MUST fail if from_env() fails, and FakePaypalAdapter is never instantiated."""
    import sys
    import types
    from backend.main import create_app

    monkeypatch.setenv("VAULTLINE_USE_REAL_PAYPAL", "true")

    dummy_module = types.ModuleType("backend.paypal.adapter")
    class BrokenSandboxAdapter:
        @classmethod
        def from_env(cls):
            raise ValueError("Simulated PayPal credentials error: missing PAYPAL_CLIENT_ID")
    dummy_module.PayPalSandboxAdapter = BrokenSandboxAdapter
    monkeypatch.setitem(sys.modules, "backend.paypal.adapter", dummy_module)

    fake_instantiated = []
    orig_init = FakePaypalAdapter.__init__
    def tracking_init(self, *args, **kwargs):
        fake_instantiated.append(True)
        return orig_init(self, *args, **kwargs)
    monkeypatch.setattr(FakePaypalAdapter, "__init__", tracking_init)

    with pytest.raises(RuntimeError) as exc_info:
        create_app()
    assert "Real PayPal mode requested" in str(exc_info.value)
    assert "PayPalSandboxAdapter.from_env() initialization failed" in str(exc_info.value)
    assert "Simulated PayPal credentials error" in str(exc_info.value)
    assert len(fake_instantiated) == 0, "FakePaypalAdapter must NEVER be instantiated when from_env() fails!"

def test_explicit_fake_mode(monkeypatch):
    """VAULTLINE_USE_REAL_PAYPAL=false explicitly instantiates FakePaypalAdapter."""
    from backend.main import create_app
    monkeypatch.setenv("VAULTLINE_USE_REAL_PAYPAL", "false")
    app = create_app()
    assert isinstance(app.state.adapter, FakePaypalAdapter)

def test_create_app_with_real_paypal_mode_success(monkeypatch):
    """When VAULTLINE_USE_REAL_PAYPAL=true and PayPal env vars are valid, create_app instantiates PayPalSandboxAdapter."""
    from backend.main import create_app
    from backend.paypal.adapter import PayPalSandboxAdapter

    monkeypatch.setenv("VAULTLINE_USE_REAL_PAYPAL", "true")
    monkeypatch.setenv("PAYPAL_CLIENT_ID", "test_client_id")
    monkeypatch.setenv("PAYPAL_CLIENT_SECRET", "test_client_secret")
    monkeypatch.setenv("PAYPAL_BASE_URL", "https://api-m.sandbox.paypal.com")

    app = create_app()
    assert isinstance(app.state.adapter, PayPalSandboxAdapter)
    app.state.adapter.close()

def test_missing_or_invalid_paypal_mode_fails_startup(monkeypatch):
    """Missing or unrecognized VAULTLINE_USE_REAL_PAYPAL values must fail startup."""
    from backend.main import create_app

    # Missing
    monkeypatch.delenv("VAULTLINE_USE_REAL_PAYPAL", raising=False)
    with pytest.raises(RuntimeError) as exc_missing:
        create_app()
    assert "must be explicitly configured" in str(exc_missing.value)

    # Invalid
    monkeypatch.setenv("VAULTLINE_USE_REAL_PAYPAL", "not_a_valid_boolean")
    with pytest.raises(RuntimeError) as exc_invalid:
        create_app()
    assert "Invalid VAULTLINE_USE_REAL_PAYPAL value: 'not_a_valid_boolean'" in str(exc_invalid.value)

def test_reconciliation_forwards_exact_persisted_paypal_request_id(test_config, temp_db_path):
    """Reconciliation must read the persisted paypal_request_id and forward it exactly to the adapter."""
    from backend.db import Database
    from backend.services import VaultlineService

    db = Database(temp_db_path)
    db.init_schema()
    db.seed_defaults(test_config)

    forwarded_ids = []

    class SpyAdapter(FakePaypalAdapter):
        def refund_capture(self, paypal_request_id, capture_id, amount, currency="USD", **kwargs):
            forwarded_ids.append(paypal_request_id)
            if self.should_timeout:
                raise TimeoutError("Simulated PayPal gateway timeout")
            return super().refund_capture(paypal_request_id, capture_id, amount, currency, **kwargs)

    adapter = SpyAdapter()
    adapter.seed_capture("CAP_A", Decimal("300.00"), "USD", "COMPLETED")
    adapter.set_timeout(True)

    service = VaultlineService(db=db, adapter=adapter, config=test_config)

    # Step 1: Initial auto-executed request times out, leaving status UNKNOWN
    req = service.create_refund_request(
        agent_id=test_config.agent_id,
        capture_id="CAP_A",
        amount=Decimal("5.00"),
        currency="USD",
        action="refund",
    )
    assert req.execution_status == "UNKNOWN"
    assert len(forwarded_ids) == 1
    initial_forwarded_id = forwarded_ids[0]

    # Verify ID was persisted in executions table
    conn = db.get_connection()
    row = conn.execute("SELECT paypal_request_id, status FROM executions WHERE request_id = ?", (req.request_id,)).fetchone()
    assert row is not None
    persisted_id = row["paypal_request_id"]
    assert persisted_id == initial_forwarded_id
    assert row["status"] == "UNKNOWN"

    # Step 2: Clear timeout and trigger reconciliation
    adapter.set_timeout(False)
    reconcile_res = service.reconcile_request(req.request_id)

    assert reconcile_res.execution_status == "COMPLETED"
    assert len(forwarded_ids) == 2
    reconcile_forwarded_id = forwarded_ids[1]

    # Assert that reconciliation forwarded the EXACT persisted paypal_request_id, NOT a new ID
    assert reconcile_forwarded_id == persisted_id
    assert reconcile_forwarded_id == initial_forwarded_id

    db.close()

def test_adapter_lifecycle_close_called():
    """Verify that adapter.close() is safely supported and called during app lifecycle."""
    from backend.main import create_app
    closed = []

    class MockAdapter(FakePaypalAdapter):
        def close(self):
            closed.append(True)

    mock_adapter = MockAdapter()
    app = create_app(adapter=mock_adapter)

    import asyncio
    async def run_lifespan():
        async with app.router.lifespan_context(app):
            pass

    asyncio.run(run_lifespan())
    assert len(closed) == 1


