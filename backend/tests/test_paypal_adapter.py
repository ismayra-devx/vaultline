"""Unit tests for PayPalSandboxAdapter contract compliance and normalization."""

from decimal import Decimal
import inspect
from typing import List

import httpx
import pytest

from backend.adapter_interface import CaptureResult, PayPalAdapter, PaypalAdapter, RefundResult
from backend.paypal.adapter import PayPalSandboxAdapter
from backend.paypal.client import PayPalClient, PayPalConfig, SANDBOX_BASE_URL
from backend.paypal.exceptions import (
    PayPalAPIError,
    PayPalResourceNotFoundError,
    PayPalTimeoutError,
)


def _build_adapter(handler) -> PayPalSandboxAdapter:
    cfg = PayPalConfig(
        client_id="cid_adapter_test",
        client_secret="csec_adapter_test",
        base_url=SANDBOX_BASE_URL,
    )
    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    client = PayPalClient(cfg, http_client=http_client)
    return PayPalSandboxAdapter(client)


def _oauth_ok() -> httpx.Response:
    return httpx.Response(
        200,
        json={"access_token": "tok_adapter", "token_type": "Bearer", "expires_in": 3600},
    )


def test_adapter_implements_synchronous_abc() -> None:
    adapter = _build_adapter(lambda req: _oauth_ok())
    assert isinstance(adapter, PaypalAdapter)
    assert issubclass(PayPalSandboxAdapter, PayPalAdapter)

    for method_name in ("get_capture", "refund_capture", "get_refund"):
        method = getattr(PayPalSandboxAdapter, method_name)
        assert not inspect.iscoroutinefunction(method)

    refund_sig = inspect.signature(PayPalSandboxAdapter.refund_capture)
    params = list(refund_sig.parameters.values())
    assert [p.name for p in params[:5]] == [
        "self",
        "paypal_request_id",
        "capture_id",
        "amount",
        "currency",
    ]
    assert params[4].default == "USD"
    assert any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params)


# ---------------------------------------------------------------------------
# Capture lookup normalization tests
# ---------------------------------------------------------------------------


def test_get_capture_normalizes_into_capture_result() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/oauth2/token":
            return _oauth_ok()
        assert request.method == "GET"
        assert request.url.path == "/v2/payments/captures/CAP_200"
        return httpx.Response(
            200,
            json={
                "id": "CAP_200",
                "status": "completed",
                "amount": {"value": "125.50", "currency_code": "USD"},
            },
        )

    adapter = _build_adapter(handler)
    result = adapter.get_capture("CAP_200")

    assert isinstance(result, CaptureResult)
    assert result.capture_id == "CAP_200"
    assert result.status == "COMPLETED"
    assert result.amount == Decimal("125.50")
    assert result.currency == "USD"
    # Verify dict-like compatibility expected by callers
    assert result.get("id") == "CAP_200"
    assert result.get("status") == "COMPLETED"
    assert result["amount"] == {"value": "125.50", "currency_code": "USD"}


def test_get_capture_missing_returns_not_found() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/oauth2/token":
            return _oauth_ok()
        return httpx.Response(
            404,
            json={"name": "RESOURCE_NOT_FOUND", "message": "Capture does not exist"},
            headers={"paypal-debug-id": "dbg_cap_404"},
        )

    adapter = _build_adapter(handler)
    with pytest.raises(PayPalResourceNotFoundError):
        adapter.get_capture("CAP_NOPE")


def test_get_capture_invalid_payload_raises_api_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/oauth2/token":
            return _oauth_ok()
        return httpx.Response(
            200,
            json={"id": "CAP_BAD", "status": "COMPLETED"},  # missing amount block
        )

    adapter = _build_adapter(handler)
    with pytest.raises(PayPalAPIError):
        adapter.get_capture("CAP_BAD")


def test_get_capture_http_error_raises_api_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/oauth2/token":
            return _oauth_ok()
        return httpx.Response(500, json={"name": "INTERNAL_ERROR", "message": "Failure"})

    adapter = _build_adapter(handler)
    with pytest.raises(PayPalAPIError):
        adapter.get_capture("CAP_ERR")


