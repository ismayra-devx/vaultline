"""PayPal Sandbox implementation of the Vaultline PaypalAdapter interface."""

from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlparse

from backend.adapter_interface import (
    CaptureResult,
    PaypalAdapter,
    RefundResult,
)
from backend.paypal.client import PayPalClient, PayPalConfig, SANDBOX_HOST
from backend.paypal.exceptions import PayPalAPIError, PayPalTimeoutError


def _parse_money_block(
    amount_obj: Any,
    *,
    resource_label: str,
    paypal_debug_id: Optional[str],
    ambiguous_on_error: bool = False,
) -> Tuple[Decimal, str]:
    """
    Parse and validate PayPal {value, currency_code} object using Decimal only.

    Rejects booleans, floats, NaN, Infinity, non-positive amounts, and values
    exceeding 2 decimal places for USD without rounding.
    """
    exc_cls = PayPalTimeoutError if ambiguous_on_error else PayPalAPIError

    if not isinstance(amount_obj, dict):
        raise exc_cls(
            f"PayPal {resource_label} response missing valid amount object.",
            paypal_debug_id=paypal_debug_id,
        )

    raw_value = amount_obj.get("value")
    raw_currency = amount_obj.get("currency_code")

    if (
        raw_value is None
        or isinstance(raw_value, (bool, float))
        or not isinstance(raw_currency, str)
        or not raw_currency.strip()
    ):
        raise exc_cls(
            f"PayPal {resource_label} response missing valid amount value or currency_code.",
            paypal_debug_id=paypal_debug_id,
        )

    raw_text = str(raw_value).strip()
    if not raw_text:
        raise exc_cls(
            f"PayPal {resource_label} response contained empty amount value.",
            paypal_debug_id=paypal_debug_id,
        )

    try:
        parsed_amount = Decimal(raw_text)
    except (InvalidOperation, ValueError) as exc:
        raise exc_cls(
            f"PayPal {resource_label} response contained invalid decimal amount.",
            paypal_debug_id=paypal_debug_id,
        ) from exc

    if not parsed_amount.is_finite():
        raise exc_cls(
            f"PayPal {resource_label} response contained non-finite amount.",
            paypal_debug_id=paypal_debug_id,
        )

    if parsed_amount <= Decimal("0"):
        raise exc_cls(
            f"PayPal {resource_label} response contained non-positive amount.",
            paypal_debug_id=paypal_debug_id,
        )

    normalized_currency = raw_currency.strip().upper()
    if normalized_currency == "USD":
        exp = parsed_amount.as_tuple().exponent
        if isinstance(exp, int) and exp < -2:
            raise exc_cls(
                f"PayPal {resource_label} USD amount exceeded 2 decimal places.",
                paypal_debug_id=paypal_debug_id,
            )
        try:
            quantized = parsed_amount.quantize(Decimal("0.01"))
        except InvalidOperation as exc:
            raise exc_cls(
                f"PayPal {resource_label} response amount could not be normalized.",
                paypal_debug_id=paypal_debug_id,
            ) from exc
        if parsed_amount != quantized:
            raise exc_cls(
                f"PayPal {resource_label} USD amount exceeded 2 decimal places.",
                paypal_debug_id=paypal_debug_id,
            )
        parsed_amount = quantized

    return parsed_amount, normalized_currency


def _extract_capture_id(data: Dict[str, Any]) -> Optional[str]:
    """
    Extract capture_id from explicit field or a validated PayPal Sandbox HATEOAS 'up' link.

    Only accepts clean HTTPS URLs targeting allowed PayPal Sandbox hosts
    (``api-m.sandbox.paypal.com`` or ``api.sandbox.paypal.com``) with exact path
    ``/v2/payments/captures/{capture_id}`` and no query/fragment/port.
    """
    explicit_id = data.get("capture_id")
    if isinstance(explicit_id, str) and explicit_id.strip():
        return explicit_id.strip()

    links = data.get("links")
    if not isinstance(links, list):
        return None

    allowed_hosts = {SANDBOX_HOST, "api.sandbox.paypal.com"}

    for link in links:
        if not isinstance(link, dict):
            continue
        href = link.get("href")
        if link.get("rel") != "up" or not isinstance(href, str) or not href.strip():
            continue

        try:
            parsed = urlparse(href.strip())
        except ValueError:
            continue

        if parsed.scheme.lower() != "https":
            continue
        if parsed.port is not None or parsed.netloc.lower() not in allowed_hosts:
            continue
        if (parsed.hostname or "").lower() not in allowed_hosts:
            continue
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            continue

        parts = parsed.path.split("/")
        if (
            len(parts) == 5
            and parts[0] == ""
            and parts[1] == "v2"
            and parts[2] == "payments"
            and parts[3] == "captures"
            and parts[4].strip()
        ):
            return parts[4].strip()

    return None


