"""Server-side configuration for the Vaultline MCP server.

Two transports:
- stdio (local): the agent key comes from this process's environment, set by whoever launches it.
- http (hosted): NO agent key is configured on the server. Each agent sends its own key as
  `Authorization: Bearer <key>` to the MCP endpoint and it is forwarded to the backend, which
  validates it. A shared key would let every caller act as one agent, so it is refused.

The backend URL is fixed by configuration, never by tool input. In hosted mode
(VAULTLINE_DEPLOYMENT=hosted) it must be https and must not be localhost.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from urllib.parse import urlparse

LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}


class ConfigError(RuntimeError):
    """Raised when the server is misconfigured. Messages never include secrets."""


@dataclass(frozen=True)
class Settings:
    api_base_url: str
    agent_key: str = field(default="", repr=False)  # repr=False: never printed in tracebacks/logs
    timeout_seconds: float = 10.0
    transport: str = "stdio"
    hosted: bool = False
    host: str = "0.0.0.0"
    port: int = 8080
    allowed_hosts: tuple[str, ...] = ()

    @classmethod
    def from_env(cls) -> "Settings":
        hosted = os.getenv("VAULTLINE_DEPLOYMENT", "local").strip().lower() == "hosted"
        transport = os.getenv("VAULTLINE_MCP_TRANSPORT", "http" if hosted else "stdio").strip().lower()
        base = os.getenv("VAULTLINE_API_BASE_URL", "" if hosted else "http://localhost:8000").strip().rstrip("/")
        key = os.getenv("VAULTLINE_AGENT_KEY", "").strip()
        timeout = float(os.getenv("VAULTLINE_TIMEOUT_SECONDS", "10"))
        if transport not in {"stdio", "http"}:
            raise ConfigError("VAULTLINE_MCP_TRANSPORT must be 'stdio' or 'http'.")
        if transport == "stdio" and not key:
            raise ConfigError("VAULTLINE_AGENT_KEY is not set. Configure it in the MCP server environment.")
        if transport == "http" and key:
            raise ConfigError("Do not set VAULTLINE_AGENT_KEY in http mode: each agent must send its own key.")
        if hosted and transport != "http":
            raise ConfigError("Hosted deployments must use the http transport.")
        validate_base_url(base, hosted=hosted)
        hosts = [h.strip() for h in os.getenv("VAULTLINE_MCP_ALLOWED_HOSTS", "").split(",") if h.strip()]
        render_host = os.getenv("RENDER_EXTERNAL_HOSTNAME", "").strip()
        if render_host:
            hosts.append(render_host)
        if hosted and not hosts:
            raise ConfigError("Set VAULTLINE_MCP_ALLOWED_HOSTS to this server's public hostname.")
        return cls(
            api_base_url=base,
            agent_key=key,
            timeout_seconds=timeout,
            transport=transport,
            hosted=hosted,
            host=os.getenv("HOST", "0.0.0.0" if transport == "http" else "127.0.0.1"),
            port=int(os.getenv("PORT", "8080")),
            allowed_hosts=tuple(dict.fromkeys(hosts)),
        )


def validate_base_url(base: str, *, hosted: bool = False) -> None:
    """Only the configured Vaultline backend is reachable.

    Local: https anywhere, plain http for localhost only. Hosted: https only, never localhost.
    """
    parsed = urlparse(base)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ConfigError("VAULTLINE_API_BASE_URL must be an http(s) URL with a host.")
    if parsed.username or parsed.password:
        raise ConfigError("VAULTLINE_API_BASE_URL must not contain credentials.")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ConfigError("VAULTLINE_API_BASE_URL must be an origin only, e.g. https://api.example.com")
    loopback = parsed.hostname in LOOPBACK_HOSTS or parsed.hostname.startswith("127.")
    if hosted:
        if parsed.scheme != "https" or loopback:
            raise ConfigError("Hosted deployments must point at the public https backend URL, never localhost.")
    elif parsed.scheme == "http" and not loopback:
        raise ConfigError("Remote Vaultline backends must use https (TLS). Plain http is only allowed for localhost.")
