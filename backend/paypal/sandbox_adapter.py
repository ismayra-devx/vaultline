"""PayPal Payments API v2 adapter (Sandbox only).

Implements backend.adapter_interface.PaypalAdapter. Responsibilities: OAuth, capture lookup,
refund execution with a stable PayPal-Request-Id, refund verification and error mapping.
It makes NO authorization decisions; the Vaultline service calls it only after policy and
approval checks have passed.

Outcome rules (architecture doc §2.4 / §7.7):
- Definite rejections (400/401/403/404/422/429, or no connection at all) raise ordinary
  exceptions, which the service records as FAILED. PayPal did not move money.
- Uncertain outcomes (read timeout, dropped connection after sending, 409, 5xx, malformed
  success body) raise PayPalOutcomeUnknown, a TimeoutError subclass, so the service records
  UNKNOWN instead of FAILED. Retrying with the same PayPal-Request-Id is safe.
- A 2xx refund response is not trusted on its own: the refund resource is re-read. If it
  cannot be verified, or PayPal reports a status we don't recognise, the result is reported
  as PENDING (never COMPLETED) with its refund id kept for reconciliation.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from backend.adapter_interface import CaptureResult, PaypalAdapter, RefundResult

log = logging.getLogger("vaultline.paypal")

SANDBOX_BASE_URL = "https://api-m.sandbox.paypal.com"
ALLOWED_BASE_URLS = frozenset({SANDBOX_BASE_URL})  # live host deliberately absent for the MVP
SUPPORTED_CURRENCIES = frozenset({"USD"})
KNOWN_REFUND_STATUSES = frozenset({"COMPLETED", "PENDING", "FAILED", "CANCELLED"})
_ID_RE = re.compile(r"^[A-Za-z0-9-]{1,64}$")
_REQ_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,108}$")  # PayPal-Request-Id max 108 chars


class PayPalError(Exception):
    """Definite PayPal failure: the request was rejected, no money moved."""

    def __init__(self, message: str, *, status: int | None = None, debug_id: str | None = None, issue: str | None = None):
        super().__init__(message)
        self.status = status
        self.debug_id = debug_id
        self.issue = issue


class PayPalConfigError(PayPalError):
    pass


class PayPalAuthError(PayPalError):
    pass


class PayPalPermissionError(PayPalError):
    pass


class PayPalNotFound(PayPalError):
    pass


class PayPalValidationError(PayPalError):
    pass


class PayPalRateLimited(PayPalError):
    pass


class PayPalUnavailable(PayPalError):
    """Could not connect at all; nothing was sent, so nothing was processed."""


class PayPalOutcomeUnknown(TimeoutError):
    """The money-moving request may or may not have been processed. Must be reconciled."""

    def __init__(self, message: str, *, debug_id: str | None = None):
        super().__init__(message)
        self.debug_id = debug_id


def _money(value: Any) -> Decimal:
    try:
        d = Decimal(str(value))
    except (InvalidOperation, TypeError) as exc:
        raise ValueError("invalid money value") from exc
    if not d.is_finite():
        raise ValueError("invalid money value")
    return d


class PayPalSandboxAdapter(PaypalAdapter):
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        *,
        base_url: str = SANDBOX_BASE_URL,
        timeout_seconds: float = 20.0,
        transport: httpx.BaseTransport | None = None,
    ):
        base_url = base_url.rstrip("/")
        if base_url not in ALLOWED_BASE_URLS:
            raise PayPalConfigError("PayPal base URL is not allowlisted. Only the PayPal Sandbox is permitted.")
        if not client_id or not client_secret:
            raise PayPalConfigError("PayPal sandbox client id and secret are required.")
        self._client_id = client_id
        self._client_secret = client_secret
        self._http = httpx.Client(
            base_url=base_url,
            timeout=httpx.Timeout(timeout_seconds, connect=10.0),
            follow_redirects=False,
            transport=transport,
        )
        self._token: str | None = None
        self._token_expiry = 0.0
        self._lock = threading.Lock()

    def __repr__(self) -> str:  # never print credentials
        return "PayPalSandboxAdapter(base_url=%r)" % str(self._http.base_url)

    def close(self) -> None:
        self._http.close()

    # ------------------------------------------------------------------ OAuth
    def _access_token(self, force: bool = False) -> str:
        with self._lock:
            if not force and self._token and time.monotonic() < self._token_expiry:
                return self._token
            try:
                resp = self._http.post(
                    "/v1/oauth2/token",
                    data={"grant_type": "client_credentials"},
                    auth=(self._client_id, self._client_secret),
                    headers={"Accept": "application/json"},
                )
            except httpx.HTTPError as exc:
                raise PayPalUnavailable("Could not reach PayPal to obtain an access token.") from exc
            if resp.status_code in (400, 401):
                raise PayPalAuthError("PayPal rejected the sandbox client credentials.", status=resp.status_code)
            if resp.status_code != 200:
                raise PayPalUnavailable(f"PayPal token endpoint returned HTTP {resp.status_code}.", status=resp.status_code)
            body = resp.json()
            token = body.get("access_token")
            if not isinstance(token, str) or not token:
                raise PayPalUnavailable("PayPal token response was malformed.")
            # refresh a minute early
            self._token = token
            self._token_expiry = time.monotonic() + max(60, int(body.get("expires_in", 300)) - 60)
            return token

    # --------------------------------------------------------------- transport
    def _request(self, method: str, path: str, *, json: dict | None = None, extra_headers: dict | None = None, money_moving: bool = False) -> dict:
        for attempt in (1, 2):
            headers = {"Authorization": f"Bearer {self._access_token(force=attempt == 2)}", "Accept": "application/json"}
            if json is not None:
                headers["Content-Type"] = "application/json"
            headers.update(extra_headers or {})
            try:
                resp = self._http.request(method, path, json=json, headers=headers)
            except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                raise PayPalUnavailable("Could not connect to PayPal; the request was not sent.") from exc
            except httpx.HTTPError as exc:  # read/write timeout, dropped connection, protocol error
                if money_moving:
                    raise PayPalOutcomeUnknown("PayPal did not respond after the refund was sent; outcome unknown.") from exc
                raise PayPalUnavailable("PayPal request failed before a response was received.") from exc
            if resp.status_code == 401 and attempt == 1:
                continue  # token expired/revoked: PayPal did not process the call; retry once with a fresh token
            return self._handle(resp, money_moving=money_moving)
        raise PayPalAuthError("PayPal rejected the access token.")  # pragma: no cover

    def _handle(self, resp: httpx.Response, *, money_moving: bool) -> dict:
        debug_id = resp.headers.get("PayPal-Debug-Id")
        try:
            body = resp.json() if resp.content else {}
        except ValueError:
            body = None
        if 200 <= resp.status_code < 300:
            if not isinstance(body, dict):
                if money_moving:
                    raise PayPalOutcomeUnknown("PayPal returned a malformed success response; outcome unknown.", debug_id=debug_id)
                raise PayPalUnavailable("PayPal returned a malformed response.", status=resp.status_code, debug_id=debug_id)
            return body
        issue = None
        if isinstance(body, dict):
            details = body.get("details")
            if isinstance(details, list) and details and isinstance(details[0], dict):
                issue = details[0].get("issue")
            issue = issue or body.get("name")
        msg = f"PayPal HTTP {resp.status_code}" + (f" ({issue})" if issue else "") + (f", debug id {debug_id}" if debug_id else "")
        log.warning("paypal error status=%s issue=%s debug_id=%s", resp.status_code, issue, debug_id)
        s = resp.status_code
        if money_moving and (s == 409 or s >= 500):
            raise PayPalOutcomeUnknown(msg + "; outcome unknown, reconcile before retrying.", debug_id=debug_id)
        kw = dict(status=s, debug_id=debug_id, issue=issue)
        if s == 401:
            raise PayPalAuthError(msg, **kw)
        if s == 403:
            raise PayPalPermissionError(msg, **kw)
        if s == 404:
            raise PayPalNotFound(msg, **kw)
        if s in (400, 422):
            raise PayPalValidationError(msg, **kw)
        if s == 429:
            raise PayPalRateLimited(msg, **kw)
        raise PayPalUnavailable(msg, **kw)

    # ------------------------------------------------------------ public API
    def get_capture(self, capture_id: str) -> CaptureResult:
        if not _ID_RE.match(capture_id or ""):
            raise PayPalValidationError("Invalid capture id.")
        body = self._request("GET", f"/v2/payments/captures/{capture_id}")
        try:
            amount = body["amount"]
            return CaptureResult(
                capture_id=str(body["id"]),
                status=str(body["status"]),
                amount=_money(amount["value"]),
                currency=str(amount["currency_code"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise PayPalUnavailable("PayPal capture response was missing fields.") from exc

    def refund_capture(self, paypal_request_id: str, capture_id: str, amount: Decimal, currency: str = "USD", **kwargs: Any) -> RefundResult:
        if not paypal_request_id or not _REQ_ID_RE.match(paypal_request_id):
            raise PayPalValidationError("A stable PayPal-Request-Id is required.")
        if not _ID_RE.match(capture_id or ""):
            raise PayPalValidationError("Invalid capture id.")
        if currency not in SUPPORTED_CURRENCIES:
            raise PayPalValidationError("Only USD refunds are supported.")
        if isinstance(amount, float):
            raise PayPalValidationError("Money must be Decimal, never float.")
        amt = _money(amount)
        if amt <= 0 or amt.as_tuple().exponent < -2:
            raise PayPalValidationError("Refund amount must be positive with at most 2 decimal places.")

        body = self._request(
            "POST",
            f"/v2/payments/captures/{capture_id}/refund",
            json={"amount": {"value": f"{amt:.2f}", "currency_code": currency}},
            extra_headers={"PayPal-Request-Id": paypal_request_id, "Prefer": "return=representation"},
            money_moving=True,
        )
        refund_id = body.get("id")
        if not isinstance(refund_id, str) or not _ID_RE.match(refund_id):
            raise PayPalOutcomeUnknown("PayPal accepted the refund call but returned no usable refund id; outcome unknown.")

        # Do not trust the POST response alone: re-read the refund resource.
        try:
            verified = self.get_refund(refund_id)
        except Exception:  # noqa: BLE001 - any failure here means "not verified yet"
            log.warning("refund %s submitted but verification read failed; reporting PENDING", refund_id)
            return RefundResult(refund_id=refund_id, capture_id=capture_id, status="PENDING", amount=amt, currency=currency, paypal_debug_id=None)
        if verified.amount != amt or verified.currency != currency:
            log.error("refund %s amount/currency mismatch on verification; reporting PENDING", refund_id)
            return RefundResult(refund_id=refund_id, capture_id=capture_id, status="PENDING", amount=verified.amount, currency=verified.currency, paypal_debug_id=verified.paypal_debug_id)
        return RefundResult(refund_id=refund_id, capture_id=capture_id, status=verified.status, amount=verified.amount, currency=verified.currency, paypal_debug_id=verified.paypal_debug_id)

    def get_refund(self, refund_id: str) -> RefundResult:
        if not _ID_RE.match(refund_id or ""):
            raise PayPalValidationError("Invalid refund id.")
        body = self._request("GET", f"/v2/payments/refunds/{refund_id}")
        try:
            amount = body["amount"]
            status = str(body["status"]).upper()
            capture_id = None
            for link in body.get("links", []) or []:
                href = link.get("href", "") if isinstance(link, dict) else ""
                if link.get("rel") == "up" and "/captures/" in href:
                    capture_id = href.rstrip("/").rsplit("/", 1)[-1]
            return RefundResult(
                refund_id=str(body["id"]),
                capture_id=capture_id,
                # Unrecognised states are never upgraded to COMPLETED.
                status=status if status in KNOWN_REFUND_STATUSES else "PENDING",
                amount=_money(amount["value"]),
                currency=str(amount["currency_code"]),
                paypal_debug_id=None,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise PayPalUnavailable("PayPal refund response was missing fields.") from exc
