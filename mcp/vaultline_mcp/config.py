"""Server-side configuration for the Vaultline MCP server.

The agent credential and backend URL come ONLY from the MCP server's own process
environment, which is set by whoever deploys the server (trusted context). They are
never tool arguments, never sent to the model, and never logged.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from urllib.parse import urlparse

LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}


class ConfigError(RuntimeError):
    """Raised when the server is misconfigured. Messages never include secrets."""


@dataclass(frozen=True)
class Settings:
    api_base_url: str
    agent_key: str = field(repr=False)  # repr=False: never printed in tracebacks/logs
    timeout_seconds: float = 10.0

    @classmethod
    def from_env(cls) -> "Settings":
        base = os.getenv("VAULTLINE_API_BASE_URL", "http://localhost:8000").strip().rstrip("/")
        key = os.getenv("VAULTLINE_AGENT_KEY", "").strip()
        timeout = float(os.getenv("VAULTLINE_TIMEOUT_SECONDS", "10"))
        if not key:
            raise ConfigError("VAULTLINE_AGENT_KEY is not set. Configure it in the MCP server environment.")
        validate_base_url(base)
        return cls(api_base_url=base, agent_key=key, timeout_seconds=timeout)


def validate_base_url(base: str) -> None:
    """Only the configured Vaultline backend is reachable. Plain HTTP is allowed for loopback only."""
    parsed = urlparse(base)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ConfigError("VAULTLINE_API_BASE_URL must be an http(s) URL with a host.")
    if parsed.username or parsed.password:
        raise ConfigError("VAULTLINE_API_BASE_URL must not contain credentials.")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ConfigError("VAULTLINE_API_BASE_URL must be an origin only, e.g. https://api.example.com")
    if parsed.scheme == "http" and parsed.hostname not in LOOPBACK_HOSTS:
        raise ConfigError("Remote Vaultline backends must use https (TLS). Plain http is only allowed for localhost.")