def _normalize_status(raw_status: Any) -> Optional[str]:
    if not isinstance(raw_status, str) or not raw_status.strip():
        return None
    return raw_status.strip().upper()


class PayPalSandboxAdapter(PaypalAdapter):
    """
    Synchronous PayPal Sandbox adapter implementing ``PaypalAdapter``.

    Responsible strictly for communicating with PayPal REST API v2 and
    normalizing responses into ``CaptureResult`` and ``RefundResult``.
    """

    def __init__(self, client: PayPalClient) -> None:
        self._client = client

    @classmethod
    def from_env(cls) -> "PayPalSandboxAdapter":
        """Construct adapter from validated environment variables."""
        config = PayPalConfig.from_env()
        return cls(PayPalClient(config))

    def __enter__(self) -> "PayPalSandboxAdapter":
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()

    def close(self) -> None:
        """Close the underlying PayPal HTTP client."""
        self._client.close()

    def get_capture(self, capture_id: str) -> CaptureResult:
        """Fetch and normalize capture details from PayPal."""
        resp = self._client.get_capture(capture_id)
        data = resp.data

        parsed_id = data.get("id")
        status = _normalize_status(data.get("status"))
        if not isinstance(parsed_id, str) or not parsed_id.strip() or status is None:
            raise PayPalAPIError(
                "PayPal capture response missing required id or status.",
                paypal_debug_id=resp.paypal_debug_id,
            )

        amount, currency = _parse_money_block(
            data.get("amount"),
            resource_label="capture",
            paypal_debug_id=resp.paypal_debug_id,
        )

        return CaptureResult(
            capture_id=parsed_id.strip(),
            status=status,
            amount=amount,
            currency=currency,
        )

    def refund_capture(
        self,
        paypal_request_id: str,
        capture_id: str,
        amount: Decimal,
        currency: str = "USD",
        **kwargs: Any,
    ) -> RefundResult:
        """
        Execute an idempotent capture refund against PayPal Sandbox.

        Forwards ``paypal_request_id`` unchanged in ``PayPal-Request-Id`` and
        normalizes the response into ``RefundResult``.
        """
        resp = self._client.create_refund(
            paypal_request_id=paypal_request_id,
            capture_id=capture_id,
            amount=amount,
            currency=currency,
        )
        data = resp.data

        refund_id = data.get("id")
        status = _normalize_status(data.get("status"))
        if not isinstance(refund_id, str) or not refund_id.strip() or status is None:
            raise PayPalTimeoutError(
                "PayPal refund response missing required id or status; outcome is ambiguous.",
                paypal_debug_id=resp.paypal_debug_id,
            )

        expected_amount = amount.quantize(Decimal("0.01"))
        expected_currency = currency.strip().upper()

        if "amount" in data and data["amount"] is not None:
            parsed_amount, parsed_currency = _parse_money_block(
                data["amount"],
                resource_label="refund",
                paypal_debug_id=resp.paypal_debug_id,
                ambiguous_on_error=True,
            )
            if parsed_amount != expected_amount or parsed_currency != expected_currency:
                raise PayPalTimeoutError(
                    "PayPal refund response amount or currency did not match requested refund; "
                    "reconciliation required.",
                    paypal_debug_id=resp.paypal_debug_id,
                )
        else:
            # Safe fallback when PayPal omits the amount echo on a valid 2xx refund:
            # create_refund already validated and transmitted exact expected_amount/expected_currency.
            parsed_amount = expected_amount
            parsed_currency = expected_currency

        extracted_capture_id = _extract_capture_id(data) or capture_id.strip()

        return RefundResult(
            refund_id=refund_id.strip(),
            capture_id=extracted_capture_id,
            status=status,
            amount=parsed_amount,
            currency=parsed_currency,
            paypal_debug_id=resp.paypal_debug_id,
        )

    def get_refund(self, refund_id: str) -> RefundResult:
        """Fetch and normalize status of an existing refund from PayPal."""
        resp = self._client.get_refund(refund_id)
        data = resp.data

        parsed_id = data.get("id")
        status = _normalize_status(data.get("status"))
        if not isinstance(parsed_id, str) or not parsed_id.strip() or status is None:
            raise PayPalAPIError(
                "PayPal refund lookup response missing required id or status.",
                paypal_debug_id=resp.paypal_debug_id,
            )

        amount, currency = _parse_money_block(
            data.get("amount"),
            resource_label="refund",
            paypal_debug_id=resp.paypal_debug_id,
        )

        return RefundResult(
            refund_id=parsed_id.strip(),
            capture_id=_extract_capture_id(data),
            status=status,
            amount=amount,
            currency=currency,
            paypal_debug_id=resp.paypal_debug_id,
        )
