import secrets
from decimal import Decimal
from typing import Any, Dict, Optional
from backend.adapter_interface import PaypalAdapter, CaptureResult, RefundResult

class FakePaypalAdapter(PaypalAdapter):
    """
    Test double for PayPal API interactions.
    Provides configurable behaviors, failure modes, timeouts,
    and strict idempotency by paypal_request_id / request_id.
    Implements both PaypalAdapter ABC and PayPalAdapter Protocol.
    """

    def __init__(self):
        self.money_call_count = 0
        self.captures: Dict[str, CaptureResult] = {}
        self.refunds_by_request_id: Dict[str, RefundResult] = {}
        self.refunds_by_id: Dict[str, RefundResult] = {}
        self.configured_result: str = "COMPLETED"
        self.should_timeout: bool = False

    def seed_capture(
        self,
        capture_id: str,
        amount: Decimal,
        currency: str = "USD",
        status: str = "COMPLETED",
    ) -> None:
        self.captures[capture_id] = CaptureResult(
            capture_id=capture_id,
            status=status,
            amount=amount,
            currency=currency,
        )

    def set_timeout(self, enabled: bool) -> None:
        self.should_timeout = enabled

    def set_configured_result(self, result_status: str) -> None:
        self.configured_result = result_status

    def get_capture(self, capture_id: str) -> CaptureResult:
        if capture_id in self.captures:
            return self.captures[capture_id]
        return CaptureResult(
            capture_id=capture_id,
            status="COMPLETED",
            amount=Decimal("300.00"),
            currency="USD",
        )

    def refund_capture(
        self,
        paypal_request_id: Optional[str] = None,
        capture_id: Optional[str] = None,
        amount: Optional[Decimal] = None,
        currency: str = "USD",
        request_id: Optional[str] = None,
        **kwargs,
    ) -> RefundResult:
        req_id = paypal_request_id or request_id or kwargs.get("request_id")
        if not req_id:
            raise ValueError("paypal_request_id / request_id must be provided.")
        if capture_id is None:
            raise ValueError("capture_id must be provided.")
        if amount is None:
            raise ValueError("amount must be provided.")

        # Idempotency check: if already processed with this request_id, return cached response
        if req_id in self.refunds_by_request_id:
            return self.refunds_by_request_id[req_id]

        # Count genuine money-moving calls
        self.money_call_count += 1

        if self.should_timeout:
            raise TimeoutError("PayPal API request timed out after waiting for upstream response.")

        refund_id = f"REF_{secrets.token_hex(8).upper()}"
        result = RefundResult(
            refund_id=refund_id,
            capture_id=capture_id,
            status=self.configured_result,
            amount=amount,
            currency=currency,
            paypal_debug_id=None,
        )

        # Cache for idempotency
        self.refunds_by_request_id[req_id] = result
        self.refunds_by_id[refund_id] = result
        return result

    def get_refund(self, refund_id: str) -> RefundResult:
        if refund_id in self.refunds_by_id:
            return self.refunds_by_id[refund_id]
        return RefundResult(
            refund_id=refund_id,
            capture_id=None,
            status=self.configured_result,
            amount=Decimal("0.00"),
            currency="USD",
            paypal_debug_id=None,
        )

    def close(self) -> None:
        """No-op cleanup for in-memory test double."""
        pass
