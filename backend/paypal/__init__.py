"""Vaultline P1 PayPal Sandbox adapter package."""

from backend.paypal.adapter import PayPalSandboxAdapter
from backend.paypal.client import PayPalAPIResponse, PayPalClient, PayPalConfig
from backend.paypal.exceptions import (
    PayPalAPIError,
    PayPalAuthenticationError,
    PayPalConfigurationError,
    PayPalError,
    PayPalResourceNotFoundError,
    PayPalTimeoutError,
)
from backend.paypal.register import register_capture, register_captures_from_env

__all__ = [
    "PayPalAPIError",
    "PayPalAPIResponse",
    "PayPalAuthenticationError",
    "PayPalClient",
    "PayPalConfig",
    "PayPalConfigurationError",
    "PayPalError",
    "PayPalResourceNotFoundError",
    "PayPalSandboxAdapter",
    "PayPalTimeoutError",
    "register_capture",
    "register_captures_from_env",
]