# ---------------------------------------------------------------------------
# Refund creation & status normalization tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "paypal_status",
    ["COMPLETED", "PENDING", "FAILED", "DENIED"],
)
def test_refund_capture_normalizes_statuses_without_distortion(paypal_status: str) -> None:
    seen_headers: List[httpx.Headers] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/oauth2/token":
            return _oauth_ok()
        seen_headers.append(request.headers)
        assert request.method == "POST"
        assert request.url.path == "/v2/payments/captures/CAP_900/refund"
        return httpx.Response(
            201,
            json={
                "id": "REF_900",
                "status": paypal_status,
                "amount": {"value": "10.00", "currency_code": "USD"},
                "links": [
                    {
                        "href": "https://api-m.sandbox.paypal.com/v2/payments/captures/CAP_900",
                        "rel": "up",
                        "method": "GET",
                    }
                ],
            },
            headers={"paypal-debug-id": "dbg_ref_900"},
        )

    adapter = _build_adapter(handler)
    result = adapter.refund_capture(
        paypal_request_id="pp_req_exact_backend_id",
        capture_id="CAP_900",
        amount=Decimal("10.00"),
        currency="USD",
    )

    assert isinstance(result, RefundResult)
    assert result.refund_id == "REF_900"
    assert result.capture_id == "CAP_900"
    assert result.status == paypal_status
    assert result.amount == Decimal("10.00")
    assert result.currency == "USD"
    assert result.paypal_debug_id == "dbg_ref_900"
    # Verify exact dict-like access used by VaultlineService
    assert result.get("id") == "REF_900"
    assert result.get("status") == paypal_status
    assert seen_headers[0]["PayPal-Request-Id"] == "pp_req_exact_backend_id"


def test_refund_capture_timeout_raises_timeout_error_for_service_unknown() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/oauth2/token":
            return _oauth_ok()
        raise httpx.ConnectTimeout("Timed out connecting", request=request)

    adapter = _build_adapter(handler)
    with pytest.raises(TimeoutError) as exc_info:
        adapter.refund_capture(
            paypal_request_id="pp_req_timeout_2",
            capture_id="CAP_900",
            amount=Decimal("5.00"),
            currency="USD",
        )
    assert isinstance(exc_info.value, PayPalTimeoutError)


# ---------------------------------------------------------------------------
# Refund lookup tests
# ---------------------------------------------------------------------------


def test_get_refund_normalizes_into_refund_result() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/oauth2/token":
            return _oauth_ok()
        assert request.method == "GET"
        assert request.url.path == "/v2/payments/refunds/REF_777"
        return httpx.Response(
            200,
            json={
                "id": "REF_777",
                "status": "PENDING",
                "amount": {"value": "20.00", "currency_code": "USD"},
                "links": [
                    {
                        "href": "https://api-m.sandbox.paypal.com/v2/payments/captures/CAP_777",
                        "rel": "up",
                        "method": "GET",
                    }
                ],
            },
            headers={"paypal-debug-id": "dbg_lookup_777"},
        )

    adapter = _build_adapter(handler)
    result = adapter.get_refund("REF_777")

    assert isinstance(result, RefundResult)
    assert result.refund_id == "REF_777"
    assert result.capture_id == "CAP_777"
    assert result.status == "PENDING"
    assert result.amount == Decimal("20.00")
    assert result.currency == "USD"
    assert result.paypal_debug_id == "dbg_lookup_777"


def test_get_refund_missing_raises_not_found() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/oauth2/token":
            return _oauth_ok()
        return httpx.Response(
            404,
            json={"name": "RESOURCE_NOT_FOUND", "message": "Refund not found"},
        )

    adapter = _build_adapter(handler)
    with pytest.raises(PayPalResourceNotFoundError):
        adapter.get_refund("REF_MISSING")


def test_get_refund_http_error_raises_api_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/oauth2/token":
            return _oauth_ok()
        return httpx.Response(502, json={"name": "BAD_GATEWAY"})

    adapter = _build_adapter(handler)
    with pytest.raises(PayPalAPIError):
        adapter.get_refund("REF_ERR")


