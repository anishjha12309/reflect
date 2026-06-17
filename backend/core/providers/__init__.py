"""Concrete LLM providers behind the uniform `LLMProvider` interface (CLAUDE.md §4)."""
from .base import (
    AllProvidersExhausted,
    ContextOverflowError,
    LLMError,
    LLMProvider,
    LLMResult,
    MalformedResponseError,
    Message,
    ProviderCapabilities,
    ProviderError,
    RateLimitError,
    ServerError,
    TaskType,
    TokenUsage,
    estimate_message_tokens,
    estimate_tokens,
)
from .cerebras import CerebrasProvider
from .gemini import GeminiProvider
from .groq import GroqProvider
from .openrouter import OpenRouterProvider


def all_capabilities() -> list[ProviderCapabilities]:
    """Static capabilities of every known provider (no API key / instance needed).

    Used by the /metrics dashboard to show limits alongside ledger usage.
    """
    return [
        CerebrasProvider.capabilities,
        GroqProvider.capabilities,
        GeminiProvider.capabilities,
        OpenRouterProvider.capabilities,
    ]

__all__ = [
    "AllProvidersExhausted",
    "CerebrasProvider",
    "ContextOverflowError",
    "GeminiProvider",
    "GroqProvider",
    "LLMError",
    "LLMProvider",
    "LLMResult",
    "MalformedResponseError",
    "Message",
    "OpenRouterProvider",
    "ProviderCapabilities",
    "ProviderError",
    "RateLimitError",
    "ServerError",
    "TaskType",
    "TokenUsage",
    "all_capabilities",
    "estimate_message_tokens",
    "estimate_tokens",
]
