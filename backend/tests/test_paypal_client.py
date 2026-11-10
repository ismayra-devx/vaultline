"""Unit tests for PayPalConfig and PayPalClient using mocked HTTPX transports."""

import base64
from decimal import Decimal
import json
import logging
from typing import Any, List
from urllib.parse import parse_qs

import httpx
import pytest

from backend.paypal.client import PayPalClient, PayPalConfig, SANDBOX_BASE_URL
from backend.paypal.exceptions import (
    PayPalAPIError,
    PayPalAuthenticationError,
    PayPalConfigurationError,
    PayPalResourceNotFoundError,
    PayPalTimeoutError,
)


class FakeClock:
    def __init__(self, initial: float = 1000.0) -> None:
        self.now = initial

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _make_config(**overrides) -> PayPalConfig:
    params = {
        "client_id": "test_client_id",
        "client_secret": "super_secret_value_xyz",
        "base_url": SANDBOX_BASE_URL,
    }
    params.update(overrides)
    return PayPalConfig(**params)


def _oauth_success_response(
    access_token: str = "tok_sandbox_12345",
    expires_in: int = 3600,
    debug_id: str = "dbg_oauth_1",
) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "access_token": access_token,
            "token_type": "Bearer",
            "expires_in": expires_in,
        },
        headers={"paypal-debug-id": debug_id},
    )


# ---------------------------------------------------------------------------
# Configuration & Sandbox enforcement tests
# ---------------------------------------------------------------------------


def test_config_defaults_to_sandbox_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PAYPAL_CLIENT_ID", "cid_123")
    monkeypatch.setenv("PAYPAL_CLIENT_SECRET", "csec_456")
    monkeypatch.delenv("PAYPAL_BASE_URL", raising=False)

    cfg = PayPalConfig.from_env()
    assert cfg.base_url == "https://api-m.sandbox.paypal.com"
    assert cfg.client_id == "cid_123"
    assert "csec_456" not in repr(cfg)


@pytest.mark.parametrize(
    "missing_var",
    ["PAYPAL_CLIENT_ID", "PAYPAL_CLIENT_SECRET"],
)
def test_config_missing_credentials_raises(
    monkeypatch: pytest.MonkeyPatch, missing_var: str
) -> None:
    monkeypatch.setenv("PAYPAL_CLIENT_ID", "cid_123")
    monkeypatch.setenv("PAYPAL_CLIENT_SECRET", "csec_456")
    monkeypatch.delenv(missing_var, raising=False)

    with pytest.raises(PayPalConfigurationError):
        PayPalConfig.from_env()


def test_config_blank_credentials_raises() -> None:
    with pytest.raises(PayPalConfigurationError):
        _make_config(client_id="   ")
    with pytest.raises(PayPalConfigurationError):
        _make_config(client_secret="")


@pytest.mark.parametrize(
    "bad_url",
    [
        "https://api-m.paypal.com",
        "https://api.paypal.com",
        "https://example.com",
        "http://api-m.sandbox.paypal.com",
        "http://localhost:8000",
        "https://api-m.sandbox.paypal.com/v2",
        "https://api-m.sandbox.paypal.com:8443",
        "https://api-m.sandbox.paypal.com:443",
        "https://user:pass@api-m.sandbox.paypal.com",
        "https://api-m.sandbox.paypal.com?foo=bar",
        "https://api-m.sandbox.paypal.com#frag",
    ],
)
def test_config_rejects_production_and_insecure_urls(bad_url: str) -> None:
    with pytest.raises(PayPalConfigurationError):
        _make_config(base_url=bad_url)


# ---------------------------------------------------------------------------
# OAuth token management tests
# ---------------------------------------------------------------------------


