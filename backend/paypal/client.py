"""Synchronous PayPal REST API v2 HTTP client for Sandbox execution."""

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
import logging
import math
import os
import threading
import time
from typing import Any, Callable, Dict, Optional, Tuple
from urllib.parse import quote, urlparse

import httpx

from backend.paypal.exceptions import (
    PayPalAPIError,
    PayPalAuthenticationError,
    PayPalConfigurationError,
    PayPalResourceNotFoundError,
    PayPalTimeoutError,
)

logger = logging.getLogger(__name__)

SANDBOX_BASE_URL = "https://api-m.sandbox.paypal.com"
SANDBOX_HOST = "api-m.sandbox.paypal.com"
DEFAULT_CONNECT_TIMEOUT = 5.0
DEFAULT_READ_TIMEOUT = 10.0
DEFAULT_EXPIRY_BUFFER_SECONDS = 60


@dataclass(frozen=True)
class PayPalConfig:
    """Validated PayPal Sandbox configuration."""

    client_id: str
    client_secret: str = field(repr=False)
    base_url: str = SANDBOX_BASE_URL
    connect_timeout: float = DEFAULT_CONNECT_TIMEOUT
    read_timeout: float = DEFAULT_READ_TIMEOUT
    expiry_buffer_seconds: int = DEFAULT_EXPIRY_BUFFER_SECONDS

    def __post_init__(self) -> None:
        if not isinstance(self.client_id, str) or not self.client_id.strip():
            raise PayPalConfigurationError("PAYPAL_CLIENT_ID must be configured and non-empty.")
        if not isinstance(self.client_secret, str) or not self.client_secret.strip():
            raise PayPalConfigurationError("PAYPAL_CLIENT_SECRET must be configured and non-empty.")

        normalized_url = (self.base_url or "").strip().rstrip("/")
        if not normalized_url:
            raise PayPalConfigurationError("PAYPAL_BASE_URL must not be empty.")

        parsed = urlparse(normalized_url)
        if parsed.scheme.lower() != "https":
            raise PayPalConfigurationError(
                "PAYPAL_BASE_URL must use HTTPS."
            )
        if (
            parsed.port is not None
            or parsed.netloc.lower() != SANDBOX_HOST
            or (parsed.hostname or "").lower() != SANDBOX_HOST
        ):
            raise PayPalConfigurationError(
                f"Non-Sandbox PAYPAL_BASE_URL is rejected during MVP. Expected origin 'https://{SANDBOX_HOST}'."
            )
        if parsed.path or parsed.query or parsed.fragment or parsed.username or parsed.password:
            raise PayPalConfigurationError(
                "PAYPAL_BASE_URL must be a clean origin URL without path, query, or credentials."
            )

        if (
            isinstance(self.connect_timeout, bool)
            or isinstance(self.read_timeout, bool)
            or not math.isfinite(self.connect_timeout)
            or not math.isfinite(self.read_timeout)
            or self.connect_timeout <= 0
            or self.read_timeout <= 0
        ):
            raise PayPalConfigurationError("PayPal HTTP timeouts must be finite positive numbers.")
        if isinstance(self.expiry_buffer_seconds, bool) or self.expiry_buffer_seconds < 0:
            raise PayPalConfigurationError("Token expiry buffer must be non-negative.")

        object.__setattr__(self, "client_id", self.client_id.strip())
        object.__setattr__(self, "client_secret", self.client_secret.strip())
        object.__setattr__(self, "base_url", normalized_url)

    @classmethod
    def from_env(cls) -> "PayPalConfig":
        """Load and validate PayPal configuration from environment variables."""
        client_id = os.getenv("PAYPAL_CLIENT_ID", "")
        client_secret = os.getenv("PAYPAL_CLIENT_SECRET", "")
        base_url = os.getenv("PAYPAL_BASE_URL") or SANDBOX_BASE_URL
        return cls(
            client_id=client_id,
            client_secret=client_secret,
            base_url=base_url,
        )


@dataclass(frozen=True)
class PayPalAPIResponse:
    """Raw JSON payload paired with PayPal diagnostic debug ID."""

    data: Dict[str, Any]
    paypal_debug_id: Optional[str] = None


