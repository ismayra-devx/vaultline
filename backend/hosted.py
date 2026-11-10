"""Hosted-deployment guard rails.

When VAULTLINE_DEPLOYMENT=hosted the backend refuses to start unless it is configured for a
real, internet-facing deployment: real PayPal Sandbox adapter, non-default secrets, and an
explicit allowed origin for the dashboard. Local development (the default) is unaffected.
"""

from __future__ import annotations

import os

from backend.config import Config

PUBLIC_DEFAULTS = {
    "agent_key": "vl_agent_secret_key_abc123",
    "judge_password": "vaultline_judge_password_123!",
}
MIN_SECRET_LEN = 24


class HostedConfigError(RuntimeError):
    pass


def is_hosted() -> bool:
    return os.getenv("VAULTLINE_DEPLOYMENT", "local").strip().lower() == "hosted"


def enforce_hosted_config(cfg: Config) -> None:
    """Raise HostedConfigError (never printing secret values) if a hosted deploy is unsafe."""
    problems: list[str] = []
    if os.getenv("VAULTLINE_PAYPAL_MODE", "fake").strip().lower() != "sandbox":
        problems.append("VAULTLINE_PAYPAL_MODE must be 'sandbox' (the fake adapter is for local dev only)")
    if not os.getenv("PAYPAL_CLIENT_ID") or not os.getenv("PAYPAL_CLIENT_SECRET"):
        problems.append("PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET must be set")
    for field, default in PUBLIC_DEFAULTS.items():
        value = getattr(cfg, field)
        env = "VAULTLINE_AGENT_KEY" if field == "agent_key" else "VAULTLINE_JUDGE_PASSWORD"
        if value == default:
            problems.append(f"{env} is still the public demo default")
        elif len(value) < MIN_SECRET_LEN:
            problems.append(f"{env} must be at least {MIN_SECRET_LEN} characters")
    if problems:
        raise HostedConfigError("Refusing to start hosted Vaultline: " + "; ".join(problems) + ".")


def allowed_origins() -> list[str]:
    """Dashboard origins allowed for CORS (comma separated, exact https origins)."""
    raw = os.getenv("VAULTLINE_ALLOWED_ORIGINS", "")
    return [o.strip().rstrip("/") for o in raw.split(",") if o.strip().startswith("https://")]
