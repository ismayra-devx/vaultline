from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Dict, Optional, Protocol, runtime_checkable

@dataclass(frozen=True)
class CaptureResult:
    """Normalized PayPal capture result."""
    capture_id: str
    status: str
    amount: Decimal
    currency: str

    def __getitem__(self, item: str) -> Any:
        if item == "id":
            return self.capture_id
        if item == "amount":
            return {"value": str(self.amount), "currency_code": self.currency}
        return getattr(self, item)

    def get(self, item: str, default: Any = None) -> Any:
        try:
            return self[item]
        except AttributeError:
            return default

@dataclass(frozen=True)
class RefundResult:
    """Normalized PayPal refund result."""
    refund_id: str
    capture_id: Optional[str]
    status: str
    amount: Decimal
    currency: str
    paypal_debug_id: Optional[str] = None

    def __getitem__(self, item: str) -> Any:
        if item == "id":
            return self.refund_id
        return getattr(self, item)

    def get(self, item: str, default: Any = None) -> Any:
        try:
            return self[item]
        except AttributeError:
            return default

class PaypalAdapter(ABC):
    """
    Canonical abstract base class for all PayPal communication in Vaultline.
    Synchronous execution contract for services.py, production adapter, and test doubles.
    """

    @abstractmethod
    def get_capture(self, capture_id: str) -> CaptureResult:
        """Fetches capture details from PayPal."""
        pass

    @abstractmethod
    def refund_capture(
        self,
        paypal_request_id: str,
        capture_id: str,
        amount: Decimal,
        currency: str = "USD",
        **kwargs,
    ) -> RefundResult:
        """
        Executes a capture refund using an idempotent paypal_request_id.
        Returns normalized RefundResult.
        """
        pass

    @abstractmethod
    def get_refund(self, refund_id: str) -> RefundResult:
        """Fetches status of an existing refund."""
        pass

# Deprecated for MVP: Aliased directly to PaypalAdapter to avoid competing contracts.
# All production adapters and test doubles must implement PaypalAdapter.
PayPalAdapter = PaypalAdapter
