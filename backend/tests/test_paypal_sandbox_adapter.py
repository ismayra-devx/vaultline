"""PayPal Sandbox adapter tests against a simulated PayPal API (httpx.MockTransport)."""

from __future__ import annotations

import json
from decimal import Decimal

import httpx
import pytest

from backend.paypal.sandbox_adapter import (
    PayPalAuthError,
    PayPalConfigError,
    PayPalNotFound,
    PayPalOutcomeUnknown,
    PayPalSandboxAdapter,
    PayPalUnavailable,
    PayPalValidationError,
)

SECRET = "sb-secret-DO-NOT-LEAK"


class FakePayPal:
    """Minimal stateful PayPal Sandbox: token, captures, idempotent refunds."""

    def __init__(self):
        self.calls: list[httpx.Request] = []
        self.refunds: dict[str, dict] = {}
        self.by_request_id: dict[str, str] = {}
        self.refund_mode = "ok"  # ok | timeout | 500 | 409 | 422 | malformed | pending
        self.verify_fails = False
        self.token_status = 200
        self.tokens_issued = 0
        self.reject_first_bearer = False

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.calls.append(req)
        path = req.url.path
        if path == "/v1/oauth2/token":
            if self.token_status != 200:
                return httpx.Response(self.token_status, json={"error": "invalid_client"})
            self.tokens_issued += 1
            return httpx.Response(200, json={"access_token": f"tok{self.tokens_issued}", "expires_in": 32400})
        if self.reject_first_bearer and req.headers.get("authorization") == "Bearer tok1":
            return httpx.Response(401, json={"name": "AUTHENTICATION_FAILURE"})
        if req.method == "GET" and path == "/v2/checkout/orders/ORD1":
            up = lambda c: [{"rel": "up", "href": f"https://api.sandbox.paypal.com/v2/payments/captures/{c}"}]
            return httpx.Response(200, json={"id": "ORD1", "purchase_units": [{"payments": {
                "captures": [{"id": "PARTIAL1"}, {"id": "OTHERCAP"}],
                "refunds": [
                    {"id": "R1", "status": "COMPLETED", "amount": {"value": "20.00", "currency_code": "USD"}, "links": up("PARTIAL1")},
                    {"id": "R2", "status": "PENDING", "amount": {"value": "5.00", "currency_code": "USD"}, "links": up("PARTIAL1")},
                    {"id": "R3", "status": "FAILED", "amount": {"value": "99.00", "currency_code": "USD"}, "links": up("PARTIAL1")},
                    {"id": "R4", "status": "COMPLETED", "amount": {"value": "7.00", "currency_code": "USD"}, "links": up("OTHERCAP")},
                ]}}]})
        if req.method == "GET" and path.startswith("/v2/payments/captures/") and path.rsplit("/", 1)[-1] in ("PARTIAL1", "REFUNDED1", "NOORDER1"):
            cid = path.rsplit("/", 1)[-1]
            status = {"PARTIAL1": "PARTIALLY_REFUNDED", "REFUNDED1": "REFUNDED", "NOORDER1": "PARTIALLY_REFUNDED"}[cid]
            links = [] if cid == "NOORDER1" else [{"rel": "up", "href": "https://api.sandbox.paypal.com/v2/checkout/orders/ORD1"}]
            return httpx.Response(200, json={"id": cid, "status": status, "amount": {"value": "1000.00", "currency_code": "USD"}, "links": links})
        if req.method == "GET" and path.startswith("/v2/payments/captures/"):
            cid = path.rsplit("/", 1)[-1]
            if cid == "MISSING":
                return httpx.Response(404, json={"name": "RESOURCE_NOT_FOUND", "details": [{"issue": "INVALID_RESOURCE_ID"}]}, headers={"PayPal-Debug-Id": "dbg404"})
            return httpx.Response(200, json={"id": cid, "status": "COMPLETED", "amount": {"value": "30.00", "currency_code": "USD"}})
        if req.method == "POST" and path.endswith("/refund"):
            mode = self.refund_mode
            if mode == "timeout":
                raise httpx.ReadTimeout("no response", request=req)
            if mode in ("500", "409", "422"):
                issue = {"422": "REFUND_AMOUNT_EXCEEDED", "409": "DUPLICATE_REQUEST_ID", "500": "INTERNAL_SERVER_ERROR"}[mode]
                return httpx.Response(int(mode), json={"name": issue, "details": [{"issue": issue}]}, headers={"PayPal-Debug-Id": "dbg" + mode})
            if mode == "malformed":
                return httpx.Response(201, content=b"<html>oops</html>")
            rid_key = req.headers["paypal-request-id"]
            body = json.loads(req.content)
            if rid_key not in self.by_request_id:  # idempotent on PayPal-Request-Id
                rid = f"REF{len(self.refunds) + 1:04d}"
                self.by_request_id[rid_key] = rid
                self.refunds[rid] = {"id": rid, "status": "PENDING" if mode == "pending" else "COMPLETED", "amount": body["amount"],
                                     "links": [{"rel": "up", "href": f"https://api-m.sandbox.paypal.com/v2/payments/captures/{path.split('/')[-2]}"}]}
            return httpx.Response(201, json=self.refunds[self.by_request_id[rid_key]])
        if req.method == "GET" and path.startswith("/v2/payments/refunds/"):
            if self.verify_fails:
                return httpx.Response(503, json={"name": "SERVICE_UNAVAILABLE"})
            rid = path.rsplit("/", 1)[-1]
            return httpx.Response(200, json=self.refunds[rid]) if rid in self.refunds else httpx.Response(404, json={"name": "RESOURCE_NOT_FOUND"})
        return httpx.Response(404, json={})