def format_usd_amount(amount: Decimal) -> str:
    """
    Validate and format a monetary amount as a 2-decimal USD string.

    Strictly rejects floats and values with more than 2 decimal places.
    """
    if isinstance(amount, float) or not isinstance(amount, Decimal):
        raise TypeError("Monetary amount must be a Decimal instance, never float.")
    if not amount.is_finite():
        raise ValueError("Monetary amount must be finite.")
    if amount <= Decimal("0"):
        raise ValueError("Monetary amount must be greater than zero.")

    exp = amount.as_tuple().exponent
    if isinstance(exp, int) and exp < -2:
        raise ValueError("USD amount must not exceed 2 decimal places.")

    try:
        quantized = amount.quantize(Decimal("0.01"))
    except InvalidOperation as exc:
        raise ValueError("Invalid decimal monetary amount.") from exc

    if amount != quantized:
        raise ValueError("USD amount must not exceed 2 decimal places.")

    return f"{quantized:.2f}"


class PayPalClient:
    """
    Synchronous HTTPX client for PayPal REST API v2 (Sandbox only).

    Handles OAuth 2.0 client-credentials token caching, timeouts, and safe
    error mapping without logging or leaking secrets.
    """

    def __init__(
        self,
        config: PayPalConfig,
        http_client: Optional[httpx.Client] = None,
        time_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        self._config = config
        self._time_fn = time_fn
        self._token_lock = threading.Lock()
        self._access_token: Optional[str] = None
        self._token_expires_at: float = 0.0

        if http_client is not None:
            self._client = http_client
            self._owns_client = False
        else:
            timeout = httpx.Timeout(
                timeout=config.read_timeout,
                connect=config.connect_timeout,
                read=config.read_timeout,
                write=config.read_timeout,
                pool=config.connect_timeout,
            )
            self._client = httpx.Client(
                base_url=config.base_url,
                timeout=timeout,
                follow_redirects=False,
            )
            self._owns_client = True

    @property
    def config(self) -> PayPalConfig:
        return self._config

    def __enter__(self) -> "PayPalClient":
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()

    def close(self) -> None:
        """Close the underlying HTTPX client if owned by this instance."""
        if self._owns_client:
            self._client.close()

    def _build_url(self, path: str) -> str:
        return f"{self._config.base_url}{path}"

    def _invalidate_token(self) -> None:
        with self._token_lock:
            self._access_token = None
            self._token_expires_at = 0.0

    def get_access_token(self, *, force_refresh: bool = False) -> str:
        """Return a valid cached OAuth access token, refreshing if expired."""
        now = self._time_fn()
        if not force_refresh and self._access_token is not None and now < self._token_expires_at:
            return self._access_token

        with self._token_lock:
            now = self._time_fn()
            if not force_refresh and self._access_token is not None and now < self._token_expires_at:
                return self._access_token
            return self._fetch_access_token()

    def _fetch_access_token(self) -> str:
        url = self._build_url("/v1/oauth2/token")
        try:
            response = self._client.post(
                url,
                auth=(self._config.client_id, self._config.client_secret),
                data={"grant_type": "client_credentials"},
                headers={
                    "Accept": "application/json",
                    "Accept-Language": "en_US",
                },
            )
        except httpx.TimeoutException as exc:
            raise PayPalAuthenticationError("PayPal OAuth token request timed out.") from exc
        except httpx.RequestError as exc:
            raise PayPalAuthenticationError("Failed to connect to PayPal OAuth endpoint.") from exc

        debug_id = response.headers.get("paypal-debug-id")
        if response.status_code != 200:
            err_name, err_msg, _ = self._extract_error_details(response)
            detail = f"{err_name}: {err_msg}" if err_name and err_msg else (err_msg or err_name or "Authentication failed")
            raise PayPalAuthenticationError(
                f"PayPal OAuth failed with HTTP {response.status_code} ({detail}).",
                status_code=response.status_code,
                paypal_debug_id=debug_id,
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise PayPalAuthenticationError(
                "PayPal OAuth endpoint returned invalid JSON.",
                status_code=response.status_code,
                paypal_debug_id=debug_id,
            ) from exc

        if not isinstance(payload, dict):
            raise PayPalAuthenticationError(
                "PayPal OAuth response was not a JSON object.",
                status_code=response.status_code,
                paypal_debug_id=debug_id,
            )

        access_token = payload.get("access_token")
        expires_in = payload.get("expires_in")
        token_type = payload.get("token_type")

        if not isinstance(access_token, str) or not access_token.strip():
            raise PayPalAuthenticationError(
                "PayPal OAuth response missing valid access_token.",
                status_code=response.status_code,
                paypal_debug_id=debug_id,
            )

        if token_type is not None and str(token_type).lower() != "bearer":
            raise PayPalAuthenticationError(
                "PayPal OAuth response returned unexpected token_type.",
                status_code=response.status_code,
                paypal_debug_id=debug_id,
            )

        try:
            ttl = float(expires_in) if expires_in is not None else 0.0
        except (TypeError, ValueError) as exc:
            raise PayPalAuthenticationError(
                "PayPal OAuth response returned invalid expires_in.",
                status_code=response.status_code,
                paypal_debug_id=debug_id,
            ) from exc

        if ttl <= 0:
            raise PayPalAuthenticationError(
                "PayPal OAuth response returned non-positive expires_in.",
                status_code=response.status_code,
                paypal_debug_id=debug_id,
            )

        buffer_seconds = (
            float(self._config.expiry_buffer_seconds)
            if ttl > self._config.expiry_buffer_seconds
            else ttl * 0.2
        )
        now = self._time_fn()
        self._access_token = access_token
        self._token_expires_at = now + max(0.0, ttl - buffer_seconds)

        logger.debug("Acquired PayPal Sandbox OAuth token (debug_id=%s, ttl=%.0fs)", debug_id, ttl)
        return access_token

    @staticmethod
    def _extract_error_details(
        response: httpx.Response,
    ) -> Tuple[Optional[str], Optional[str], Optional[Any]]:
        try:
            body = response.json()
        except ValueError:
            return None, None, None

        if not isinstance(body, dict):
            return None, None, None

        error_name = body.get("name") or body.get("error")
        message = body.get("message") or body.get("error_description")
        details = body.get("details")
        return (
            str(error_name) if error_name is not None else None,
            str(message) if message is not None else None,
            details,
        )

    def _get_json(self, path: str, *, resource_label: str) -> PayPalAPIResponse:
        url = self._build_url(path)
        for attempt in range(2):
            token = self.get_access_token(force_refresh=(attempt == 1))
            try:
                response = self._client.get(
                    url,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Accept": "application/json",
                    },
                )
            except httpx.TimeoutException as exc:
                raise PayPalTimeoutError(
                    f"PayPal {resource_label} lookup timed out."
                ) from exc
            except httpx.RequestError as exc:
                raise PayPalAPIError(
                    f"Network error while fetching PayPal {resource_label}."
                ) from exc

            if response.status_code == 401 and attempt == 0:
                self._invalidate_token()
                continue

            debug_id = response.headers.get("paypal-debug-id")
            if response.status_code == 404:
                err_name, err_msg, details = self._extract_error_details(response)
                raise PayPalResourceNotFoundError(
                    err_msg or f"PayPal {resource_label} was not found.",
                    status_code=404,
                    error_name=err_name,
                    paypal_debug_id=debug_id,
                    details=details,
                )

            if response.status_code in (401, 403):
                raise PayPalAuthenticationError(
                    f"PayPal rejected authorization for {resource_label} lookup (HTTP {response.status_code}).",
                    status_code=response.status_code,
                    paypal_debug_id=debug_id,
                )

            if response.status_code >= 400:
                err_name, err_msg, details = self._extract_error_details(response)
                raise PayPalAPIError(
                    err_msg or f"PayPal {resource_label} lookup failed with HTTP {response.status_code}.",
                    status_code=response.status_code,
                    error_name=err_name,
                    paypal_debug_id=debug_id,
                    details=details,
                )

            try:
                payload = response.json()
            except ValueError as exc:
                raise PayPalAPIError(
                    f"PayPal {resource_label} response contained invalid JSON.",
                    status_code=response.status_code,
                    paypal_debug_id=debug_id,
                ) from exc

            if not isinstance(payload, dict):
                raise PayPalAPIError(
                    f"PayPal {resource_label} response was not a JSON object.",
                    status_code=response.status_code,
                    paypal_debug_id=debug_id,
                )

            return PayPalAPIResponse(data=payload, paypal_debug_id=debug_id)

        raise PayPalAuthenticationError(f"PayPal {resource_label} lookup failed authentication after refresh.")

    def get_capture(self, capture_id: str) -> PayPalAPIResponse:
        """Fetch capture resource from GET /v2/payments/captures/{capture_id}."""
        if not isinstance(capture_id, str) or not capture_id.strip():
            raise ValueError("capture_id must be a non-empty string.")
        quoted_id = quote(capture_id.strip(), safe="")
        return self._get_json(f"/v2/payments/captures/{quoted_id}", resource_label="capture")

    def get_refund(self, refund_id: str) -> PayPalAPIResponse:
        """Fetch refund resource from GET /v2/payments/refunds/{refund_id}."""
        if not isinstance(refund_id, str) or not refund_id.strip():
            raise ValueError("refund_id must be a non-empty string.")
        quoted_id = quote(refund_id.strip(), safe="")
        return self._get_json(f"/v2/payments/refunds/{quoted_id}", resource_label="refund")

    def create_refund(
        self,
        *,
        paypal_request_id: str,
        capture_id: str,
        amount: Decimal,
        currency: str = "USD",
    ) -> PayPalAPIResponse:
        """
        Issue a capture refund via POST /v2/payments/captures/{capture_id}/refund.

        Never retries automatically. Ambiguous timeouts, transport failures, and
        5xx server errors raise PayPalTimeoutError (subclass of TimeoutError).
        """
        if not isinstance(paypal_request_id, str) or not paypal_request_id.strip():
            raise ValueError("paypal_request_id must be a non-empty string.")
        if not isinstance(capture_id, str) or not capture_id.strip():
            raise ValueError("capture_id must be a non-empty string.")
        if not isinstance(currency, str) or currency.strip().upper() != "USD":
            raise ValueError("Only USD currency is supported during MVP.")

        amount_value = format_usd_amount(amount)
        quoted_capture_id = quote(capture_id.strip(), safe="")
        url = self._build_url(f"/v2/payments/captures/{quoted_capture_id}/refund")
        token = self.get_access_token()

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "PayPal-Request-Id": paypal_request_id,
        }
        payload = {
            "amount": {
                "value": amount_value,
                "currency_code": "USD",
            }
        }

        try:
            response = self._client.post(url, headers=headers, json=payload)
        except httpx.TimeoutException as exc:
            raise PayPalTimeoutError(
                "PayPal refund request timed out; execution outcome is unknown."
            ) from exc
        except httpx.RequestError as exc:
            raise PayPalTimeoutError(
                "PayPal refund request failed due to network error; execution outcome is unknown."
            ) from exc

        debug_id = response.headers.get("paypal-debug-id")

        if response.status_code >= 500:
            raise PayPalTimeoutError(
                f"PayPal server returned HTTP {response.status_code} during refund creation; outcome is ambiguous.",
                status_code=response.status_code,
                paypal_debug_id=debug_id,
            )

        if response.status_code == 404:
            err_name, err_msg, details = self._extract_error_details(response)
            raise PayPalResourceNotFoundError(
                err_msg or "PayPal capture was not found for refund.",
                status_code=404,
                error_name=err_name,
                paypal_debug_id=debug_id,
                details=details,
            )

        if response.status_code in (401, 403):
            if response.status_code == 401:
                self._invalidate_token()
            raise PayPalAuthenticationError(
                f"PayPal rejected authorization for refund creation (HTTP {response.status_code}).",
                status_code=response.status_code,
                paypal_debug_id=debug_id,
            )

        if response.status_code >= 400:
            err_name, err_msg, details = self._extract_error_details(response)
            raise PayPalAPIError(
                err_msg or f"PayPal refund creation failed with HTTP {response.status_code}.",
                status_code=response.status_code,
                error_name=err_name,
                paypal_debug_id=debug_id,
                details=details,
            )

        try:
            body = response.json()
        except ValueError as exc:
            raise PayPalTimeoutError(
                "PayPal returned invalid JSON after refund request; outcome is ambiguous.",
                status_code=response.status_code,
                paypal_debug_id=debug_id,
            ) from exc

        if not isinstance(body, dict):
            raise PayPalTimeoutError(
                "PayPal returned unexpected non-object response after refund request; outcome is ambiguous.",
                status_code=response.status_code,
                paypal_debug_id=debug_id,
            )

        logger.debug(
            "PayPal refund POST completed (capture_id=%s, status_code=%s, debug_id=%s)",
            capture_id,
            response.status_code,
            debug_id,
        )
        return PayPalAPIResponse(data=body, paypal_debug_id=debug_id)
