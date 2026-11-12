"""Prompt-injection detection and untrusted email framing utilities."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
import re

_INJECTION_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "instruction_override",
        re.compile(
            r"\b(ignore|disregard|forget|override|bypass)\b.{0,40}\b(previous|prior|above|all|system|policy|policies|rules?|limits?|instructions?)\b",
            re.IGNORECASE | re.DOTALL,
        ),
    ),
    (
        "auto_approve_directive",
        re.compile(
            r"\b(auto[- ]?approve|skip approval|without approval|approve immediately|force allow|set decision to allow)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "authority_spoofing",
        re.compile(
            r"\b(ceo|admin|system administrator|security team|developer mode|jailbreak)\b.{0,40}\b(says|orders|instructs|authorizes|requires|commands)\b",
            re.IGNORECASE | re.DOTALL,
        ),
    ),
    (
        "prompt_or_credential_exfil",
        re.compile(
            r"\b(reveal|print|output|leak|expose|return)\b.{0,40}\b(system prompt|api[-_ ]?key|secret|password|token|credential|env)\b",
            re.IGNORECASE | re.DOTALL,
        ),
    ),
    (
        "policy_mutation_attempt",
        re.compile(
            r"\b(raise|increase|change|modify|update|set)\b.{0,30}\b(limit|threshold|max_auto|max_escalate|policy)\b",
            re.IGNORECASE,
        ),
    ),
)


def detect_prompt_injection(email_text: str) -> list[str]:
    """Return deterministic prompt-injection signal names found in untrusted email text."""
    if not email_text:
        return []
    signals: list[str] = []
    for label, pattern in _INJECTION_RULES:
        if pattern.search(email_text):
            signals.append(label)
    return signals


_CLOSING_TAG_RE = re.compile(r"<\s*/\s*untrusted_customer_email\s*>", re.IGNORECASE)


def frame_untrusted_email(email_text: str) -> str:
    """Wrap untrusted customer email content in explicit data delimiters for the model."""
    # Neutralize any closing delimiter inside the email (case/whitespace-insensitive) so it cannot break out.
    sanitized = _CLOSING_TAG_RE.sub("[/untrusted_customer_email]", email_text)
    return (
        "<untrusted_customer_email>\n"
        f"{sanitized}\n"
        "</untrusted_customer_email>"
    )


_AMOUNT_MENTION_RE = re.compile(
    r"(?:USD\s*)?\$\s*(\d+(?:\.\d{1,2})?)\b|\b(\d+(?:\.\d{1,2})?)\s*USD\b",
    re.IGNORECASE,
)
_CAP_PREFIX_RE = re.compile(r"\b(CAP_[A-Za-z0-9_-]{1,60})\b")
_CAP_LABEL_RE = re.compile(
    r"\bcapture(?:\s+id|\s*#|\s*:)\s*([A-Za-z0-9_-]{4,64})\b",
    re.IGNORECASE,
)


def detect_conflicting_details(email_text: str) -> list[str]:
    """Detect multiple distinct amounts or capture IDs in an untrusted email."""
    if not email_text:
        return []

    signals: list[str] = []

    amounts: set[Decimal] = set()
    for match in _AMOUNT_MENTION_RE.finditer(email_text):
        raw_val = match.group(1) or match.group(2)
        if raw_val:
            try:
                amounts.add(Decimal(raw_val).quantize(Decimal("0.01")))
            except InvalidOperation:
                continue
    if len(amounts) > 1:
        signals.append("conflicting_amounts")

    captures: set[str] = set(_CAP_PREFIX_RE.findall(email_text))
    for match in _CAP_LABEL_RE.finditer(email_text):
        candidate = match.group(1)
        # Avoid false positives on ordinary English words by requiring digits/underscores or >=8 uppercase chars.
        if any(ch.isdigit() or ch == "_" for ch in candidate) or (candidate.isupper() and len(candidate) >= 8):
            captures.add(candidate)
    if len(captures) > 1:
        signals.append("conflicting_capture_ids")

    return signals

