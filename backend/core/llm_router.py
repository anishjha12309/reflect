"""Rate-limit-aware multi-provider LLM gateway — the signature feature (CLAUDE.md §1).

Picks a provider by (task_type, needed context, live quota), fails over on 429/5xx,
guards each provider with a circuit breaker, backs off+retries the whole chain, and
records every call into the quota ledger. No agent ever calls a provider directly;
they all go through `LLMRouter.complete`.
"""
from __future__ import annotations

import asyncio
import random
import time
from enum import Enum
from typing import Awaitable, Callable, Sequence

import structlog
from pydantic import BaseModel, ValidationError

from .providers.base import (
    AllProvidersExhausted,
    LLMProvider,
    LLMResult,
    MalformedResponseError,
    Message,
    ProviderError,
    TaskType,
    estimate_message_tokens,
)
from .quota import QuotaLedger

log = structlog.get_logger(__name__)

# Ordered fallback policy per task type (CLAUDE.md §4). The first viable provider
# (passing context / breaker / quota filters) is tried first, then the rest in order.
#
# "short" (summarize): fast + reliable JSON. Cerebras leads (fastest throughput),
# then SambaNova/Mistral (both verified to emit clean JSON), Groq last (6K TPM is
# poor for bursty summarize fan-out).
#
# "reasoning" (planner, critic): strong reasoning + clean JSON. Groq Llama-3.3 leads,
# SambaNova/Mistral back it up, Gemini is last resort (lowest RPD, reserve for synthesis).
#
# "long_synthesis": needs long context. Gemini leads (up to 1M tokens), then Mistral
# (32K), then SambaNova (16K).
#
# "overflow": Mistral → SambaNova → Groq as a general escape valve.
DEFAULT_POLICY: dict[TaskType, tuple[str, ...]] = {
    "short": ("cerebras", "sambanova", "mistral", "groq"),
    "reasoning": ("groq", "sambanova", "mistral", "gemini"),
    "long_synthesis": ("gemini", "mistral", "sambanova"),
    "overflow": ("mistral", "sambanova", "groq"),
}

_JSON_ONLY_REMINDER = Message(
    role="system",
    content="Return ONLY valid JSON matching the requested schema. No prose, no markdown fences.",
)


