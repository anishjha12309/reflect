"""LLMRouter: policy selection, failover, breaker, backoff, JSON-guard.

Uses in-memory fake providers (capabilities + scripted behaviors) so routing logic
is tested deterministically with no HTTP at all.
"""
from typing import Sequence

import pytest
from pydantic import BaseModel

from core.llm_router import BreakerState, CircuitBreaker, LLMRouter, TokenBucket
from core.providers.base import (
    AllProvidersExhausted,
    LLMProvider,
    LLMResult,
    Message,
    ProviderCapabilities,
    RateLimitError,
    TokenUsage,
)
from core.quota import QuotaLedger


# --- test doubles -----------------------------------------------------------


class FakeProvider(LLMProvider):
    """Replays a scripted list of behaviors: each is an LLMResult or an Exception.
    The last entry repeats if called more times than scripted."""

    def __init__(self, capabilities: ProviderCapabilities, behaviors: list[object]) -> None:
        self.capabilities = capabilities
        self._behaviors = behaviors
        self.calls = 0

    async def complete(
        self, messages: Sequence[Message], *, max_tokens: int, json_mode: bool = False
    ) -> LLMResult:
        behavior = self._behaviors[min(self.calls, len(self._behaviors) - 1)]
        self.calls += 1
        if isinstance(behavior, Exception):
            raise behavior
        assert isinstance(behavior, LLMResult)
        return behavior


class FakeSleep:
    def __init__(self) -> None:
        self.calls: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.calls.append(delay)


class FakeClock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    def advance(self, delta: float) -> None:
        self.t += delta


def _cap(name: str, **kw: object) -> ProviderCapabilities:
    base: dict[str, object] = {"max_context": 32_768, "tags": ("reasoning",)}
    base.update(kw)
    return ProviderCapabilities(name=name, **base)  # type: ignore[arg-type]


def _result(name: str, text: str = "ok") -> LLMResult:
    return LLMResult(
        text=text,
        usage=TokenUsage(prompt_tokens=10, completion_tokens=5),
        provider=name,
        model="m",
    )


_USER = [Message(role="user", content="question")]


# --- happy path -------------------------------------------------------------


async def test_happy_path_returns_and_records() -> None:
    led = QuotaLedger(":memory:")
    groq = FakeProvider(_cap("groq"), [_result("groq")])
    router = LLMRouter({"groq": groq}, led, sleep=FakeSleep())

    result = await router.complete(_USER, task_type="reasoning", max_tokens=64)

    assert result.provider == "groq"
    assert groq.calls == 1
    assert led.usage_today("groq") == {"requests": 1, "tokens": 15}


# --- 429 failover -----------------------------------------------------------


async def test_429_fails_over_to_next_provider() -> None:
    led = QuotaLedger(":memory:")
    groq = FakeProvider(_cap("groq"), [RateLimitError("429")])
    openrouter = FakeProvider(_cap("openrouter", tags=("overflow",)), [_result("openrouter")])
    router = LLMRouter({"groq": groq, "openrouter": openrouter}, led, sleep=FakeSleep())

    result = await router.complete(_USER, task_type="reasoning", max_tokens=64)

    assert result.provider == "openrouter"
    assert groq.calls == 1 and openrouter.calls == 1
    # groq's failed attempt and openrouter's success are both recorded
    assert led.usage_today("groq")["requests"] == 1
    assert led.usage_today("openrouter")["requests"] == 1


# --- all exhausted → backoff then raise -------------------------------------


async def test_all_providers_exhausted_backs_off_then_raises() -> None:
    led = QuotaLedger(":memory:")
    sleep = FakeSleep()
    groq = FakeProvider(_cap("groq"), [RateLimitError("x")])
    openrouter = FakeProvider(_cap("openrouter", tags=("overflow",)), [RateLimitError("x")])
    router = LLMRouter(
        {"groq": groq, "openrouter": openrouter}, led, max_retries=3, sleep=sleep
    )

    with pytest.raises(AllProvidersExhausted):
        await router.complete(_USER, task_type="reasoning", max_tokens=64)

    # backed off between attempts (max_retries-1 times), with growing delay
    assert len(sleep.calls) == 2
    assert sleep.calls[1] >= sleep.calls[0]


# --- Cerebras 8K context-overflow exclusion ---------------------------------


