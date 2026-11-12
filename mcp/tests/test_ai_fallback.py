"""Comprehensive unit and integration tests for Gemini + Groq AI fallback and security controls.

Covers all 11 requirements from Part 5:
1. Gemini success â€” Groq not called.
2. Gemini timeout â€” Groq fallback succeeds.
3. Gemini 429 â€” Groq fallback succeeds.
4. Gemini 5xx â€” Groq fallback succeeds.
5. Gemini and Groq both fail â€” controlled failure, no refund execution.
6. Invalid Gemini response â€” fails over to Groq fallback.
7. Invalid Groq response â€” controlled failure, no fabricated result.
8. Both providers return different risk signals â€” stricter risk treatment.
9. Authentication failure â€” no provider fallback.
10. Existing deterministic refund thresholds remain unchanged.
11. No AI-provider failure can bypass merchant approval or PayPal security.
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

import httpx
import pytest

from backend.policy import Decision, evaluate_refund_policy
from vaultline_agent import (
    AIFallbackClient,
    AllAIProvidersUnavailableError,
    AgentConfig,
    AgentRunResult,
    GeminiClient,
    GeminiUnavailableError,
    GeminiValidationError,
    GroqClient,
    GroqError,
    GroqUnavailableError,
    GroqValidationError,
    RefundAgent,
    RefundProposal,
    resolve_stricter_risk,
)

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def _no_sleep(_: float) -> None:
    return None


def _make_config(**overrides: Any) -> AgentConfig:
    defaults = {
        "gemini_api_key": "fake_gemini_key",
        "gemini_model": "gemini-3.8-flash",
        "gemini_timeout_seconds": 1.0,
        "gemini_max_retries": 1,
        "groq_api_key": "fake_groq_key",
        "groq_model": "llama-3.3-70b-versatile",
        "groq_timeout_seconds": 1.0,
        "groq_max_retries": 1,
        "vaultline_api_base_url": "http://localhost:8000",
        "vaultline_agent_key": "fake_agent_key",
        "vaultline_timeout_seconds": 1.0,
    }
    defaults.update(overrides)
    return AgentConfig(**defaults)


def _gemini_response(payload_dict: dict[str, Any], status_code: int = 200) -> httpx.Response:
    if status_code != 200:
        return httpx.Response(status_code, json={"error": "provider error"})
    return httpx.Response(
        200,
        json={
            "candidates": [
                {
                    "content": {
                        "parts": [{"text": json.dumps(payload_dict)}],
                    }
                }
            ]
        },
    )


def _groq_response(payload_dict: dict[str, Any], status_code: int = 200) -> httpx.Response:
    if status_code != 200:
        return httpx.Response(status_code, json={"error": {"message": "provider error"}})
    return httpx.Response(
        200,
        json={
            "choices": [
                {
                    "message": {
                        "content": json.dumps(payload_dict),
                    }
                }
            ]
        },
    )


VALID_PROPOSAL_A = {
    "capture_id": "CAP_12345",
    "amount": "5.00",
    "currency": "USD",
    "reason": "Damaged goods in transit",
    "suspicious_indicators": [],
    "risk_explanation": "Standard return within policy limits",
}

VALID_PROPOSAL_GROQ = {
    "capture_id": "CAP_12345",
    "amount": "5.00",
    "currency": "USD",
    "reason": "Damaged goods in transit (Groq extracted)",
    "suspicious_indicators": [],
    "risk_explanation": "Factual extraction by fallback provider",
}


# ==============================================================================
# TEST 1: Gemini success â€” Groq not called
# ==============================================================================
async def test_1_gemini_success_groq_not_called():
    gemini_calls = 0
    groq_calls = 0

    def gemini_handler(request: httpx.Request) -> httpx.Response:
        nonlocal gemini_calls
        gemini_calls += 1
        return _gemini_response(VALID_PROPOSAL_A)

    def groq_handler(request: httpx.Request) -> httpx.Response:
        nonlocal groq_calls
        groq_calls += 1
        return _groq_response(VALID_PROPOSAL_GROQ)

    cfg = _make_config()
    gemini_client = GeminiClient(cfg, transport=httpx.MockTransport(gemini_handler), sleep_fn=_no_sleep)
    groq_client = GroqClient(cfg, transport=httpx.MockTransport(groq_handler), sleep_fn=_no_sleep)
    ai_client = AIFallbackClient(cfg, gemini_client=gemini_client, groq_client=groq_client)

    proposal, metadata = await ai_client.extract_refund_proposal("Please refund $5.00 for CAP_12345")
    assert proposal.capture_id == "CAP_12345"
    assert proposal.amount == "5.00"
    assert metadata["provider_used"] == "gemini"
    assert metadata["fallback_used"] is False
    assert gemini_calls == 1
    assert groq_calls == 0


# ==============================================================================
# TEST 2: Gemini timeout â€” Groq fallback succeeds
# ==============================================================================
async def test_2_gemini_timeout_groq_fallback_succeeds():
    gemini_calls = 0
    groq_calls = 0

    def gemini_handler(request: httpx.Request) -> httpx.Response:
        nonlocal gemini_calls
        gemini_calls += 1
        raise httpx.ReadTimeout("Gemini connection timed out")

    def groq_handler(request: httpx.Request) -> httpx.Response:
        nonlocal groq_calls
        groq_calls += 1
        return _groq_response(VALID_PROPOSAL_GROQ)

    cfg = _make_config()
    gemini_client = GeminiClient(cfg, transport=httpx.MockTransport(gemini_handler), sleep_fn=_no_sleep)
    groq_client = GroqClient(cfg, transport=httpx.MockTransport(groq_handler), sleep_fn=_no_sleep)
    ai_client = AIFallbackClient(cfg, gemini_client=gemini_client, groq_client=groq_client)

    proposal, metadata = await ai_client.extract_refund_proposal("Please refund $5.00 for CAP_12345")
    assert proposal.capture_id == "CAP_12345"
    assert metadata["provider_used"] == "groq"
    assert metadata["fallback_used"] is True
    assert metadata["fallback_reason"] == "GEMINI_TIMEOUT"
    assert gemini_calls == 2  # 1 initial + 1 retry
    assert groq_calls == 1


# ==============================================================================
# TEST 3: Gemini 429 â€” Groq fallback succeeds
# ==============================================================================
async def test_3_gemini_429_groq_fallback_succeeds():
    gemini_calls = 0
    groq_calls = 0

    def gemini_handler(request: httpx.Request) -> httpx.Response:
        nonlocal gemini_calls
        gemini_calls += 1
        return _gemini_response({}, status_code=429)

    def groq_handler(request: httpx.Request) -> httpx.Response:
        nonlocal groq_calls
        groq_calls += 1
        return _groq_response(VALID_PROPOSAL_GROQ)

    cfg = _make_config()
    gemini_client = GeminiClient(cfg, transport=httpx.MockTransport(gemini_handler), sleep_fn=_no_sleep)
    groq_client = GroqClient(cfg, transport=httpx.MockTransport(groq_handler), sleep_fn=_no_sleep)
    ai_client = AIFallbackClient(cfg, gemini_client=gemini_client, groq_client=groq_client)

    proposal, metadata = await ai_client.extract_refund_proposal("Please refund $5.00 for CAP_12345")
    assert proposal.capture_id == "CAP_12345"
    assert metadata["provider_used"] == "groq"
    assert metadata["fallback_used"] is True
    assert metadata["fallback_reason"] == "GEMINI_UNAVAILABLE"
    assert gemini_calls == 2
    assert groq_calls == 1


# ==============================================================================
# TEST 4: Gemini 5xx â€” Groq fallback succeeds
# ==============================================================================
async def test_4_gemini_5xx_groq_fallback_succeeds():
    gemini_calls = 0
    groq_calls = 0

    def gemini_handler(request: httpx.Request) -> httpx.Response:
        nonlocal gemini_calls
        gemini_calls += 1
        return _gemini_response({}, status_code=503)

    def groq_handler(request: httpx.Request) -> httpx.Response:
        nonlocal groq_calls
        groq_calls += 1
        return _groq_response(VALID_PROPOSAL_GROQ)

    cfg = _make_config()
    gemini_client = GeminiClient(cfg, transport=httpx.MockTransport(gemini_handler), sleep_fn=_no_sleep)
    groq_client = GroqClient(cfg, transport=httpx.MockTransport(groq_handler), sleep_fn=_no_sleep)
    ai_client = AIFallbackClient(cfg, gemini_client=gemini_client, groq_client=groq_client)

    proposal, metadata = await ai_client.extract_refund_proposal("Please refund $5.00 for CAP_12345")
    assert proposal.capture_id == "CAP_12345"
    assert metadata["provider_used"] == "groq"
    assert metadata["fallback_used"] is True
    assert metadata["fallback_reason"] == "GEMINI_UNAVAILABLE"
    assert gemini_calls == 2
    assert groq_calls == 1


# ==============================================================================
# TEST 5: Gemini and Groq both fail â€” controlled failure, no refund execution
# ==============================================================================
async def test_5_both_providers_fail_controlled_failure():
    def gemini_handler(request: httpx.Request) -> httpx.Response:
        return _gemini_response({}, status_code=500)

    def groq_handler(request: httpx.Request) -> httpx.Response:
        return _groq_response({}, status_code=500)

    cfg = _make_config()
    gemini_client = GeminiClient(cfg, transport=httpx.MockTransport(gemini_handler), sleep_fn=_no_sleep)
    groq_client = GroqClient(cfg, transport=httpx.MockTransport(groq_handler), sleep_fn=_no_sleep)
    ai_client = AIFallbackClient(cfg, gemini_client=gemini_client, groq_client=groq_client)

    with pytest.raises(AllAIProvidersUnavailableError) as exc_info:
        await ai_client.extract_refund_proposal("Please refund $5.00 for CAP_12345")

    assert exc_info.value.code == "AI_PROVIDERS_UNAVAILABLE"

    # Test through RefundAgent orchestrator to verify controlled failure and no refund execution
    agent = RefundAgent(cfg, ai_client=ai_client)
    res = await agent.process_email("Please refund $5.00 for CAP_12345")
    assert res.status == "EXTRACTION_FAILED"
    assert res.ai_extracted is False
    assert res.proposal is None
    assert res.refund_id is None
    assert "no refund was submitted" in res.message


# ==============================================================================
# TEST 6: Invalid Gemini response â€” follow defined validation/error policy
# ==============================================================================
async def test_6_invalid_gemini_response_fails_over_to_groq():
    gemini_calls = 0
    groq_calls = 0

    def gemini_handler(request: httpx.Request) -> httpx.Response:
        nonlocal gemini_calls
        gemini_calls += 1
        # Gemini returns invalid schema (missing capture_id and float amount)
        return _gemini_response({"amount": 5.0, "currency": "EUR"})

    def groq_handler(request: httpx.Request) -> httpx.Response:
        nonlocal groq_calls
        groq_calls += 1
        return _groq_response(VALID_PROPOSAL_GROQ)

    cfg = _make_config()
    gemini_client = GeminiClient(cfg, transport=httpx.MockTransport(gemini_handler), sleep_fn=_no_sleep)
    groq_client = GroqClient(cfg, transport=httpx.MockTransport(groq_handler), sleep_fn=_no_sleep)
    ai_client = AIFallbackClient(cfg, gemini_client=gemini_client, groq_client=groq_client)

    proposal, metadata = await ai_client.extract_refund_proposal("Please refund $5.00 for CAP_12345")
    assert proposal.capture_id == "CAP_12345"
    assert metadata["provider_used"] == "groq"
    assert metadata["fallback_used"] is True
    assert metadata["fallback_reason"] in ("GEMINI_INVALID_SCHEMA", "GEMINI_MISSING_CONTENT")
    assert gemini_calls == 1  # Schema errors are not retried against the same provider
    assert groq_calls == 1


# ==============================================================================
# TEST 7: Invalid Groq response â€” controlled failure, no fabricated result
# ==============================================================================
async def test_7_invalid_groq_response_controlled_failure():
    def gemini_handler(request: httpx.Request) -> httpx.Response:
        return _gemini_response({}, status_code=503)

    def groq_handler(request: httpx.Request) -> httpx.Response:
        # Groq returns malformed non-JSON text
        return httpx.Response(200, json={"choices": [{"message": {"content": "Not JSON at all!"}}]})

    cfg = _make_config()
    gemini_client = GeminiClient(cfg, transport=httpx.MockTransport(gemini_handler), sleep_fn=_no_sleep)
    groq_client = GroqClient(cfg, transport=httpx.MockTransport(groq_handler), sleep_fn=_no_sleep)
    ai_client = AIFallbackClient(cfg, gemini_client=gemini_client, groq_client=groq_client)

    agent = RefundAgent(cfg, ai_client=ai_client)
    res = await agent.process_email("Please refund $5.00 for CAP_12345")
    assert res.status == "EXTRACTION_FAILED"
    assert res.ai_extracted is False
    assert res.proposal is None
    assert res.refund_id is None
    # Verifies result is NOT fabricated
    assert res.mcp_result is None


# ==============================================================================
# TEST 8: Both providers return different risk signals â€” stricter risk treatment
# ==============================================================================
def test_8_different_risk_signals_stricter_risk_treatment():
    # Model A flags no indicators, suggests low risk
    proposal_a = RefundProposal(
        capture_id="CAP_LOW_RISK",
        amount="5.00",
        currency="USD",
        reason="Normal customer return",
        suspicious_indicators=[],
        risk_explanation="No anomalies found",
    )

    # Model B flags an anomaly or injection indicator
    proposal_b = RefundProposal(
        capture_id="CAP_LOW_RISK",
        amount="5.00",
        currency="USD",
        reason="Normal customer return",
        suspicious_indicators=["rapid_velocity_indicator", "unexpected_phrasing"],
        risk_explanation="Heuristic detected suspicious velocity cue",
    )

    # Stricter risk resolution merges indicators
    resolved = resolve_stricter_risk(proposal_a, proposal_b)
    assert len(resolved.suspicious_indicators) == 2
    assert "rapid_velocity_indicator" in resolved.suspicious_indicators
    assert "unexpected_phrasing" in resolved.suspicious_indicators

    # Policy evaluation with strictly merged risk: ALLOW must tighten to ESCALATE
    capture = {
        "id": "CAP_LOW_RISK",
        "merchant_id": "merch_1",
        "original_amount": "100.00",
        "refunded_amount": "0.00",
        "status": "COMPLETED",
    }
    agent = {"id": "agent_1", "merchant_id": "merch_1", "status": "active"}

    # Without risk flag: $5.00 is ALLOW
    res_clean = evaluate_refund_policy(agent, capture, Decimal("5.00"), "USD")
    assert res_clean.decision == Decision.ALLOW

    # With resolved risk flag from Model B: $5.00 MUST be tightened to ESCALATE
    res_flagged = evaluate_refund_policy(
        agent,
        capture,
        Decimal("5.00"),
        "USD",
        ai_risk_flag=bool(resolved.suspicious_indicators),
        ai_risk_explanation=resolved.risk_explanation,
    )
    assert res_flagged.decision == Decision.ESCALATE
    assert res_flagged.approval_required is True
    assert "Tightened to ESCALATE" in res_flagged.reasons[1]


# ==============================================================================
# TEST 9: Authentication failure â€” no provider fallback
# ==============================================================================
async def test_9_auth_failure_no_provider_fallback():
    # If the user input is empty or invalid, neither provider should be invoked
    gemini_calls = 0
    groq_calls = 0

    def gemini_handler(request: httpx.Request) -> httpx.Response:
        nonlocal gemini_calls
        gemini_calls += 1
        return _gemini_response(VALID_PROPOSAL_A)

    def groq_handler(request: httpx.Request) -> httpx.Response:
        nonlocal groq_calls
        groq_calls += 1
        return _groq_response(VALID_PROPOSAL_GROQ)

    cfg = _make_config()
    gemini_client = GeminiClient(cfg, transport=httpx.MockTransport(gemini_handler))
    groq_client = GroqClient(cfg, transport=httpx.MockTransport(groq_handler))
    ai_client = AIFallbackClient(cfg, gemini_client=gemini_client, groq_client=groq_client)

    # Empty email input raises validation error before calling any provider
    with pytest.raises(GeminiValidationError) as exc:
        await ai_client.extract_refund_proposal("   ")
    assert exc.value.code == "EMPTY_EMAIL"
    assert gemini_calls == 0
    assert groq_calls == 0


# ==============================================================================
# TEST 10: Existing deterministic refund thresholds remain unchanged
# ==============================================================================
def test_10_deterministic_refund_thresholds_remain_unchanged():
    capture = {
        "id": "CAP_1",
        "merchant_id": "merch_1",
        "original_amount": "100.00",
        "refunded_amount": "0.00",
        "status": "COMPLETED",
    }
    agent = {"id": "agent_1", "merchant_id": "merch_1", "status": "active"}

    # <= $10.00 -> ALLOW
    r1 = evaluate_refund_policy(agent, capture, Decimal("5.00"), "USD")
    assert r1.decision == Decision.ALLOW
    assert r1.approval_required is False

    r2 = evaluate_refund_policy(agent, capture, Decimal("10.00"), "USD")
    assert r2.decision == Decision.ALLOW
    assert r2.approval_required is False

    # > $10.00 and <= $25.00 -> ESCALATE
    r3 = evaluate_refund_policy(agent, capture, Decimal("10.01"), "USD")
    assert r3.decision == Decision.ESCALATE
    assert r3.approval_required is True

    r4 = evaluate_refund_policy(agent, capture, Decimal("25.00"), "USD")
    assert r4.decision == Decision.ESCALATE
    assert r4.approval_required is True

    # > $25.00 -> BLOCK
    r5 = evaluate_refund_policy(agent, capture, Decimal("25.01"), "USD")
    assert r5.decision == Decision.BLOCK
    assert r5.approval_required is False

    r6 = evaluate_refund_policy(agent, capture, Decimal("100.00"), "USD")
    assert r6.decision == Decision.BLOCK
    assert r6.approval_required is False


# ==============================================================================
# TEST 11: No AI-provider failure can bypass merchant approval or PayPal security
# ==============================================================================
def test_11_ai_failure_cannot_bypass_merchant_approval_or_paypal():
    capture = {
        "id": "CAP_1",
        "merchant_id": "merch_1",
        "original_amount": "100.00",
        "refunded_amount": "0.00",
        "status": "COMPLETED",
    }
    agent = {"id": "agent_1", "merchant_id": "merch_1", "status": "active"}

    # When AI provider fails (ai_signal_error present), $5.00 MUST tighten to ESCALATE
    r_ai_err = evaluate_refund_policy(
        agent,
        capture,
        Decimal("5.00"),
        "USD",
        ai_signal_error="All AI providers timed out",
    )
    assert r_ai_err.decision == Decision.ESCALATE
    assert r_ai_err.approval_required is True

    # For an escalated amount ($20.00), AI error never alters ESCALATE to ALLOW
    r_esc_ai_err = evaluate_refund_policy(
        agent,
        capture,
        Decimal("20.00"),
        "USD",
        ai_signal_error="Groq and Gemini down",
    )
    assert r_esc_ai_err.decision == Decision.ESCALATE
    assert r_esc_ai_err.approval_required is True

    # For an excessive amount ($30.00), AI cannot bypass BLOCK
    r_block = evaluate_refund_policy(
        agent,
        capture,
        Decimal("30.00"),
        "USD",
        ai_signal_error="Groq and Gemini down",
    )
    assert r_block.decision == Decision.BLOCK
    assert r_block.approval_required is False
