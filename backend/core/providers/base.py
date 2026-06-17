"""Uniform LLM provider interface + shared types and errors.

Every concrete provider (Cerebras/Groq/OpenRouter/Gemini) implements `LLMProvider`
so the router can treat them interchangeably. No provider SDK is used directly —
all I/O is raw async httpx (CLAUDE.md §6, §8).
"""
from __future__ import annotations

import math
from abc import ABC, abstractmethod
from typing import ClassVar, Literal, Sequence

from pydantic import BaseModel, Field

# What a task needs from a provider. Maps to the §4 "Best for" column and drives
# the router policy. Kept as a closed set so routing is exhaustive.
TaskType = Literal["short", "reasoning", "long_synthesis", "overflow"]


class Message(BaseModel):
    """One chat message. The only shape that crosses the provider boundary."""

    role: Literal["system", "user", "assistant"]
    content: str


class TokenUsage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


class LLMResult(BaseModel):
    """Normalized completion result returned by every provider."""

    text: str
    usage: TokenUsage = Field(default_factory=TokenUsage)
    provider: str
    model: str


class ProviderCapabilities(BaseModel, frozen=True):
    """Static description of a provider's free-tier limits and strengths (§4)."""

    name: str
    max_context: int
    tags: tuple[TaskType, ...]
    rpm: int | None = None  # requests / minute
    rpd: int | None = None  # requests / day
    tpm: int | None = None  # tokens / minute
    tpd: int | None = None  # tokens / day (e.g. Cerebras 1M/day)


# --- error taxonomy ---------------------------------------------------------
# ProviderError and its subclasses are *recoverable*: the router catches them and
# fails over to the next provider in the chain. AllProvidersExhausted is terminal.


class LLMError(Exception):
    """Base class for all router/provider errors."""


class ProviderError(LLMError):
    """Recoverable provider failure — triggers failover to the next provider."""


class RateLimitError(ProviderError):
    """HTTP 429 — provider is rate limited right now."""


class ServerError(ProviderError):
    """HTTP 5xx or a transport-level failure."""


class MalformedResponseError(ProviderError):
    """Provider returned a body we could not parse / validate."""


class ContextOverflowError(LLMError):
    """Prompt exceeds a provider's max_context (pre-flight guard, see router.pick)."""


class AllProvidersExhausted(LLMError):
    """Every provider in the chain failed after capped retries. Callers degrade."""


# --- token estimation -------------------------------------------------------
# Pre-flight token counting so we never trust the API to tell us we overflowed
# (CLAUDE.md §9: "never trust the call"). tiktoken is not a dependency; a 4-chars
# ≈ 1-token heuristic is deterministic and good enough for routing decisions.

_CHARS_PER_TOKEN = 4
_PER_MESSAGE_OVERHEAD = 4  # role + delimiters, roughly


def estimate_tokens(text: str) -> int:
    return max(1, math.ceil(len(text) / _CHARS_PER_TOKEN))


def estimate_message_tokens(messages: Sequence[Message]) -> int:
    return sum(estimate_tokens(m.content) + _PER_MESSAGE_OVERHEAD for m in messages)


class LLMProvider(ABC):
    """Uniform async interface every provider implements."""

    capabilities: ClassVar[ProviderCapabilities]

    @abstractmethod
    async def complete(
        self,
        messages: Sequence[Message],
        *,
        max_tokens: int,
        json_mode: bool = False,
    ) -> LLMResult:
        """Run a completion. Raise a ProviderError subclass on a recoverable failure."""
        raise NotImplementedError