def test_refund_capture_malformed_2xx_raises_timeout_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/oauth2/token":
            return _oauth_ok()
        return httpx.Response(201, json={"status": "COMPLETED"})  # missing id

    adapter = _build_adapter(handler)
    with pytest.raises(TimeoutError) as exc_info:
        adapter.refund_capture(
            paypal_request_id="pp_req_malformed",
            capture_id="CAP_900",
            amount=Decimal("5.00"),
            currency="USD",
        )
    assert isinstance(exc_info.value, PayPalTimeoutError)


def test_adapter_from_env_context_manager(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PAYPAL_CLIENT_ID", "env_cid")
    monkeypatch.setenv("PAYPAL_CLIENT_SECRET", "env_csec")
    monkeypatch.setenv("PAYPAL_BASE_URL", SANDBOX_BASE_URL)

    with PayPalSandboxAdapter.from_env() as adapter:
        assert not adapter._client._client.is_closed
    assert adapter._client._client.is_closed


# ---------------------------------------------------------------------------
# Monetary validation & mismatch tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_value",
    [
        "NaN",
        "Infinity",
        "-Infinity",
        "0",
        "0.00",
        "-5.00",
        "10.123",
        "10.000",
        "not-a-number",
        "",
    ],
)
def test_get_capture_rejects_invalid_monetary_values(bad_value: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/oauth2/token":
            return _oauth_ok()
        return httpx.Response(
            200,
            json={
                "id": "CAP_BAD_AMT",
                "status": "COMPLETED",
                "amount": {"value": bad_value, "currency_code": "USD"},
            },
        )

    adapter = _build_adapter(handler)
    with pytest.raises(PayPalAPIError):
        adapter.get_capture("CAP_BAD_AMT")


@pytest.mark.parametrize(
    "bad_value",
    [
        "NaN",
        "Infinity",
        "-Infinity",
        "0.00",
        "-1.00",
        "10.123",
        "10.000",
    ],
)
def test_get_refund_rejects_invalid_monetary_values(bad_value: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/oauth2/token":
            return _oauth_ok()
        return httpx.Response(
            200,
            json={
                "id": "REF_BAD_AMT",
                "status": "COMPLETED",
                "amount": {"value": bad_value, "currency_code": "USD"},
            },
        )

    adapter = _build_adapter(handler)
    with pytest.raises(PayPalAPIError):
        adapter.get_refund("REF_BAD_AMT")


@pytest.mark.parametrize(
    "bad_value",
    [
        "NaN",
        "Infinity",
        "-Infinity",
        "0.00",
        "-10.00",
        "10.005",
        "10.000",
    ],
)
def test_refund_capture_invalid_returned_amount_raises_timeout_error(bad_value: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/oauth2/token":
            return _oauth_ok()
        return httpx.Response(
            201,
            json={
                "id": "REF_BAD_RET",
                "status": "COMPLETED",
                "amount": {"value": bad_value, "currency_code": "USD"},
            },
        )

    adapter = _build_adapter(handler)
    with pytest.raises(TimeoutError) as exc_info:
        adapter.refund_capture(
            paypal_request_id="pp_req_bad_ret",
            capture_id="CAP_900",
            amount=Decimal("10.00"),
            currency="USD",
        )
    assert isinstance(exc_info.value, PayPalTimeoutError)


def test_refund_capture_amount_mismatch_raises_timeout_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/oauth2/token":
            return _oauth_ok()
        return httpx.Response(
            201,
            json={
                "id": "REF_MISMATCH_AMT",
                "status": "COMPLETED",
                "amount": {"value": "9.00", "currency_code": "USD"},
            },
            headers={"paypal-debug-id": "dbg_mismatch_amt"},
        )

    adapter = _build_adapter(handler)
    with pytest.raises(TimeoutError) as exc_info:
        adapter.refund_capture(
            paypal_request_id="pp_req_mismatch_amt",
            capture_id="CAP_900",
            amount=Decimal("10.00"),
            currency="USD",
        )
    assert isinstance(exc_info.value, PayPalTimeoutError)
    assert exc_info.value.paypal_debug_id == "dbg_mismatch_amt"


def test_refund_capture_currency_mismatch_raises_timeout_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/oauth2/token":
            return _oauth_ok()
        return httpx.Response(
            201,
            json={
                "id": "REF_MISMATCH_CUR",
                "status": "COMPLETED",
                "amount": {"value": "10.00", "currency_code": "EUR"},
            },
            headers={"paypal-debug-id": "dbg_mismatch_cur"},
        )

    adapter = _build_adapter(handler)
    with pytest.raises(TimeoutError) as exc_info:
        adapter.refund_capture(
            paypal_request_id="pp_req_mismatch_cur",
            capture_id="CAP_900",
            amount=Decimal("10.00"),
            currency="USD",
        )
    assert isinstance(exc_info.value, PayPalTimeoutError)
    assert exc_info.value.paypal_debug_id == "dbg_mismatch_cur"


def test_refund_capture_omitted_amount_uses_validated_request_fallback() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/oauth2/token":
            return _oauth_ok()
        return httpx.Response(
            201,
            json={
                "id": "REF_NO_AMT",
                "status": "COMPLETED",
            },
        )

    adapter = _build_adapter(handler)
    result = adapter.refund_capture(
        paypal_request_id="pp_req_no_amt",
        capture_id="CAP_900",
        amount=Decimal("12.5"),
        currency="USD",
    )
    assert result.refund_id == "REF_NO_AMT"
    assert result.status == "COMPLETED"
    assert result.amount == Decimal("12.50")
    assert result.currency == "USD"


@pytest.mark.parametrize(
    "payload",
    [
        {"status": "COMPLETED", "amount": {"value": "5.00", "currency_code": "USD"}},
        {"id": "   ", "status": "COMPLETED", "amount": {"value": "5.00", "currency_code": "USD"}},
        {"id": "REF_1", "amount": {"value": "5.00", "currency_code": "USD"}},
        {"id": "REF_1", "status": "  ", "amount": {"value": "5.00", "currency_code": "USD"}},
    ],
)
def test_refund_capture_missing_id_or_status_raises_timeout_error(payload: dict) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/oauth2/token":
            return _oauth_ok()
        return httpx.Response(201, json=payload)

    adapter = _build_adapter(handler)
    with pytest.raises(TimeoutError) as exc_info:
        adapter.refund_capture(
            paypal_request_id="pp_req_missing_fields",
            capture_id="CAP_900",
            amount=Decimal("5.00"),
            currency="USD",
        )
    assert isinstance(exc_info.value, PayPalTimeoutError)


# ---------------------------------------------------------------------------
# HATEOAS capture ID extraction security tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "allowed_host",
    [
        "api-m.sandbox.paypal.com",
        "api.sandbox.paypal.com",
    ],
)
def test_get_refund_accepts_allowed_sandbox_hateoas_hosts(allowed_host: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/oauth2/token":
            return _oauth_ok()
        return httpx.Response(
            200,
            json={
                "id": "REF_HOST_OK",
                "status": "COMPLETED",
                "amount": {"value": "10.00", "currency_code": "USD"},
                "links": [
                    {
                        "href": f"https://{allowed_host}/v2/payments/captures/CAP_ALLOWED_42",
                        "rel": "up",
                        "method": "GET",
                    },
                ],
            },
        )

    adapter = _build_adapter(handler)
    result = adapter.get_refund("REF_HOST_OK")
    assert result.capture_id == "CAP_ALLOWED_42"


@pytest.mark.parametrize(
    "untrusted_href",
    [
        "https://evil.example.com/v2/payments/captures/CAP_EVIL",
        "https://api-m.paypal.com/v2/payments/captures/CAP_PROD",
        "https://api.paypal.com/v2/payments/captures/CAP_PROD2",
        "https://api.sandbox.paypal.com.evil.com/v2/payments/captures/CAP_LOOKALIKE1",
        "https://evil-api.sandbox.paypal.com/v2/payments/captures/CAP_LOOKALIKE2",
        "https://api-sandbox.paypal.com/v2/payments/captures/CAP_LOOKALIKE3",
        "https://api.sandbox-paypal.com/v2/payments/captures/CAP_LOOKALIKE4",
        "https://api-m.sandbox.paypal.com:8443/v2/payments/captures/CAP_PORT",
        "https://api.sandbox.paypal.com:8443/v2/payments/captures/CAP_PORT2",
        "https://api.sandbox.paypal.com:443/v2/payments/captures/CAP_PORT443",
        "http://api-m.sandbox.paypal.com/v2/payments/captures/CAP_HTTP",
        "http://api.sandbox.paypal.com/v2/payments/captures/CAP_HTTP2",
        "https://user:pass@api-m.sandbox.paypal.com/v2/payments/captures/CAP_CRED",
        "https://user:pass@api.sandbox.paypal.com/v2/payments/captures/CAP_CRED2",
        "https://api-m.sandbox.paypal.com/v1/payments/captures/CAP_V1",
        "https://api.sandbox.paypal.com/v1/payments/captures/CAP_V1_2",
        "https://api-m.sandbox.paypal.com/v2/payments/captures/CAP_Q?override=1",
        "https://api.sandbox.paypal.com/v2/payments/captures/CAP_Q2?override=1",
        "https://api-m.sandbox.paypal.com/v2/payments/captures/CAP_F#frag",
        "https://api.sandbox.paypal.com/v2/payments/captures/CAP_F2#frag",
        "https://api-m.sandbox.paypal.com/v2/payments/captures/CAP_EXTRA/refund",
        "https://api.sandbox.paypal.com/v2/payments/captures/CAP_EXTRA2/refund",
        "https://api-m.sandbox.paypal.com/v2/payments/captures/",
        "https://api.sandbox.paypal.com/v2/payments/captures/",
        "not-a-valid-url",
    ],
)
def test_get_refund_rejects_untrusted_or_malformed_hateoas_links(untrusted_href: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/oauth2/token":
            return _oauth_ok()
        return httpx.Response(
            200,
            json={
                "id": "REF_LINK_TEST",
                "status": "COMPLETED",
                "amount": {"value": "10.00", "currency_code": "USD"},
                "links": [
                    {"href": untrusted_href, "rel": "up", "method": "GET"},
                ],
            },
        )

    adapter = _build_adapter(handler)
    result = adapter.get_refund("REF_LINK_TEST")
    assert result.capture_id is None


@pytest.mark.parametrize("rel_value", ["self", "alternate", "next", ""])
def test_get_refund_ignores_non_up_hateoas_links(rel_value: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/oauth2/token":
            return _oauth_ok()
        return httpx.Response(
            200,
            json={
                "id": "REF_REL_TEST",
                "status": "COMPLETED",
                "amount": {"value": "10.00", "currency_code": "USD"},
                "links": [
                    {
                        "href": "https://api.sandbox.paypal.com/v2/payments/captures/CAP_IGNORED",
                        "rel": rel_value,
                        "method": "GET",
                    },
                ],
            },
        )

    adapter = _build_adapter(handler)
    result = adapter.get_refund("REF_REL_TEST")
    assert result.capture_id is None


def test_get_refund_preserves_explicit_capture_id_fallback() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/oauth2/token":
            return _oauth_ok()
        return httpx.Response(
            200,
            json={
                "id": "REF_EXPLICIT",
                "capture_id": "CAP_EXPLICIT_123",
                "status": "COMPLETED",
                "amount": {"value": "10.00", "currency_code": "USD"},
                "links": [
                    {
                        "href": "https://evil.example.com/v2/payments/captures/CAP_EVIL",
                        "rel": "up",
                        "method": "GET",
                    }
                ],
            },
        )

    adapter = _build_adapter(handler)
    result = adapter.get_refund("REF_EXPLICIT")
    assert result.capture_id == "CAP_EXPLICIT_123"
