"""MCP SDK 2.x stdio client wrapper for communicating with the Vaultline MCP server.

Uses the same `mcp.Client` + `StdioServerParameters` launch pattern as
`mcp/tests/test_integration.py`. Credentials (`VAULTLINE_AGENT_KEY`) are passed
only to the MCP server subprocess environment and never to Gemini or tool arguments.
"""

from __future__ import annotations

import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

from mcp import Client, StdioServerParameters

from .config import AgentConfig

EXPECTED_MCP_TOOLS = ("get_agent_policy", "get_request_status", "request_refund")


class MCPClientError(Exception):
    """Raised when communicating with the Vaultline MCP server fails."""

    def __init__(self, message: str, *, uncertain: bool = False):
        super().__init__(message)
        self.message = message
        self.uncertain = uncertain


def _extract_error_text(result: Any) -> str:
    content = getattr(result, "content", None) or []
    parts = [getattr(item, "text", "") for item in content if getattr(item, "text", None)]
    return " ".join(parts).strip() or "MCP tool returned an error."


def _extract_structured(result: Any) -> dict[str, Any]:
    data = getattr(result, "structured_content", None)
    if isinstance(data, dict):
        return data
    content = getattr(result, "content", None) or []
    for item in content:
        text = getattr(item, "text", None)
        if isinstance(text, str) and text.strip().startswith("{"):
            import json
            try:
                parsed = json.loads(text)
                if isinstance(parsed, dict):
                    return parsed
            except ValueError:
                pass
    return {}


class MCPAgentSession:
    """Active MCP session exposing only the three approved Vaultline tools."""

    def __init__(self, client: Any):
        self._client = client

    async def verify_tools(self) -> tuple[str, ...]:
        listed = await self._client.list_tools()
        names = tuple(sorted(t.name for t in listed.tools))
        if names != EXPECTED_MCP_TOOLS:
            raise MCPClientError(
                f"Unexpected MCP tool surface: expected {EXPECTED_MCP_TOOLS}, got {names}."
            )
        return names

    async def get_agent_policy(self) -> dict[str, Any]:
        res = await self._client.call_tool("get_agent_policy", {})
        if getattr(res, "is_error", False):
            raise MCPClientError(_extract_error_text(res))
        return _extract_structured(res)

    async def request_refund(
        self,
        *,
        capture_id: str,
        amount: str,
        reason: str,
        currency: str = "USD",
        client_request_id: str,
    ) -> dict[str, Any]:
        args = {
            "capture_id": capture_id,
            "amount": amount,
            "currency": currency,
            "reason": reason,
            "client_request_id": client_request_id,
        }
        res = await self._client.call_tool("request_refund", args)
        if getattr(res, "is_error", False):
            err_text = _extract_error_text(res)
            lower = err_text.lower()
            is_uncertain = (
                "did not confirm" in lower
                or "outcome_uncertain" in lower
                or "timed out" in lower
                or "timeout" in lower
            )
            raise MCPClientError(err_text, uncertain=is_uncertain)
        return _extract_structured(res)

    async def get_request_status(
        self,
        request_id: str | None = None,
        *,
        client_request_id: str | None = None,
    ) -> dict[str, Any]:
        if bool(request_id) == bool(client_request_id):
            raise MCPClientError("Provide exactly one of request_id or client_request_id.")
        args = {"request_id": request_id} if request_id is not None else {"client_request_id": client_request_id}
        res = await self._client.call_tool("get_request_status", args)
        if getattr(res, "is_error", False):
            raise MCPClientError(_extract_error_text(res))
        return _extract_structured(res)


class MCPAgentClient:
    """Launches `python -m vaultline_mcp` over stdio via MCP SDK 2.x."""

    def __init__(self, config: AgentConfig, *, mcp_cwd: Path | None = None):
        self._config = config
        # Run from `mcp/` so the repo folder `mcp/` does not shadow the `mcp` SDK package.
        self._mcp_cwd = mcp_cwd or Path(__file__).resolve().parents[1]

    @asynccontextmanager
    async def session(self) -> AsyncIterator[MCPAgentSession]:
        if not self._config.vaultline_agent_key:
            raise MCPClientError(
                "VAULTLINE_AGENT_KEY is not configured for the MCP server."
            )

        env = os.environ.copy()
        env["VAULTLINE_API_BASE_URL"] = self._config.vaultline_api_base_url
        env["VAULTLINE_AGENT_KEY"] = self._config.vaultline_agent_key
        env["VAULTLINE_TIMEOUT_SECONDS"] = str(self._config.vaultline_timeout_seconds)
        # Never pass GEMINI_API_KEY into the MCP server process.
        env.pop("GEMINI_API_KEY", None)

        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "vaultline_mcp"],
            env=env,
            cwd=str(self._mcp_cwd),
        )
        try:
            async with Client(params) as client:
                agent_session = MCPAgentSession(client)
                await agent_session.verify_tools()
                yield agent_session
        except BaseExceptionGroup as eg:
            mcp_errors = [e for e in eg.exceptions if isinstance(e, MCPClientError)]
            if mcp_errors and len(mcp_errors) == len(eg.exceptions):
                raise mcp_errors[0] from eg
            raise
