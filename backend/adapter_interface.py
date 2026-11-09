from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Dict, Optional, Protocol, runtime_checkable

@dataclass(frozen=True)
class CaptureResult:
    """Normalized PayPal capture result (v2.0 contract)."""
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
    """Normalized PayPal refund result (v2.0 contract)."""
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

@runtime_checkable
class PayPalAdapter(Protocol):
    """
    Protocol definition for PayPal adapter (v2.0 architecture contract).
    Used by P1 (Ismayra) and P2 (Ankit) for dependency injection.
    """
    async def get_capture(
        self,
        capture_id: str,
    ) -> CaptureResult:
        ...

    async def refund_capture(
        self,
        capture_id: str,
        amount: Decimal,
        currency: str,
        request_id: str,
    ) -> RefundResult:
        ...

    async def get_refund(
        self,
        refund_id: str,
    ) -> RefundResult:
        ...

class PaypalAdapter(ABC):
    """
    Abstract interface for all PayPal communication.
    Maintained for synchronous/ABC compatibility with existing callers.
    """

    @abstractmethod
    def get_capture(self, capture_id: str) -> Any:
        """Fetches capture details from PayPal."""
        pass

    @abstractmethod
    def refund_capture(
        self,
        paypal_request_id: str,
        capture_id: str,
        amount: Decimal,
        currency: str,
    ) -> Any:
        """
        Executes a capture refund using an idempotent paypal_request_id.
        Returns normalized refund response.
        """
        pass

    @abstractmethod
    def get_refund(self, refund_id: str) -> Any:
        """Fetches status of an existing refund."""
        pass
