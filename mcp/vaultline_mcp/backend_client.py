"""Thin HTTP client for the three agent-facing Vaultline backend endpoints.

This module performs NO authorization. FastAPI is the authority; this client only
forwards the server-side agent credential and translates responses into safe results.
"""

from __future__ import annotations

from typing import Any

import httpx

from .config import Settings


class BackendError(Exception):
    """A failure the agent should see as a tool error. `message` is safe to show."""

    def __init__(self, message: str, *, status: int | None = None, code: str | None = None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code


class OutcomeUncertain(BackendError):
    """A refund submission whose result is unknown (timeout / dropped connection)."""


def _safe_detail(resp: httpx.Response) -> tuple[str | None, str]:
    """Extract (code, message) from either {"error": {...}} or FastAPI {"detail": ...} bodies."""
    try:
        body = resp.json()
    except ValueError:
        return None, resp.reason_phrase or "Unexpected response"
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict):
            return err.get("code"), str(err.get("message") or "")
        detail = body.get("detail")
        if isinstance(detail, str):
            return None, detail
        if isinstance(detail, list):  # pydantic validation errors: keep only the messages
            msgs = [str(d.get("msg")) for d in detail if isinstance(d, dict) and d.get("msg")]
            return "VALIDATION_ERROR", "; ".join(msgs)[:500]
        if isinstance(detail, dict):
            return detail.get("code"), str(detail.get("message") or "")
    return None, "Unexpected response"


class VaultlineBackend:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        self._settings = settings
        self._client = httpx.AsyncClient(
            base_url=settings.api_base_url,
            timeout=settings.timeout_seconds,
            follow_redirects=False,  # never forward the bearer token to another host
            headers={"Authorization": f"Bearer {settings.agent_key}", "Accept": "application/json"},
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _send(self, method: str, path: str, *, json: dict[str, Any] | None = None, submitting: bool = False) -> dict[str, Any]:
        try:
            resp = await self._client.request(method, path, json=json)
        except (httpx.TimeoutException, httpx.RemoteProtocolError, httpx.ReadError, httpx.WriteError) as exc:
            if submitting:
                raise OutcomeUncertain(
                    "The Vaultline backend did not confirm whether this refund request was recorded. "
                    "Do NOT tell the customer a refund happened. Retry later with the SAME client_request_id "
                    "(Vaultline deduplicates it), or check with get_request_status.",
                    code="OUTCOME_UNCERTAIN",
                ) from exc
            raise BackendError("Vaultline backend timed out. No action was taken.", code="BACKEND_TIMEOUT") from exc
        except httpx.HTTPError as exc:
            raise BackendError(
                "Vaultline backend is unavailable. No refund was executed.", code="BACKEND_UNAVAILABLE"
            ) from exc

        if resp.is_redirect:
            raise BackendError("Unexpected redirect from Vaultline backend. No action was taken.", status=resp.status_code)
        if resp.status_code >= 400:
            code, detail = _safe_detail(resp)
            raise BackendError(_explain_status(resp.status_code, detail), status=resp.status_code, code=code)
        try:
            data = resp.json()
        except ValueError as exc:
            raise BackendError("Malformed response from Vaultline backend. Do not assume success.") from exc
        if not isinstance(data, dict):
            raise BackendError("Malformed response from Vaultline backend. Do not assume success.")
        return data

    async def submit_refund(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._send("POST", "/api/v1/refunds", json=payload, submitting=True)

    async def get_request(self, request_id: str) -> dict[str, Any]:
        # request_id is validated against a strict pattern by the tool before reaching here.
        return await self._send("GET", f"/api/v1/refunds/{request_id}")

    async def get_request_by_client_id(self, client_request_id: str) -> dict[str, Any]:
        # client_request_id is validated against CLIENT_ID_PATTERN before reaching here.
        return await self._send("GET", f"/api/v1/refunds/by-client-id/{client_request_id}")

    async def get_policy(self) -> dict[str, Any]:
        return await self._send("GET", "/api/v1/agents/me/policy")


def _explain_status(status: int, detail: str) -> str:
    suffix = f" Detail: {detail}" if detail else ""
    if status == 401:
        return "Vaultline rejected this agent's credential. No refund was executed." + suffix
    if status == 403:
        return "This agent is not permitted to perform that action. No refund was executed." + suffix
    if status == 404:
        return "Request not found for this agent." + suffix
    if status == 409:
        return "Conflict: this client_request_id was already used with a different payload. No new refund was created." + suffix
    if status in (400, 422):
        return "Vaultline rejected the request as invalid. No refund was executed." + suffix
    if status == 429:
        return "Rate limited by Vaultline. Wait before retrying. No refund was executed." + suffix
    return f"Vaultline backend error (HTTP {status}). Do not assume any refund happened." + suffix
