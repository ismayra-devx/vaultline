"""Configuration for the Vaultline AI agent.

Loads environment variables from the repository root `.env` (if present) without
overriding variables already set in the process environment.

Security:
- `GEMINI_API_KEY` and `VAULTLINE_AGENT_KEY` are stored with `repr=False` and
  must never be logged, exposed to the model, or passed as MCP tool arguments.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

DEFAULT_GEMINI_MODEL = "gemini-3.8-flash"
DEFAULT_GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
DEFAULT_GEMINI_TIMEOUT_SECONDS = 15.0
DEFAULT_GEMINI_MAX_RETRIES = 2
MAX_ALLOWED_RETRIES = 3


class AgentConfigError(RuntimeError):
    """Raised when agent configuration is invalid. Messages never include secrets."""


def _load_root_env() -> None:
    """Load root `.env` if present without overriding existing environment variables."""
    repo_root = Path(__file__).resolve().parents[2]
    env_path = repo_root / ".env"
    if env_path.is_file():
        load_dotenv(dotenv_path=env_path, override=False)


def _parse_positive_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        val = float(raw.strip())
    except ValueError as exc:
        raise AgentConfigError(f"{name} must be a positive number.") from exc
    if val <= 0:
        raise AgentConfigError(f"{name} must be greater than 0.")
    return val


def _parse_bounded_retries(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        val = int(raw.strip())
    except ValueError as exc:
        raise AgentConfigError(f"{name} must be an integer.") from exc
    if val < 0 or val > MAX_ALLOWED_RETRIES:
        raise AgentConfigError(f"{name} must be between 0 and {MAX_ALLOWED_RETRIES}.")
    return val


@dataclass(frozen=True)
class AgentConfig:
    gemini_api_key: str = field(repr=False)
    gemini_model: str = DEFAULT_GEMINI_MODEL
    gemini_base_url: str = DEFAULT_GEMINI_BASE_URL
    gemini_timeout_seconds: float = DEFAULT_GEMINI_TIMEOUT_SECONDS
    gemini_max_retries: int = DEFAULT_GEMINI_MAX_RETRIES
    vaultline_api_base_url: str = "http://localhost:8000"
    vaultline_agent_key: str = field(default="", repr=False)
    vaultline_timeout_seconds: float = 10.0

    @classmethod
    def from_env(cls, *, load_dotenv_file: bool = True) -> "AgentConfig":
        if load_dotenv_file:
            _load_root_env()

        gemini_api_key = os.getenv("GEMINI_API_KEY", "").strip()
        gemini_model = os.getenv("GEMINI_MODEL", DEFAULT_GEMINI_MODEL).strip() or DEFAULT_GEMINI_MODEL
        gemini_base_url = (
            os.getenv("GEMINI_BASE_URL", DEFAULT_GEMINI_BASE_URL).strip().rstrip("/")
            or DEFAULT_GEMINI_BASE_URL
        )
        gemini_timeout = _parse_positive_float("GEMINI_TIMEOUT_SECONDS", DEFAULT_GEMINI_TIMEOUT_SECONDS)
        gemini_retries = _parse_bounded_retries("GEMINI_MAX_RETRIES", DEFAULT_GEMINI_MAX_RETRIES)

        api_base_url = os.getenv("VAULTLINE_API_BASE_URL", "http://localhost:8000").strip().rstrip("/")
        agent_key = os.getenv("VAULTLINE_AGENT_KEY", "").strip()
        vaultline_timeout = _parse_positive_float("VAULTLINE_TIMEOUT_SECONDS", 10.0)

        return cls(
            gemini_api_key=gemini_api_key,
            gemini_model=gemini_model,
            gemini_base_url=gemini_base_url,
            gemini_timeout_seconds=gemini_timeout,
            gemini_max_retries=gemini_retries,
            vaultline_api_base_url=api_base_url,
            vaultline_agent_key=agent_key,
            vaultline_timeout_seconds=vaultline_timeout,
        )

