"""Custom exceptions for the Vaultline PayPal Sandbox adapter."""

from typing import Any, Optional


class PayPalError(Exception):
    """Base exception for PayPal adapter errors."""


class PayPalConfigurationError(PayPalError):
    """Raised when PayPal configuration or credentials are missing or invalid."""


class PayPalAuthenticationError(PayPalError):
    """Raised when PayPal OAuth2 token acquisition or authorization fails."""

    def __init__(
        self,
        message: str,
        *,
        status_code: Optional[int] = None,
        paypal_debug_id: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.paypal_debug_id = paypal_debug_id


class PayPalAPIError(PayPalError):
    """Raised for definitive PayPal REST API errors (e.g., 4xx responses)."""

    def __init__(
        self,
        message: str,
        *,
        status_code: Optional[int] = None,
        error_name: Optional[str] = None,
        paypal_debug_id: Optional[str] = None,
        details: Optional[Any] = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.error_name = error_name
        self.paypal_debug_id = paypal_debug_id
        self.details = details


class PayPalResourceNotFoundError(PayPalAPIError):
    """Raised when a PayPal capture or refund resource is not found (HTTP 404)."""


class PayPalTimeoutError(TimeoutError, PayPalError):
    """
    Raised when a PayPal request times out or fails with an ambiguous outcome.

    Inherits from built-in ``TimeoutError`` so that the backend service
    reconciles the transaction to ``UNKNOWN`` instead of marking it ``FAILED``.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: Optional[int] = None,
        paypal_debug_id: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.paypal_debug_id = paypal_debug_id