class BreakerState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """Per-provider breaker: opens after N consecutive failures, half-opens to probe
    after a cooldown, closes on a successful probe (CLAUDE.md §9)."""

    def __init__(
        self,
        *,
        failure_threshold: int = 3,
        cooldown: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._threshold = failure_threshold
        self._cooldown = cooldown
        self._clock = clock
        self._failures = 0
        self._state = BreakerState.CLOSED
        self._opened_at = 0.0

    @property
    def state(self) -> BreakerState:
        # Lazily transition OPEN → HALF_OPEN once the cooldown has elapsed.
        if (
            self._state is BreakerState.OPEN
            and self._clock() - self._opened_at >= self._cooldown
        ):
            self._state = BreakerState.HALF_OPEN
        return self._state

    def allow(self) -> bool:
        return self.state is not BreakerState.OPEN

    def record_success(self) -> None:
        self._failures = 0
        self._state = BreakerState.CLOSED

    def record_failure(self) -> None:
        # A failed probe in HALF_OPEN re-opens immediately; otherwise count up.
        if self.state is BreakerState.HALF_OPEN:
            self._trip()
            return
        self._failures += 1
        if self._failures >= self._threshold:
            self._trip()

    def _trip(self) -> None:
        self._state = BreakerState.OPEN
        self._opened_at = self._clock()


class TokenBucket:
    """Per-provider rate limiter as a refilling token bucket.

    Can limit any configurable resource per minute — requests (RPM) or tokens (TPM).
    `try_acquire(amount)` is non-blocking: it returns immediately with True when the
    requested amount is available (so we never wait under normal load — the router just
    calls the provider) and False when the provider is momentarily at its limit (so the
    router fails over to another provider instead of eating a 429). Single-threaded asyncio
    means the check-and-decrement is atomic — no lock needed.
    """

    def __init__(self, rate_per_min: float, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._capacity = max(1.0, rate_per_min)
        self._tokens = self._capacity
        self._refill_per_sec = max(rate_per_min, 1.0) / 60.0
        self._clock = clock
        self._updated = clock()

    @property
    def capacity(self) -> float:
        """Maximum resource units this bucket can hold (= rate_per_min * safety factor)."""
        return self._capacity

    def _refill(self) -> None:
        now = self._clock()
        self._tokens = min(self._capacity, self._tokens + (now - self._updated) * self._refill_per_sec)
        self._updated = now

    def try_acquire(self, amount: float = 1.0) -> bool:
        """Try to consume `amount` units. Returns True and debits on success, False if insufficient."""
        self._refill()
        if self._tokens >= amount:
            self._tokens -= amount
            return True
        return False

    def time_until(self, amount: float = 1.0) -> float:
        """Seconds until `amount` units will be available (0.0 if available now)."""
        self._refill()
        if self._tokens >= amount:
            return 0.0
        return (amount - self._tokens) / self._refill_per_sec


class LLMRouter:
    def __init__(
        self,
        providers: dict[str, LLMProvider],
        ledger: QuotaLedger,
        *,
        policy: dict[TaskType, tuple[str, ...]] | None = None,
        breaker_threshold: int = 3,
        breaker_cooldown: float = 30.0,
        max_retries: int = 3,
        base_backoff: float = 0.5,
        rpm_safety: float = 0.9,
        max_throttle_wait: float = 5.0,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._providers = providers
        self._ledger = ledger
        self._policy = policy or DEFAULT_POLICY
        self._max_retries = max_retries
        self._base_backoff = base_backoff
        self._max_throttle_wait = max_throttle_wait
        self._sleep = sleep
        self._breakers = {
            name: CircuitBreaker(
                failure_threshold=breaker_threshold,
                cooldown=breaker_cooldown,
                clock=clock,
            )
            for name in providers
        }
        # Per-provider request-rate throttle, sized at a safety fraction of each
        # provider's free-tier RPM so we self-pace instead of discovering the limit
        # via 429s. Providers with no declared RPM are left unthrottled.
        # A parallel token-rate limiter (self._token_limiters) enforces per-minute token
        # budgets (TPM) so bursty summarize fan-outs never discover Groq's 6K TPM via 429s.
        self._limiters: dict[str, TokenBucket] = {
            name: TokenBucket(max(1.0, p.capabilities.rpm * rpm_safety), clock=clock)
            for name, p in providers.items()
            if p.capabilities.rpm
        }
        # Per-provider token-rate throttle (TPM). Only created for providers with a declared
        # tpm; providers without tpm are treated as token-unlimited (e.g. Cerebras, SambaNova).
        self._token_limiters: dict[str, TokenBucket] = {
            name: TokenBucket(max(1.0, p.capabilities.tpm * rpm_safety), clock=clock)
            for name, p in providers.items()
            if p.capabilities.tpm
        }

    def pick(self, task_type: TaskType, needed_context_tokens: int) -> list[str]:
        """Return the ordered, *currently viable* fallback chain for this task.

        Excludes providers whose max_context is too small (the Cerebras 8,192 guard),
        whose breaker is open, or whose daily quota is spent.
        """
        chain: list[str] = []
        for name in self._policy.get(task_type, ()):
            provider = self._providers.get(name)
            if provider is None:
                continue
            cap = provider.capabilities
            if needed_context_tokens > cap.max_context:
                continue  # pre-flight overflow exclusion (never trust the API)
            if not self._breakers[name].allow():
                continue
            if self._ledger.is_exhausted(cap):
                continue
            chain.append(name)
        return chain

    async def complete(
        self,
        messages: Sequence[Message],
        *,
        task_type: TaskType,
        max_tokens: int = 1024,
        json_mode: bool = False,
        schema: type[BaseModel] | None = None,
    ) -> LLMResult:
        # Pre-flight budget: prompt estimate + reserved completion space.
        needed = estimate_message_tokens(messages) + max_tokens
        prompt_est = estimate_message_tokens(messages)
        last_error: Exception | None = None

        attempt = 0
        while True:
            chain = self.pick(task_type, needed)  # re-pick: breakers may have recovered
            called_any = False
            soonest_token: float | None = None  # min wait among throttled providers

            for name in chain:
                provider = self._providers[name]
                breaker = self._breakers[name]
                if not breaker.allow():
                    continue
                rpm_limiter = self._limiters.get(name)
                tok_limiter = self._token_limiters.get(name)

                # Over-capacity check: if this single call needs more tokens than the
                # provider's entire TPM bucket can ever hold, skip it permanently rather
                # than pacing forever waiting for capacity that can never arrive.
                if tok_limiter is not None and needed > tok_limiter.capacity:
                    continue

                # Peek at both limiters (refill is idempotent, no state change yet).
                # Single-threaded asyncio: no await between peek and acquire, so the
                # refilled state is consistent — this is the atomic peek-then-acquire.
                rpm_wait = rpm_limiter.time_until(1.0) if rpm_limiter else 0.0
                tok_wait = tok_limiter.time_until(needed) if tok_limiter else 0.0

                if rpm_wait > 0 or tok_wait > 0:
                    # At its RPM or TPM right now → skip to a provider with capacity
                    # (fail over for speed, not a 429). Remember the soonest refill time.
                    wait = max(rpm_wait, tok_wait)
                    soonest_token = wait if soonest_token is None else min(soonest_token, wait)
                    continue

                # Both limiters have headroom — debit them now, then call the provider.
                if rpm_limiter:
                    rpm_limiter.try_acquire(1.0)
                if tok_limiter:
                    tok_limiter.try_acquire(needed)
                called_any = True
                try:
                    result = await self._call(provider, messages, max_tokens, json_mode, schema)
                except ProviderError as exc:
                    last_error = exc
                    breaker.record_failure()
                    self._ledger.record(name, task_type, prompt_est, 0, success=False)
                    log.warning("provider_failed", provider=name, error=str(exc))
                    continue
                breaker.record_success()
                self._ledger.record(
                    name,
                    task_type,
                    result.usage.prompt_tokens or prompt_est,
                    result.usage.completion_tokens,
                    success=True,
                )
                return result

            if called_any:
                # A real attempt happened (≥1 provider called and failed) → backoff/retry.
                attempt += 1
                if attempt >= self._max_retries:
                    break
                await self._backoff(attempt - 1)
            elif soonest_token is not None:
                # Every viable provider is throttled — pace briefly, then retry WITHOUT
                # consuming a retry (this isn't a failure, just rate-shaping).
                await self._sleep(min(soonest_token, self._max_throttle_wait))
            else:
                # Nothing viable (all breaker-open / quota-exhausted / context-excluded).
                break

        raise AllProvidersExhausted(
            f"all providers exhausted for task_type={task_type} after "
            f"{self._max_retries} attempts"
        ) from last_error

    async def _call(
        self,
        provider: LLMProvider,
        messages: Sequence[Message],
        max_tokens: int,
        json_mode: bool,
        schema: type[BaseModel] | None,
    ) -> LLMResult:
        result = await provider.complete(messages, max_tokens=max_tokens, json_mode=json_mode)
        if not (json_mode and schema is not None):
            return result
        if _valid(result.text, schema):
            return result
        # One corrective retry with an explicit "JSON only" reminder, same provider.
        retry_messages = [*messages, _JSON_ONLY_REMINDER]
        result = await provider.complete(
            retry_messages, max_tokens=max_tokens, json_mode=json_mode
        )
        if _valid(result.text, schema):
            return result
        # Persistent bad JSON → treat as a provider failure so we fail over cleanly.
        raise MalformedResponseError(
            f"{provider.capabilities.name}: response failed schema validation twice"
        )

    async def _backoff(self, attempt: int) -> None:
        delay = self._base_backoff * (2**attempt) + random.uniform(0, self._base_backoff)
        log.info("backoff", attempt=attempt, delay=round(delay, 3))
        await self._sleep(delay)

    def quota_report(self) -> list[dict[str, int | str | None]]:
        """Per-provider remaining-quota snapshot for telemetry / the UI strip."""
        return [self._ledger.remaining(p.capabilities) for p in self._providers.values()]


def _valid(text: str, schema: type[BaseModel]) -> bool:
    try:
        schema.model_validate_json(text)
        return True
    except (ValidationError, ValueError):
        return False


def build_router_from_env() -> LLMRouter:
    """Construct a router from env vars, registering only providers whose key is set.

    Lets the app run on whatever free tiers are configured without crashing on the
    missing ones (CLAUDE.md §2: secrets via env only).
    """
    import os

    from .providers.cerebras import CerebrasProvider
    from .providers.gemini import GeminiProvider
    from .providers.groq import GroqProvider
    from .providers.mistral import MistralProvider
    from .providers.sambanova import SambaNovaProvider

    spec: list[tuple[str, str, type]] = [
        ("cerebras", "CEREBRAS_API_KEY", CerebrasProvider),
        ("groq", "GROQ_API_KEY", GroqProvider),
        ("gemini", "GEMINI_API_KEY", GeminiProvider),
        ("sambanova", "SAMBANOVA_API_KEY", SambaNovaProvider),
        ("mistral", "MISTRAL_API_KEY", MistralProvider),
    ]
    providers: dict[str, LLMProvider] = {}
    for name, env_key, cls in spec:
        api_key = os.environ.get(env_key)
        if api_key:
            providers[name] = cls(api_key)
    if not providers:
        log.warning("no_llm_providers_configured")
    return LLMRouter(providers, QuotaLedger())
