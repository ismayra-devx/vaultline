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

