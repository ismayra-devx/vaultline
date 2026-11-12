"""Strict Pydantic models for AI-extracted refund proposals and agent outcomes."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

CAPTURE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
AMOUNT_RE = re.compile(r"^(0|[1-9][0-9]{0,8})(\.[0-9]{1,2})?$")


class RefundProposal(BaseModel):
    """Structured refund proposal extracted by Gemini from an untrusted customer email.

    Strict validation ensures malformed, float, negative/zero, unsupported currency,
    or extra model-injected fields are rejected before any MCP tool call is made.
    """

    model_config = ConfigDict(extra="forbid")

    capture_id: str = Field(description="PayPal capture identifier matching ^[A-Za-z0-9_-]{1,64}$")
    amount: str = Field(description="Positive decimal string with up to 2 decimal places, e.g. '5.00'")
    currency: Literal["USD"] = Field(description="Supported currency code (USD only)")
    reason: str = Field(min_length=1, max_length=500, description="Concise business reason for the refund")
    suspicious_indicators: list[str] = Field(
        default_factory=list,
        max_length=20,
        description="Observed prompt-injection or fraud indicators in the email",
    )
    risk_explanation: str = Field(
        min_length=1,
        max_length=1000,
        description="Explanation of why the proposal is normal or suspicious",
    )

    @field_validator("capture_id", mode="before")
    @classmethod
    def _validate_capture_id(cls, value: Any) -> str:
        if not isinstance(value, str):
            raise ValueError("capture_id must be a string")
        cleaned = value.strip()
        if not CAPTURE_ID_RE.fullmatch(cleaned):
            raise ValueError("capture_id must match ^[A-Za-z0-9_-]{1,64}$")
        return cleaned

    @field_validator("amount", mode="before")
    @classmethod
    def _validate_amount(cls, value: Any) -> str:
        if not isinstance(value, str):
            raise ValueError("amount must be a decimal string, never a float or int")
        cleaned = value.strip()
        if not AMOUNT_RE.fullmatch(cleaned):
            raise ValueError("amount must be a valid decimal string with up to 2 decimal places")
        try:
            dec = Decimal(cleaned)
        except InvalidOperation as exc:
            raise ValueError("amount is not a valid decimal") from exc
        if dec <= Decimal("0.00"):
            raise ValueError("amount must be greater than 0.00")
        return cleaned

    @field_validator("reason", mode="before")
    @classmethod
    def _validate_reason(cls, value: Any) -> str:
        if not isinstance(value, str):
            raise ValueError("reason must be a string")
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("reason must not be empty")
        if len(cleaned) > 500:
            raise ValueError("reason must be at most 500 characters")
        return cleaned

    @field_validator("risk_explanation", mode="before")
    @classmethod
    def _validate_risk_explanation(cls, value: Any) -> str:
        if not isinstance(value, str):
            raise ValueError("risk_explanation must be a string")
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("risk_explanation must not be empty")
        if len(cleaned) > 1000:
            raise ValueError("risk_explanation must be at most 1000 characters")
        return cleaned

    @field_validator("suspicious_indicators", mode="before")
    @classmethod
    def _validate_indicators(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError("suspicious_indicators must be a list of strings")
        out: list[str] = []
        for item in value:
            if not isinstance(item, str):
                raise ValueError("each suspicious indicator must be a string")
            cleaned = item.strip()
            if cleaned:
                out.append(cleaned[:200])
        return out


class AgentRunResult(BaseModel):
    """Final structured result returned by RefundAgent.process_email()."""

    model_config = ConfigDict(extra="forbid")

    source: Literal["gemini", "deterministic_fallback", "deterministic_security_check"]
    ai_extracted: bool
    status: Literal[
        "COMPLETED",
        "ESCALATED",
        "BLOCKED",
        "REVIEW_REQUIRED",
        "DRY_RUN",
        "UNCERTAIN",
        "EXTRACTION_FAILED",
        "ERROR",
    ]
    proposal: RefundProposal | None = None
    prompt_injection_detected: bool = False
    injection_signals: list[str] = Field(default_factory=list)
    policy: dict[str, Any] | None = None
    mcp_result: dict[str, Any] | None = None
    client_request_id: str | None = None
    refund_id: str | None = None
    message: str
    error_code: str | None = None

