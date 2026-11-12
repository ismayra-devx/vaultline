"""Vaultline AI Refund Agent package."""

from .agent import RefundAgent
from .config import AgentConfig, AgentConfigError, DEFAULT_GEMINI_MODEL
from .gemini_client import (
    GeminiClient,
    GeminiError,
    GeminiUnavailableError,
    GeminiValidationError,
)
from .mcp_client import EXPECTED_MCP_TOOLS, MCPAgentClient, MCPAgentSession, MCPClientError
from .models import AgentRunResult, RefundProposal
from .security import detect_prompt_injection, frame_untrusted_email

__all__ = [
    "AgentConfig",
    "AgentConfigError",
    "AgentRunResult",
    "DEFAULT_GEMINI_MODEL",
    "EXPECTED_MCP_TOOLS",
    "GeminiClient",
    "GeminiError",
    "GeminiUnavailableError",
    "GeminiValidationError",
    "MCPAgentClient",
    "MCPAgentSession",
    "MCPClientError",
    "RefundAgent",
    "RefundProposal",
    "detect_prompt_injection",
    "frame_untrusted_email",
]

