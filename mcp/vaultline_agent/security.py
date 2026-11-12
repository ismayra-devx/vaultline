"""Prompt-injection detection and untrusted email framing utilities."""

from __future__ import annotations

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

