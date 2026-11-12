import os
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Dict, Optional, Set

SENSITIVE_KEYS = {
    "password",
    "password_hash",
    "secret",
    "api_key",
    "api_key_hash",
    "token",
    "session_token",
    "authorization",
    "x-agent-key",
    "x-session-token",
}

KNOWN_INSECURE_PASSWORDS: Set[str] = {
    "vaultline_judge_password_123!",
    "replace_with_strong_password_in_production",
    "password",
    "admin",
    "12345678",
}

KNOWN_INSECURE_AGENT_KEYS: Set[str] = {
    "vl_agent_secret_key_abc123",
    "replace_with_agent_secret_key_in_production",
    "secret",
    "default",
}

KNOWN_INSECURE_USERNAMES: Set[str] = {
    "replace_me",
    "admin",
    "root",
}

DEFAULT_DEMO_JUDGE_USERNAME = "judge_merchant"
DEFAULT_DEMO_JUDGE_PASSWORD = "vaultline_judge_password_123!"
DEFAULT_DEMO_AGENT_KEY = "vl_agent_secret_key_abc123"

def redact_sensitive_data(data: Any) -> Any:
    """Recursively redact secrets in dictionaries or lists."""
    if isinstance(data, dict):
        redacted = {}
        for k, v in data.items():
            if isinstance(k, str) and k.lower() in SENSITIVE_KEYS:
                redacted[k] = "[REDACTED]"
            else:
                redacted[k] = redact_sensitive_data(v)
        return redacted
    elif isinstance(data, list):
        return [redact_sensitive_data(item) for item in data]
    return data

@dataclass
class Config:
    db_path: str = field(default_factory=lambda: os.getenv("VAULTLINE_DB_PATH", "vaultline.db"))
    merchant_id: str = field(default_factory=lambda: os.getenv("VAULTLINE_MERCHANT_ID", "merch_default"))
    judge_username: str = field(default_factory=lambda: os.getenv("VAULTLINE_JUDGE_USERNAME", ""))
    judge_password: str = field(default_factory=lambda: os.getenv("VAULTLINE_JUDGE_PASSWORD", ""))
    agent_id: str = field(default_factory=lambda: os.getenv("VAULTLINE_AGENT_ID", "agent_default"))
    agent_key: str = field(default_factory=lambda: os.getenv("VAULTLINE_AGENT_KEY", ""))
    max_auto_amount: Decimal = field(default_factory=lambda: Decimal(os.getenv("VAULTLINE_MAX_AUTO_USD", "10.00")))
    max_escalate_amount: Decimal = field(default_factory=lambda: Decimal(os.getenv("VAULTLINE_MAX_ESCALATE_USD", "25.00")))
    currency: str = field(default_factory=lambda: os.getenv("VAULTLINE_CURRENCY", "USD"))
    approval_expiry_seconds: int = field(default_factory=lambda: int(os.getenv("VAULTLINE_APPROVAL_EXPIRY_SECONDS", "900")))
    demo_mode: bool = False

    def __post_init__(self):
        # In explicit demo mode only, populate safe demo defaults if secrets are omitted
        if self.demo_mode or os.getenv("VAULTLINE_DEMO_MODE", "").strip().lower() in ("true", "1", "yes"):
            if not self.judge_username or not self.judge_username.strip():
                self.judge_username = DEFAULT_DEMO_JUDGE_USERNAME
            if not self.judge_password or not self.judge_password.strip():
                self.judge_password = DEFAULT_DEMO_JUDGE_PASSWORD
            if not self.agent_key or not self.agent_key.strip():
                self.agent_key = DEFAULT_DEMO_AGENT_KEY

    def validate_credentials(
        self,
        is_production: bool = False,
        is_real_paypal: bool = False,
        is_demo_mode: bool = False,
    ) -> None:
        """Validate credentials according to security policy and deployment mode."""
        username = (self.judge_username or "").strip()
        password = (self.judge_password or "").strip()
        key = (self.agent_key or "").strip()
        agent_id = (self.agent_id or "").strip()

        if not agent_id:
            raise RuntimeError(
                "Security initialization failed: VAULTLINE_AGENT_ID must be "
                "explicitly configured and non-empty. Selecting an arbitrary agent is strictly prohibited."
            )

        # Contradiction check: demo mode cannot be enabled with real PayPal or in production
        if is_demo_mode and (is_production or is_real_paypal):
            raise RuntimeError(
                "Contradictory configuration: demo mode (VAULTLINE_DEMO_MODE=true) "
                "cannot be enabled in production or with real PayPal adapter."
            )

        # Outside explicit demo mode: judge username, judge password, and agent key MUST be explicitly configured
        if not is_demo_mode:
            if not username:
                raise RuntimeError(
                    "Security initialization failed: VAULTLINE_JUDGE_USERNAME must be "
                    "explicitly configured outside demo mode."
                )
            if not password:
                raise RuntimeError(
                    "Security initialization failed: VAULTLINE_JUDGE_PASSWORD must be "
                    "explicitly configured outside demo mode."
                )
            if not key:
                raise RuntimeError(
                    "Security initialization failed: VAULTLINE_AGENT_KEY must be "
                    "explicitly configured outside demo mode."
                )

        # In production or real PayPal mode (or outside demo mode), reject known insecure defaults
        if is_production or is_real_paypal or not is_demo_mode:
            if username.lower() in KNOWN_INSECURE_USERNAMES:
                raise RuntimeError(
                    f"Security initialization failed: VAULTLINE_JUDGE_USERNAME cannot use "
                    f"insecure placeholder or default administrative name '{username}'."
                )
            if password in KNOWN_INSECURE_PASSWORDS:
                raise RuntimeError(
                    "Security initialization failed: VAULTLINE_JUDGE_PASSWORD matches a known "
                    "insecure default. A strong, unique secret is required."
                )
            if key in KNOWN_INSECURE_AGENT_KEYS:
                raise RuntimeError(
                    "Security initialization failed: VAULTLINE_AGENT_KEY matches a known "
                    "insecure default. A strong, unique secret is required."
                )