def test_oauth_token_retrieval_basic_auth_and_grant() -> None:
    captured_requests: List[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_requests.append(request)
        return _oauth_success_response()

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    client = PayPalClient(_make_config(), http_client=http_client)

    token = client.get_access_token()
    assert token == "tok_sandbox_12345"
    assert len(captured_requests) == 1

    req = captured_requests[0]
    assert req.method == "POST"
    assert str(req.url) == "https://api-m.sandbox.paypal.com/v1/oauth2/token"

    auth_header = req.headers["Authorization"]
    assert auth_header.startswith("Basic ")
    decoded = base64.b64decode(auth_header.split(" ", 1)[1]).decode("utf-8")
    assert decoded == "test_client_id:super_secret_value_xyz"

    form_data = parse_qs(req.content.decode("utf-8"))
    assert form_data == {"grant_type": ["client_credentials"]}


def test_oauth_token_reuse_and_refresh_after_expiry() -> None:
    clock = FakeClock(initial=1000.0)
    oauth_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal oauth_calls
        oauth_calls += 1
        return _oauth_success_response(
            access_token=f"tok_{oauth_calls}",
            expires_in=3600,
        )

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    client = PayPalClient(
        _make_config(expiry_buffer_seconds=60),
        http_client=http_client,
        time_fn=clock,
    )

    assert client.get_access_token() == "tok_1"
    # Within valid window (3539s elapsed < 3540s effective TTL)
    clock.advance(3500.0)
    assert client.get_access_token() == "tok_1"
    assert oauth_calls == 1

    # Advance into expiry buffer (total 3545s elapsed >= 3540s)
    clock.advance(45.0)
    assert client.get_access_token() == "tok_2"
    assert oauth_calls == 2


def test_oauth_failure_raises_without_leaking_secrets() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401,
            json={
                "error": "invalid_client",
                "error_description": "Client Authentication failed",
            },
            headers={"paypal-debug-id": "dbg_auth_fail"},
        )

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    client = PayPalClient(_make_config(), http_client=http_client)

    with pytest.raises(PayPalAuthenticationError) as exc_info:
        client.get_access_token()

    msg = str(exc_info.value)
    assert "super_secret_value_xyz" not in msg
    assert exc_info.value.status_code == 401
    assert exc_info.value.paypal_debug_id == "dbg_auth_fail"


def test_oauth_timeout_raises_auth_error_without_leaking_secrets() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("OAuth read timeout", request=request)

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    client = PayPalClient(_make_config(), http_client=http_client)

    with pytest.raises(PayPalAuthenticationError) as exc_info:
        client.get_access_token()

    assert "super_secret_value_xyz" not in str(exc_info.value)


# ---------------------------------------------------------------------------
# Client refund, capture, idempotency, and timeout tests
# ---------------------------------------------------------------------------


