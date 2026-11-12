"""Vaultline AI Refund Agent orchestrator.

Architecture:
  Customer Email (untrusted)
    -> GeminiClient (extract + strict Pydantic validation; fail closed on outage/error)
    -> MCPAgentClient (stdio MCP SDK 2.x -> vaultline_mcp)
       -> get_agent_policy()
       -> request_refund()  [backend is sole authority for ALLOW / ESCALATE / BLOCK]
       -> get_request_status()

The agent never calls FastAPI or PayPal directly, never sees PayPal credentials,
and never sends VAULTLINE_AGENT_KEY to Gemini or as a model-supplied argument.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from .config import AgentConfig
from .gemini_client import GeminiClient, GeminiError
from .mcp_client import MCPAgentClient, MCPClientError
from .models import AgentRunResult, RefundProposal
from .security import detect_prompt_injection

security_log = logging.getLogger("vaultline_agent.security")


def _log_security_event(
    *,
    event: str,
    status: str,
    source: str,
    error_code: str | None,
    client_request_id: str | None = None,
    signal_count: int = 0,
) -> None:
    """Emit structured, privacy-safe operational security logs (no email text or secrets)."""
    payload = {
        "event": event,
        "status": status,
        "source": source,
        "error_code": error_code,
        "client_request_id": client_request_id,
        "signal_count": signal_count,
    }
    security_log.warning("vaultline_agent_event %s", json.dumps(payload, sort_keys=True))


def _first_mcp_error(exc: BaseException) -> MCPClientError:
    if isinstance(exc, MCPClientError):
        return exc
    if isinstance(exc, BaseExceptionGroup):
        for sub in exc.exceptions:
            return _first_mcp_error(sub)
    raise exc


def _merge_indicators(ai_indicators: list[str], deterministic_signals: list[str]) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for item in [*ai_indicators, *deterministic_signals]:
        key = item.strip()
        if key and key not in seen:
            seen.add(key)
            merged.append(key)
    return merged[:20]


def _classify_mcp_outcome(mcp_result: dict[str, Any]) -> tuple[str, str | None, str]:
    """Conservatively classify an MCP refund response.

    Never reports success unless execution_status == 'COMPLETED' and a refund ID is present.
    """
    decision = str(mcp_result.get("decision") or "").upper()
    execution = str(mcp_result.get("execution_status") or "").upper()
    refund_id = mcp_result.get("refund_id") or mcp_result.get("paypal_refund_id")
    outcome = str(mcp_result.get("outcome") or "").strip()

    if decision == "BLOCK":
        return (
            "BLOCKED",
            None,
            outcome or "BLOCKED by Vaultline deterministic policy. No refund was executed.",
        )
    if decision == "ESCALATE":
        return (
            "ESCALATED",
            None,
            outcome or "ESCALATED: awaiting authenticated human merchant approval. No refund was executed.",
        )
    if execution == "COMPLETED" and refund_id:
        return (
            "COMPLETED",
            str(refund_id),
            outcome or f"Refund completed and verified with PayPal (refund id {refund_id}).",
        )
    if execution in {"UNKNOWN", "RECONCILING"}:
        return (
            "UNCERTAIN",
            None,
            outcome
            or "Refund outcome is UNKNOWN/RECONCILING. Do NOT report success or submit a new request; check status with get_request_status.",
        )
    if execution in {"FAILED", "CANCELLED"}:
        return (
            "ERROR",
            None,
            outcome or f"Refund execution {execution}. No refund was completed.",
        )
    return (
        "UNCERTAIN",
        None,
        outcome or f"Refund not confirmed completed (decision={decision or 'unknown'}, execution_status={execution or 'unknown'}).",
    )


class RefundAgent:
    """AI refund-proposal agent backed by Gemini and the Vaultline MCP server."""

    def __init__(
        self,
        config: AgentConfig | None = None,
        *,
        gemini_client: GeminiClient | None = None,
        mcp_client: MCPAgentClient | None = None,
    ):
        self._config = config or AgentConfig.from_env()
        self._gemini = gemini_client or GeminiClient(self._config)
        self._mcp = mcp_client or MCPAgentClient(self._config)

    async def process_email(
        self,
        email_text: str,
        *,
        client_request_id: str | None = None,
    ) -> AgentRunResult:
        """Process an untrusted customer email through security checks, Gemini extraction, and Vaultline MCP."""
        injection_signals = detect_prompt_injection(email_text)
        if injection_signals:
            _log_security_event(
                event="security_halt",
                status="REVIEW_REQUIRED",
                source="deterministic_security_check",
                error_code="PROMPT_INJECTION_DETECTED",
                client_request_id=client_request_id,
                signal_count=len(injection_signals),
            )
            return AgentRunResult(
                source="deterministic_security_check",
                ai_extracted=False,
                status="REVIEW_REQUIRED",
                proposal=None,
                prompt_injection_detected=True,
                injection_signals=injection_signals,
                policy=None,
                mcp_result=None,
                client_request_id=None,
                refund_id=None,
                message=(
                    "Prompt-injection attempt detected in untrusted customer email "
                    f"({', '.join(injection_signals)}). Processing halted for manual review; "
                    "Gemini extraction and refund submission were not executed."
                ),
                error_code="PROMPT_INJECTION_DETECTED",
            )

        try:
            proposal = await self._gemini.extract_refund_proposal(email_text)
        except GeminiError as exc:
            # Fail closed: never fabricate AI output and never submit a refund without valid AI extraction.
            _log_security_event(
                event="extraction_failed",
                status="EXTRACTION_FAILED",
                source="deterministic_fallback",
                error_code=exc.code,
                client_request_id=client_request_id,
                signal_count=0,
            )
            return AgentRunResult(
                source="deterministic_fallback",
                ai_extracted=False,
                status="EXTRACTION_FAILED",
                proposal=None,
                prompt_injection_detected=bool(injection_signals),
                injection_signals=injection_signals,
                policy=None,
                mcp_result=None,
                client_request_id=None,
                refund_id=None,
                message=(
                    f"Deterministic fallback (no AI extraction performed): {exc.message} "
                    "Processing stopped and no refund was submitted."
                ),
                error_code=exc.code,
            )

        merged_indicators = _merge_indicators(proposal.suspicious_indicators, injection_signals)
        enriched_proposal = RefundProposal(
            capture_id=proposal.capture_id,
            amount=proposal.amount,
            currency=proposal.currency,
            reason=proposal.reason,
            suspicious_indicators=merged_indicators,
            risk_explanation=proposal.risk_explanation,
        )
        prompt_injection_detected = bool(injection_signals or proposal.suspicious_indicators)

        if proposal.suspicious_indicators:
            _log_security_event(
                event="security_halt",
                status="REVIEW_REQUIRED",
                source="gemini",
                error_code="SUSPICIOUS_INDICATORS_DETECTED",
                client_request_id=client_request_id,
                signal_count=len(merged_indicators),
            )
            return AgentRunResult(
                source="gemini",
                ai_extracted=True,
                status="REVIEW_REQUIRED",
                proposal=enriched_proposal,
                prompt_injection_detected=True,
                injection_signals=injection_signals,
                policy=None,
                mcp_result=None,
                client_request_id=None,
                refund_id=None,
                message=(
                    "Suspicious indicators detected during AI extraction. "
                    "Processing halted for manual review; no refund was submitted or executed."
                ),
                error_code="SUSPICIOUS_INDICATORS_DETECTED",
            )

        cid = client_request_id or f"agent-{uuid.uuid4()}"
        policy: dict[str, Any] | None = None

        mcp_result: dict[str, Any] | None = None
        mcp_error: MCPClientError | None = None
        try:
            async with self._mcp.session() as session:
                policy = await session.get_agent_policy()
                mcp_result = await session.request_refund(
                    capture_id=enriched_proposal.capture_id,
                    amount=enriched_proposal.amount,
                    currency=enriched_proposal.currency,
                    reason=enriched_proposal.reason,
                    client_request_id=cid,
                )
        except* MCPClientError as eg:
            mcp_error = _first_mcp_error(eg)

        if mcp_error is not None:
            status = "UNCERTAIN" if mcp_error.uncertain else "ERROR"
            code = "MCP_OUTCOME_UNCERTAIN" if mcp_error.uncertain else "MCP_ERROR"
            return AgentRunResult(
                source="gemini",
                ai_extracted=True,
                status=status,
                proposal=enriched_proposal,
                prompt_injection_detected=prompt_injection_detected,
                injection_signals=injection_signals,
                policy=policy,
                mcp_result=None,
                client_request_id=cid,
                refund_id=None,
                message=mcp_error.message,
                error_code=code,
            )

        assert mcp_result is not None

        status, refund_id, message = _classify_mcp_outcome(mcp_result)
        return AgentRunResult(
            source="gemini",
            ai_extracted=True,
            status=status,  # type: ignore[arg-type]
            proposal=enriched_proposal,
            prompt_injection_detected=prompt_injection_detected,
            injection_signals=injection_signals,
            policy=policy,
            mcp_result=mcp_result,
            client_request_id=str(mcp_result.get("client_request_id") or cid),
            refund_id=refund_id,
            message=message,
            error_code=None,
        )

    async def check_status(self, request_id: str) -> dict[str, Any]:
        """Retrieve subsequent refund status strictly through the MCP `get_request_status` tool."""
        async with self._mcp.session() as session:
            return await session.get_request_status(request_id)