def test_pick_includes_cerebras_for_small_context() -> None:
    led = QuotaLedger(":memory:")
    cerebras = FakeProvider(_cap("cerebras", max_context=8192, tags=("short",)), [_result("cerebras")])
    groq = FakeProvider(_cap("groq", tags=("reasoning",)), [_result("groq")])
    router = LLMRouter({"cerebras": cerebras, "groq": groq}, led)

    assert router.pick("short", needed_context_tokens=1000)[0] == "cerebras"


def test_pick_excludes_cerebras_when_context_overflows() -> None:
    # "short" policy is now ("cerebras", "openrouter") — groq is reserved for "reasoning".
    led = QuotaLedger(":memory:")
    cerebras = FakeProvider(_cap("cerebras", max_context=8192, tags=("short",)), [_result("cerebras")])
    openrouter = FakeProvider(_cap("openrouter"), [_result("openrouter")])
    router = LLMRouter({"cerebras": cerebras, "openrouter": openrouter}, led)

    chain = router.pick("short", needed_context_tokens=9000)
    assert "cerebras" not in chain
    assert chain == ["openrouter"]


async def test_complete_reroutes_oversize_prompt_past_cerebras() -> None:
    # "short" policy is now ("cerebras", "openrouter") — groq is reserved for "reasoning".
    led = QuotaLedger(":memory:")
    cerebras = FakeProvider(_cap("cerebras", max_context=8192, tags=("short",)), [_result("cerebras")])
    openrouter = FakeProvider(_cap("openrouter"), [_result("openrouter")])
    router = LLMRouter({"cerebras": cerebras, "openrouter": openrouter}, led, sleep=FakeSleep())

    # ~9.2K-token prompt by the 4-chars/token estimate → exceeds Cerebras' 8192 cap
    big = [Message(role="user", content="x" * (8192 * 4 + 4000))]
    result = await router.complete(big, task_type="short", max_tokens=64)

    assert result.provider == "openrouter"
    assert cerebras.calls == 0  # never even attempted


# --- malformed JSON retry ---------------------------------------------------


class _Answer(BaseModel):
    answer: str


async def test_malformed_json_retries_once_same_provider() -> None:
    led = QuotaLedger(":memory:")
    groq = FakeProvider(
        _cap("groq"),
        [_result("groq", text="not json"), _result("groq", text='{"answer": "hi"}')],
    )
    router = LLMRouter({"groq": groq}, led)

    result = await router.complete(
        _USER, task_type="reasoning", max_tokens=64, json_mode=True, schema=_Answer
    )

    assert groq.calls == 2  # first invalid, second valid after the reminder
    assert result.provider == "groq"


async def test_malformed_json_twice_fails_over() -> None:
    led = QuotaLedger(":memory:")
    groq = FakeProvider(
        _cap("groq"),
        [_result("groq", text="nope"), _result("groq", text="still nope")],
    )
    openrouter = FakeProvider(
        _cap("openrouter", tags=("overflow",)), [_result("openrouter", text='{"answer": "ok"}')]
    )
    router = LLMRouter({"groq": groq, "openrouter": openrouter}, led, sleep=FakeSleep())

    result = await router.complete(
        _USER, task_type="reasoning", max_tokens=64, json_mode=True, schema=_Answer
    )

    assert groq.calls == 2  # tried twice on the same provider, then gave up
    assert result.provider == "openrouter"


# --- circuit breaker transitions --------------------------------------------


def test_breaker_opens_after_threshold() -> None:
    breaker = CircuitBreaker(failure_threshold=2, cooldown=10, clock=FakeClock())
    assert breaker.allow()
    breaker.record_failure()
    assert breaker.allow()  # one failure, still closed
    breaker.record_failure()
    assert not breaker.allow()  # second failure → open
    assert breaker.state is BreakerState.OPEN


def test_breaker_half_opens_after_cooldown_then_closes_on_success() -> None:
    clock = FakeClock()
    breaker = CircuitBreaker(failure_threshold=1, cooldown=10, clock=clock)
    breaker.record_failure()  # open at t=0
    assert not breaker.allow()

    clock.advance(10)  # cooldown elapsed
    assert breaker.allow()  # half-open probe permitted
    assert breaker.state is BreakerState.HALF_OPEN

    breaker.record_success()
    assert breaker.state is BreakerState.CLOSED