def test_create_refund_preserves_request_id_and_formats_decimal_json() -> None:
    captured_refund_req: List[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/oauth2/token":
            return _oauth_success_response()
        captured_refund_req.append(request)
        return httpx.Response(
            201,
            json={
                "id": "REF_PP_001",
                "status": "COMPLETED",
                "amount": {"value": "10.00", "currency_code": "USD"},
            },
            headers={"paypal-debug-id": "dbg_ref_001"},
        )

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    client = PayPalClient(_make_config(), http_client=http_client)

    resp = client.create_refund(
        paypal_request_id="pp_req_backend_999",
        capture_id="CAP_123",
        amount=Decimal("10"),
        currency="USD",
    )

    assert resp.data["id"] == "REF_PP_001"
    assert resp.paypal_debug_id == "dbg_ref_001"
    assert len(captured_refund_req) == 1

    req = captured_refund_req[0]
    assert req.method == "POST"
    assert str(req.url) == "https://api-m.sandbox.paypal.com/v2/payments/captures/CAP_123/refund"
    assert req.headers["PayPal-Request-Id"] == "pp_req_backend_999"
    assert req.headers["Authorization"] == "Bearer tok_sandbox_12345"

    body = json.loads(req.content.decode("utf-8"))
    assert body == {
        "amount": {
            "value": "10.00",
            "currency_code": "USD",
        }
    }


def test_create_refund_rejects_float_excess_precision_and_blank_request_id() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("No HTTP call should happen on invalid input")

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    client = PayPalClient(_make_config(), http_client=http_client)

    with pytest.raises(TypeError):
        client.create_refund(
            paypal_request_id="pp_req_1",
            capture_id="CAP_123",
            amount=10.5,  # type: ignore[arg-type]
            currency="USD",
        )

    with pytest.raises(ValueError):
        client.create_refund(
            paypal_request_id="pp_req_1",
            capture_id="CAP_123",
            amount=Decimal("10.123"),
            currency="USD",
        )

    with pytest.raises(ValueError):
        client.create_refund(
            paypal_request_id="   ",
            capture_id="CAP_123",
            amount=Decimal("10.00"),
            currency="USD",
        )


def test_create_refund_timeout_raises_timeout_error_without_retry() -> None:
    refund_attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal refund_attempts
        if request.url.path == "/v1/oauth2/token":
            return _oauth_success_response()
        refund_attempts += 1
        raise httpx.ReadTimeout("Upstream timeout", request=request)

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    client = PayPalClient(_make_config(), http_client=http_client)

    with pytest.raises(TimeoutError) as exc_info:
        client.create_refund(
            paypal_request_id="pp_req_timeout",
            capture_id="CAP_123",
            amount=Decimal("15.00"),
            currency="USD",
        )

    assert isinstance(exc_info.value, PayPalTimeoutError)
    assert refund_attempts == 1
    assert "tok_sandbox_12345" not in str(exc_info.value)


def test_create_refund_network_error_raises_timeout_error_without_retry() -> None:
    refund_attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal refund_attempts
        if request.url.path == "/v1/oauth2/token":
            return _oauth_success_response()
        refund_attempts += 1
        raise httpx.RemoteProtocolError("Connection dropped mid-response", request=request)

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    client = PayPalClient(_make_config(), http_client=http_client)

    with pytest.raises(TimeoutError):
        client.create_refund(
            paypal_request_id="pp_req_net",
            capture_id="CAP_123",
            amount=Decimal("15.00"),
            currency="USD",
        )

    assert refund_attempts == 1


@pytest.mark.parametrize("status_code", [500, 502, 503, 504])
def test_create_refund_5xx_raises_timeout_error_without_retry(status_code: int) -> None:
    refund_attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal refund_attempts
        if request.url.path == "/v1/oauth2/token":
            return _oauth_success_response()
        refund_attempts += 1
        return httpx.Response(
            status_code,
            json={"name": "INTERNAL_SERVICE_ERROR", "message": "Upstream error"},
            headers={"paypal-debug-id": "dbg_5xx"},
        )

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    client = PayPalClient(_make_config(), http_client=http_client)

    with pytest.raises(TimeoutError) as exc_info:
        client.create_refund(
            paypal_request_id="pp_req_5xx",
            capture_id="CAP_123",
            amount=Decimal("15.00"),
            currency="USD",
        )

    assert isinstance(exc_info.value, PayPalTimeoutError)
    assert exc_info.value.paypal_debug_id == "dbg_5xx"
    assert refund_attempts == 1


def test_create_refund_422_raises_api_error_with_debug_id_no_retry() -> None:
    refund_attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal refund_attempts
        if request.url.path == "/v1/oauth2/token":
            return _oauth_success_response()
        refund_attempts += 1
        return httpx.Response(
            422,
            json={
                "name": "UNPROCESSABLE_ENTITY",
                "message": "The requested action could not be performed.",
                "details": [{"issue": "CAPTURE_FULLY_REFUNDED"}],
            },
            headers={"paypal-debug-id": "dbg_422"},
        )

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    client = PayPalClient(_make_config(), http_client=http_client)

    with pytest.raises(PayPalAPIError) as exc_info:
        client.create_refund(
            paypal_request_id="pp_req_422",
            capture_id="CAP_123",
            amount=Decimal("15.00"),
            currency="USD",
        )

    assert not isinstance(exc_info.value, TimeoutError)
    assert exc_info.value.status_code == 422
    assert exc_info.value.error_name == "UNPROCESSABLE_ENTITY"
    assert exc_info.value.paypal_debug_id == "dbg_422"
    assert refund_attempts == 1


def test_get_capture_404_raises_resource_not_found() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/oauth2/token":
            return _oauth_success_response()
        return httpx.Response(
            404,
            json={"name": "RESOURCE_NOT_FOUND", "message": "Capture not found"},
            headers={"paypal-debug-id": "dbg_404"},
        )

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    client = PayPalClient(_make_config(), http_client=http_client)

    with pytest.raises(PayPalResourceNotFoundError) as exc_info:
        client.get_capture("MISSING_CAP")

    assert exc_info.value.status_code == 404
    assert exc_info.value.paypal_debug_id == "dbg_404"


def test_client_context_manager_closes_owned_client() -> None:
    cfg = _make_config()
    with PayPalClient(cfg) as client:
        assert not client._client.is_closed
    assert client._client.is_closed


def test_create_refund_401_does_not_retry_and_invalidates_token() -> None:
    refund_attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal refund_attempts
        if request.url.path == "/v1/oauth2/token":
            return _oauth_success_response(access_token="tok_secret_abc")
        refund_attempts += 1
        return httpx.Response(401, json={"error": "invalid_token"})

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    client = PayPalClient(_make_config(), http_client=http_client)

    with pytest.raises(PayPalAuthenticationError) as exc_info:
        client.create_refund(
            paypal_request_id="pp_req_401",
            capture_id="CAP_123",
            amount=Decimal("5.00"),
            currency="USD",
        )

    assert refund_attempts == 1
    assert client._access_token is None
    assert "tok_secret_abc" not in str(exc_info.value)
    assert "super_secret_value_xyz" not in str(exc_info.value)


def test_get_capture_retries_once_on_401_token_expiry() -> None:
    oauth_calls = 0
    capture_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal oauth_calls, capture_calls
        if request.url.path == "/v1/oauth2/token":
            oauth_calls += 1
            return _oauth_success_response(access_token=f"tok_{oauth_calls}")
        capture_calls += 1
        if capture_calls == 1:
            assert request.headers["Authorization"] == "Bearer tok_1"
            return httpx.Response(401, json={"error": "invalid_token"})
        assert request.headers["Authorization"] == "Bearer tok_2"
        return httpx.Response(
            200,
            json={
                "id": "CAP_RETRY",
                "status": "COMPLETED",
                "amount": {"value": "10.00", "currency_code": "USD"},
            },
        )

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    client = PayPalClient(_make_config(), http_client=http_client)
    resp = client.get_capture("CAP_RETRY")

    assert oauth_calls == 2
    assert capture_calls == 2
    assert resp.data["id"] == "CAP_RETRY"


@pytest.mark.parametrize(
    "bad_amount",
    [
        10.5,
        True,
        "10.00",
        None,
    ],
)
def test_create_refund_rejects_non_decimal_amount_types(bad_amount: Any) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("No HTTP call should happen on invalid amount type")

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    client = PayPalClient(_make_config(), http_client=http_client)

    with pytest.raises(TypeError):
        client.create_refund(
            paypal_request_id="pp_req_bad_type",
            capture_id="CAP_123",
            amount=bad_amount,
            currency="USD",
        )


@pytest.mark.parametrize(
    "bad_decimal",
    [
        Decimal("NaN"),
        Decimal("Infinity"),
        Decimal("-Infinity"),
        Decimal("0"),
        Decimal("0.00"),
        Decimal("-1.00"),
        Decimal("10.123"),
        Decimal("10.000"),
    ],
)
def test_create_refund_rejects_invalid_decimal_amounts(bad_decimal: Decimal) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("No HTTP call should happen on invalid decimal amount")

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    client = PayPalClient(_make_config(), http_client=http_client)

    with pytest.raises(ValueError):
        client.create_refund(
            paypal_request_id="pp_req_bad_dec",
            capture_id="CAP_123",
            amount=bad_decimal,
            currency="USD",
        )


def test_create_refund_malformed_json_2xx_raises_timeout_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/oauth2/token":
            return _oauth_success_response()
        return httpx.Response(201, content=b"not-json", headers={"paypal-debug-id": "dbg_bad_json"})

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    client = PayPalClient(_make_config(), http_client=http_client)

    with pytest.raises(TimeoutError) as exc_info:
        client.create_refund(
            paypal_request_id="pp_req_bad_json",
            capture_id="CAP_123",
            amount=Decimal("5.00"),
            currency="USD",
        )
    assert isinstance(exc_info.value, PayPalTimeoutError)
    assert exc_info.value.paypal_debug_id == "dbg_bad_json"


def test_get_capture_malformed_json_raises_api_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/oauth2/token":
            return _oauth_success_response()
        return httpx.Response(200, content=b"{invalid")

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    client = PayPalClient(_make_config(), http_client=http_client)

    with pytest.raises(PayPalAPIError):
        client.get_capture("CAP_BAD_JSON")


def test_no_secret_or_token_leakage_in_logs(caplog: pytest.LogCaptureFixture) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/oauth2/token":
            return _oauth_success_response(access_token="tok_super_sensitive_999")
        return httpx.Response(
            201,
            json={
                "id": "REF_LOG_CHECK",
                "status": "COMPLETED",
                "amount": {"value": "5.00", "currency_code": "USD"},
            },
            headers={"paypal-debug-id": "dbg_log"},
        )

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    client = PayPalClient(_make_config(), http_client=http_client)

    with caplog.at_level(logging.DEBUG, logger="backend.paypal"):
        client.create_refund(
            paypal_request_id="pp_req_log",
            capture_id="CAP_LOG",
            amount=Decimal("5.00"),
            currency="USD",
        )

    combined_logs = "\n".join(record.getMessage() for record in caplog.records)
    assert "super_secret_value_xyz" not in combined_logs
    assert "tok_super_sensitive_999" not in combined_logs
    assert "Basic " not in combined_logs
