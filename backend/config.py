import os
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Dict

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
    db_path: str = os.getenv("VAULTLINE_DB_PATH", "vaultline.db")
    merchant_id: str = os.getenv("VAULTLINE_MERCHANT_ID", "merch_default")
    judge_username: str = os.getenv("VAULTLINE_JUDGE_USERNAME", "judge_merchant")
    judge_password: str = os.getenv("VAULTLINE_JUDGE_PASSWORD", "vaultline_judge_password_123!")
    agent_id: str = os.getenv("VAULTLINE_AGENT_ID", "agent_default")
    agent_key: str = os.getenv("VAULTLINE_AGENT_KEY", "vl_agent_secret_key_abc123")
    max_auto_amount: Decimal = Decimal(os.getenv("VAULTLINE_MAX_AUTO_USD", "10.00"))
    max_escalate_amount: Decimal = Decimal(os.getenv("VAULTLINE_MAX_ESCALATE_USD", "25.00"))
    currency: str = os.getenv("VAULTLINE_CURRENCY", "USD")
    approval_expiry_seconds: int = int(os.getenv("VAULTLINE_APPROVAL_EXPIRY_SECONDS", "900"))