@pytest.fixture
def pp():
    return FakePayPal()


@pytest.fixture
def adapter(pp):
    return PayPalSandboxAdapter("client-id", SECRET, transport=httpx.MockTransport(pp))


def money_calls(pp):
    return [c for c in pp.calls if c.method == "POST" and c.url.path.endswith("/refund")]


def test_only_sandbox_host_is_allowed():
    for bad in ("https://api-m.paypal.com", "http://api-m.sandbox.paypal.com", "https://evil.example"):
        with pytest.raises(PayPalConfigError):
            PayPalSandboxAdapter("id", SECRET, base_url=bad)


def test_missing_credentials_rejected():
    with pytest.raises(PayPalConfigError):
        PayPalSandboxAdapter("", "")


def test_repr_hides_secret(adapter):
    assert SECRET not in repr(adapter)


def test_get_capture_normalizes(adapter):
    cap = adapter.get_capture("CAPTURE1")
    assert (cap.capture_id, cap.status, cap.amount, cap.currency) == ("CAPTURE1", "COMPLETED", Decimal("30.00"), "USD")


def test_get_capture_not_found(adapter):
    with pytest.raises(PayPalNotFound) as e:
        adapter.get_capture("MISSING")
    assert e.value.debug_id == "dbg404"


def test_refund_sends_idempotency_header_and_decimal_string(adapter, pp):
    r = adapter.refund_capture(paypal_request_id="exec-123", capture_id="CAPTURE1", amount=Decimal("5.00"))
    (call,) = money_calls(pp)
    assert call.headers["paypal-request-id"] == "exec-123"
    assert json.loads(call.content) == {"amount": {"value": "5.00", "currency_code": "USD"}}
    assert r.status == "COMPLETED" and r.refund_id == "REF0001" and r.amount == Decimal("5.00")
    # verified by re-reading the refund resource
    assert any(c.method == "GET" and c.url.path == "/v2/payments/refunds/REF0001" for c in pp.calls)


def test_retry_with_same_request_id_does_not_double_refund(adapter, pp):
    a = adapter.refund_capture(paypal_request_id="exec-same", capture_id="CAPTURE1", amount=Decimal("5.00"))
    b = adapter.refund_capture(paypal_request_id="exec-same", capture_id="CAPTURE1", amount=Decimal("5.00"))
    assert a.refund_id == b.refund_id and len(pp.refunds) == 1


