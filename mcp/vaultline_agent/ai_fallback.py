"""Orchestrator for Gemini + Groq AI provider fallback with strict validation and observability."""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from .config import AgentConfig
from .gemini_client import GeminiClient, GeminiError, GeminiUnavailableError, GeminiValidationError
from .groq_client import GroqClient, GroqError, GroqUnavailableError, GroqValidationError
from .models import RefundProposal

ai_log = logging.getLogger("vaultline_agent.ai")


class AIProviderError(Exception):
    """Base error for combined AI provider failures."""

    def __init__(self, message: str, *, code: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.message = message
        self.code = code
        self.details = details or {}


class AllAIProvidersUnavailableError(AIProviderError):
    """Raised when both primary (Gemini) and fallback (Groq) providers fail."""


class AIFallbackClient:
    """Orchestrates structured proposal extraction with Gemini as primary and Groq as fallback."""

    def __init__(
        self,
        config: AgentConfig,
        *,
        gemini_client: GeminiClient | None = None,
        groq_client: GroqClient | None = None,
    ):
        self._config = config
        self._gemini = gemini_client or GeminiClient(config)
        self._groq = groq_client or GroqClient(config)

    @property
    def gemini(self) -> GeminiClient:
        return self._gemini

    @property
    def groq(self) -> GroqClient:
        return self._groq

    async def extract_refund_proposal(
        self,
        email_text: str,
        *,
        correlation_id: str | None = None,
    ) -> tuple[RefundProposal, dict[str, Any]]:
        """Attempt Gemini first. Fail over to Groq if Gemini is unavailable, rate-limited, timed out, or returns 5xx.

        Never triggers fallback for empty or malformed user input.
        Returns (proposal, metadata) describing provider used, latency, and failover status.
        """
        # Do not trigger fallback for empty/malformed user input
        if not isinstance(email_text, str) or not email_text.strip():
            raise GeminiValidationError(
                "Customer email content is empty.",
                code="EMPTY_EMAIL",
            )

        metadata: dict[str, Any] = {
            "correlation_id": correlation_id,
            "primary_provider": "gemini",
            "fallback_provider": "groq",
            "fallback_used": False,
            "fallback_reason": None,
            "provider_used": None,
            "latency_ms": None,
        }

        # ------------------------------------------------------------------
        # 1. Primary: Google Gemini
        # ------------------------------------------------------------------
        gemini_start = time.perf_counter()
        gemini_error: GeminiError | None = None
        try:
            proposal = await self._gemini.extract_refund_proposal(
                email_text,
                correlation_id=correlation_id,
            )
            gemini_duration_ms = round((time.perf_counter() - gemini_start) * 1000, 2)
            metadata["provider_used"] = "gemini"
            metadata["latency_ms"] = gemini_duration_ms
            self._log_event(
                event="ai_extraction_success",
                provider="gemini",
                fallback_used=False,
                latency_ms=gemini_duration_ms,
                correlation_id=correlation_id,
                error_code=None,
            )
            return proposal, metadata
        except GeminiError as exc:
            gemini_duration_ms = round((time.perf_counter() - gemini_start) * 1000, 2)
            gemini_error = exc
            metadata["fallback_used"] = True
            metadata["fallback_reason"] = exc.code
            metadata["gemini_latency_ms"] = gemini_duration_ms
            self._log_event(
                event="ai_fallback_triggered",
                provider="gemini",
                fallback_used=True,
                fallback_reason=exc.code,
                latency_ms=gemini_duration_ms,
                correlation_id=correlation_id,
                error_code=exc.code,
            )
            ai_log.warning(
                "Gemini extraction failed (%s: %s); failing over to secondary provider Groq (cid=%s)",
                exc.code,
                exc.message,
                correlation_id,
            )

        # ------------------------------------------------------------------
        # 2. Fallback: Groq API
        # ------------------------------------------------------------------
        groq_start = time.perf_counter()
        try:
            proposal = await self._groq.extract_refund_proposal(
                email_text,
                correlation_id=correlation_id,
            )
            groq_duration_ms = round((time.perf_counter() - groq_start) * 1000, 2)
            metadata["provider_used"] = "groq"
            metadata["latency_ms"] = groq_duration_ms
            self._log_event(
                event="ai_extraction_success",
                provider="groq",
                fallback_used=True,
                fallback_reason=gemini_error.code if gemini_error else None,
                latency_ms=groq_duration_ms,
                correlation_id=correlation_id,
                error_code=None,
            )
            return proposal, metadata
        except GroqError as groq_exc:
            groq_duration_ms = round((time.perf_counter() - groq_start) * 1000, 2)
            metadata["groq_latency_ms"] = groq_duration_ms
            self._log_event(
                event="ai_extraction_failed_all_providers",
                provider="all",
                fallback_used=True,
                fallback_reason=gemini_error.code if gemini_error else None,
                latency_ms=groq_duration_ms,
                correlation_id=correlation_id,
                error_code=groq_exc.code,
            )
            ai_log.error(
                "Both Gemini and Groq AI providers failed (Gemini: %s, Groq: %s, cid=%s)",
                gemini_error.code if gemini_error else "unknown",
                groq_exc.code,
                correlation_id,
            )
            raise AllAIProvidersUnavailableError(
                f"Both AI providers unavailable. Gemini: {gemini_error.message if gemini_error else 'error'}; "
                f"Groq: {groq_exc.message}",
                code="AI_PROVIDERS_UNAVAILABLE",
                details={
                    "gemini_error": gemini_error.code if gemini_error else None,
                    "groq_error": groq_exc.code,
                    "correlation_id": correlation_id,
                },
            ) from groq_exc

    @staticmethod
    def _log_event(
        *,
        event: str,
        provider: str,
        fallback_used: bool,
        latency_ms: float | None,
        correlation_id: str | None,
        error_code: str | None,
        fallback_reason: str | None = None,
    ) -> None:
        payload = {
            "event": event,
            "provider": provider,
            "fallback_used": fallback_used,
            "fallback_reason": fallback_reason,
            "latency_ms": latency_ms,
            "correlation_id": correlation_id,
            "error_code": error_code,
        }
        ai_log.info("ai_telemetry %s", json.dumps(payload, sort_keys=True))
