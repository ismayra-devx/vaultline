"""Vaultline AI Refund Agent package."""

from .agent import RefundAgent
from .ai_fallback import AIFallbackClient, AIProviderError, AllAIProvidersUnavailableError
from .config import AgentConfig, AgentConfigError, DEFAULT_GEMINI_MODEL, DEFAULT_GROQ_MODEL
from .gemini_client import (
    GeminiClient,
    GeminiError,
    GeminiUnavailableError,
    GeminiValidationError,
)
from .groq_client import (
    GroqClient,
    GroqError,
    GroqUnavailableError,
    GroqValidationError,
)
from .mcp_client import EXPECTED_MCP_TOOLS, MCPAgentClient, MCPAgentSession, MCPClientError
from .models import AgentRunResult, RefundProposal, resolve_stricter_risk
from .security import detect_prompt_injection, frame_untrusted_email

__all__ = [
    "AIFallbackClient",
    "AIProviderError",
    "AgentConfig",
    "AgentConfigError",
    "AgentRunResult",
    "AllAIProvidersUnavailableError",
    "DEFAULT_GEMINI_MODEL",
    "DEFAULT_GROQ_MODEL",
    "EXPECTED_MCP_TOOLS",
    "GeminiClient",
    "GeminiError",
    "GeminiUnavailableError",
    "GeminiValidationError",
    "GroqClient",
    "GroqError",
    "GroqUnavailableError",
    "GroqValidationError",
    "MCPAgentClient",
    "MCPAgentSession",
    "MCPClientError",
    "RefundAgent",
    "RefundProposal",
    "detect_prompt_injection",
    "frame_untrusted_email",
    "resolve_stricter_risk",
]