@pytest.mark.parametrize("mode", ["timeout", "500", "409", "malformed"])
def test_uncertain_outcomes_raise_timeout_subclass(adapter, pp, mode):
    pp.refund_mode = mode
    with pytest.raises(PayPalOutcomeUnknown) as e:
        adapter.refund_capture(paypal_request_id="exec-u", capture_id="CAPTURE1", amount=Decimal("5.00"))
    assert isinstance(e.value, TimeoutError)  # service maps TimeoutError -> UNKNOWN, never FAILED/COMPLETED
    assert len(money_calls(pp)) == 1  # never silently resubmits


def test_definite_rejection_is_not_unknown(adapter, pp):
    pp.refund_mode = "422"
    with pytest.raises(PayPalValidationError) as e:
        adapter.refund_capture(paypal_request_id="exec-r", capture_id="CAPTURE1", amount=Decimal("5.00"))
    assert not isinstance(e.value, TimeoutError) and e.value.issue == "REFUND_AMOUNT_EXCEEDED"


def test_pending_is_reported_as_pending(adapter, pp):
    pp.refund_mode = "pending"
    assert adapter.refund_capture(paypal_request_id="exec-p", capture_id="CAPTURE1", amount=Decimal("5.00")).status == "PENDING"


def test_unverifiable_success_is_pending_not_completed(adapter, pp):
    pp.verify_fails = True
    r = adapter.refund_capture(paypal_request_id="exec-v", capture_id="CAPTURE1", amount=Decimal("5.00"))
    assert r.status == "PENDING" and r.refund_id == "REF0001"


def test_unknown_paypal_status_never_becomes_completed(adapter, pp):
    adapter.refund_capture(paypal_request_id="exec-s", capture_id="CAPTURE1", amount=Decimal("5.00"))
    pp.refunds["REF0001"]["status"] = "SOMETHING_NEW"
    assert adapter.get_refund("REF0001").status == "PENDING"


@pytest.mark.parametrize("kwargs", [
    dict(amount=5.0),
    dict(amount=Decimal("0")),
    dict(amount=Decimal("-1")),
    dict(amount=Decimal("1.001")),
    dict(currency="EUR"),
    dict(capture_id="../../v1/oauth2/token"),
    dict(paypal_request_id=""),
])
def test_invalid_refund_inputs_never_reach_paypal(adapter, pp, kwargs):
    args = dict(paypal_request_id="exec-x", capture_id="CAPTURE1", amount=Decimal("5.00"), currency="USD") | kwargs
    with pytest.raises(PayPalValidationError):
        adapter.refund_capture(**args)
    assert money_calls(pp) == []


def test_bad_credentials(pp):
    pp.token_status = 401
    a = PayPalSandboxAdapter("id", SECRET, transport=httpx.MockTransport(pp))
    with pytest.raises(PayPalAuthError):
        a.get_capture("CAPTURE1")


def test_expired_token_is_refreshed_once(adapter, pp):
    pp.reject_first_bearer = True
    assert adapter.get_capture("CAPTURE1").status == "COMPLETED"
    assert pp.tokens_issued == 2


def test_token_is_cached(adapter, pp):
    adapter.get_capture("C1")
    adapter.get_capture("C2")
    assert pp.tokens_issued == 1


def test_connect_failure_is_definite_not_unknown(pp):
    def refuse(req):
        if req.url.path == "/v1/oauth2/token":
            return pp(req)
        raise httpx.ConnectError("refused", request=req)
    a = PayPalSandboxAdapter("id", SECRET, transport=httpx.MockTransport(refuse))
    with pytest.raises(PayPalUnavailable):
        a.refund_capture(paypal_request_id="exec-c", capture_id="CAPTURE1", amount=Decimal("5.00"))


def test_secret_never_in_request_except_basic_auth(adapter, pp):
    adapter.refund_capture(paypal_request_id="exec-l", capture_id="CAPTURE1", amount=Decimal("5.00"))
    for c in pp.calls:
        assert SECRET not in str(c.url) and SECRET.encode() not in c.content


def test_refunded_total_reads_paypal_order_history(adapter):
    # COMPLETED 20 + PENDING 5 for this capture; FAILED and other-capture refunds ignored.
    assert adapter.get_refunded_total("PARTIAL1") == Decimal("25.00")


def test_refunded_total_without_order_link_refuses_to_guess(adapter):
    with pytest.raises(PayPalUnavailable):
        adapter.get_refunded_total("NOORDER1")