def test_breaker_half_open_failure_reopens() -> None:
    clock = FakeClock()
    breaker = CircuitBreaker(failure_threshold=1, cooldown=10, clock=clock)
    breaker.record_failure()  # open at t=0
    clock.advance(10)
    assert breaker.state is BreakerState.HALF_OPEN

    breaker.record_failure()  # failed probe → re-open immediately
    assert breaker.state is BreakerState.OPEN
    assert not breaker.allow()


def test_pick_excludes_open_breaker() -> None:
    led = QuotaLedger(":memory:")
    groq = FakeProvider(_cap("groq"), [_result("groq")])
    router = LLMRouter({"groq": groq}, led, breaker_threshold=1)

    assert router.pick("reasoning", needed_context_tokens=100) == ["groq"]
    router._breakers["groq"].record_failure()  # trips the breaker (threshold=1)
    assert router.pick("reasoning", needed_context_tokens=100) == []


async def test_exhausted_provider_excluded_from_pick() -> None:
    led = QuotaLedger(":memory:")
    groq = FakeProvider(_cap("groq", rpd=1), [_result("groq")])
    openrouter = FakeProvider(_cap("openrouter", tags=("overflow",)), [_result("openrouter")])
    router = LLMRouter({"groq": groq, "openrouter": openrouter}, led, sleep=FakeSleep())

    # first call uses groq and exhausts its rpd=1
    first = await router.complete(_USER, task_type="reasoning", max_tokens=8)
    assert first.provider == "groq"
    # second call must skip the now-exhausted groq
    assert "groq" not in router.pick("reasoning", needed_context_tokens=100)


# --- per-provider RPM throttle ----------------------------------------------


class FakeTime:
    """Shared clock whose async sleep advances time — so a throttle wait actually
    refills the token bucket in tests (no real sleeping)."""

    def __init__(self) -> None:
        self.t = 0.0

    def clock(self) -> float:
        return self.t

    async def sleep(self, delay: float) -> None:
        self.t += delay


def test_token_bucket_allows_capacity_then_throttles_then_refills() -> None:
    clock = FakeClock()
    bucket = TokenBucket(2, clock=clock)  # capacity 2, refills 2/60 per second

    assert bucket.try_acquire() is True
    assert bucket.try_acquire() is True
    assert bucket.try_acquire() is False  # drained
    assert bucket.time_until_token() > 0

    clock.advance(30)  # 30s * (2/60) = 1 token
    assert bucket.try_acquire() is True
    assert bucket.try_acquire() is False


async def test_throttled_provider_is_skipped_for_one_with_capacity() -> None:
    led = QuotaLedger(":memory:")
    clock = FakeClock()
    groq = FakeProvider(_cap("groq", rpm=1), [_result("groq"), _result("groq")])
    openrouter = FakeProvider(_cap("openrouter", tags=("overflow",), rpm=60), [_result("openrouter")])
    router = LLMRouter(
        {"groq": groq, "openrouter": openrouter},
        led,
        clock=clock,
        sleep=FakeSleep(),
        rpm_safety=1.0,  # capacity == rpm exactly
    )

    first = await router.complete(_USER, task_type="reasoning", max_tokens=8)
    assert first.provider == "groq"  # consumes groq's single token

    # groq is now at its RPM (clock hasn't moved) → router fails over WITHOUT a 429
    second = await router.complete(_USER, task_type="reasoning", max_tokens=8)
    assert second.provider == "openrouter"
    assert groq.calls == 1  # groq never even called the 2nd time
    # no failures recorded — throttling is not a 429
    assert led.usage_today("groq")["requests"] == 1


async def test_all_throttled_paces_then_succeeds_without_429() -> None:
    led = QuotaLedger(":memory:")
    ft = FakeTime()
    groq = FakeProvider(_cap("groq", rpm=1), [_result("groq"), _result("groq")])
    router = LLMRouter(
        {"groq": groq},
        led,
        clock=ft.clock,
        sleep=ft.sleep,
        rpm_safety=1.0,
        max_throttle_wait=100.0,
    )

    await router.complete(_USER, task_type="reasoning", max_tokens=8)  # drains the token
    result = await router.complete(_USER, task_type="reasoning", max_tokens=8)

    assert result.provider == "groq"
    assert groq.calls == 2
    assert ft.t >= 60  # it paced (~60s to refill) instead of 429-ing
    assert led.usage_today("groq")["requests"] == 2  # both succeeded, no failures
