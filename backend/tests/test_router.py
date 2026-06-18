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
    sambanova = FakeProvider(_cap("sambanova", tags=("overflow",)), [_result("sambanova")])
    router = LLMRouter({"groq": groq, "sambanova": sambanova}, led, sleep=FakeSleep())

    result = await router.complete(_USER, task_type="reasoning", max_tokens=64)

    assert result.provider == "sambanova"
    assert groq.calls == 1 and sambanova.calls == 1
    # groq's failed attempt and sambanova's success are both recorded
    assert led.usage_today("groq")["requests"] == 1
    assert led.usage_today("sambanova")["requests"] == 1


# --- all exhausted → backoff then raise -------------------------------------


async def test_all_providers_exhausted_backs_off_then_raises() -> None:
    led = QuotaLedger(":memory:")
    sleep = FakeSleep()
    groq = FakeProvider(_cap("groq"), [RateLimitError("x")])
    sambanova = FakeProvider(_cap("sambanova", tags=("overflow",)), [RateLimitError("x")])
    router = LLMRouter(
        {"groq": groq, "sambanova": sambanova}, led, max_retries=3, sleep=sleep
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
    # "short" policy is ("cerebras", "groq") — Groq is the reliable-JSON fallback.
    led = QuotaLedger(":memory:")
    cerebras = FakeProvider(_cap("cerebras", max_context=8192, tags=("short",)), [_result("cerebras")])
    groq = FakeProvider(_cap("groq", tags=("reasoning",)), [_result("groq")])
    router = LLMRouter({"cerebras": cerebras, "groq": groq}, led)

    chain = router.pick("short", needed_context_tokens=9000)
    assert "cerebras" not in chain
    assert chain == ["groq"]


async def test_complete_reroutes_oversize_prompt_past_cerebras() -> None:
    # "short" policy is ("cerebras", "groq") — Groq is the reliable-JSON fallback.
    led = QuotaLedger(":memory:")
    cerebras = FakeProvider(_cap("cerebras", max_context=8192, tags=("short",)), [_result("cerebras")])
    groq = FakeProvider(_cap("groq", tags=("reasoning",)), [_result("groq")])
    router = LLMRouter({"cerebras": cerebras, "groq": groq}, led, sleep=FakeSleep())

    # ~9.2K-token prompt by the 4-chars/token estimate → exceeds Cerebras' 8192 cap
    big = [Message(role="user", content="x" * (8192 * 4 + 4000))]
    result = await router.complete(big, task_type="short", max_tokens=64)

    assert result.provider == "groq"
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
    sambanova = FakeProvider(
        _cap("sambanova", tags=("overflow",)), [_result("sambanova", text='{"answer": "ok"}')]
    )
    router = LLMRouter({"groq": groq, "sambanova": sambanova}, led, sleep=FakeSleep())

    result = await router.complete(
        _USER, task_type="reasoning", max_tokens=64, json_mode=True, schema=_Answer
    )

    assert groq.calls == 2  # tried twice on the same provider, then gave up
    assert result.provider == "sambanova"


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
    sambanova = FakeProvider(_cap("sambanova", tags=("overflow",)), [_result("sambanova")])
    router = LLMRouter({"groq": groq, "sambanova": sambanova}, led, sleep=FakeSleep())

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
    assert bucket.time_until() > 0

    clock.advance(30)  # 30s * (2/60) = 1 token
    assert bucket.try_acquire() is True
    assert bucket.try_acquire() is False


async def test_throttled_provider_is_skipped_for_one_with_capacity() -> None:
    led = QuotaLedger(":memory:")
    clock = FakeClock()
    groq = FakeProvider(_cap("groq", rpm=1), [_result("groq"), _result("groq")])
    sambanova = FakeProvider(_cap("sambanova", tags=("overflow",), rpm=60), [_result("sambanova")])
    router = LLMRouter(
        {"groq": groq, "sambanova": sambanova},
        led,
        clock=clock,
        sleep=FakeSleep(),
        rpm_safety=1.0,  # capacity == rpm exactly
    )

    first = await router.complete(_USER, task_type="reasoning", max_tokens=8)
    assert first.provider == "groq"  # consumes groq's single token

    # groq is now at its RPM (clock hasn't moved) → router fails over WITHOUT a 429
    second = await router.complete(_USER, task_type="reasoning", max_tokens=8)
    assert second.provider == "sambanova"
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


# --- per-provider TPM throttle ----------------------------------------------


def test_token_bucket_variable_amount_try_acquire_and_time_until() -> None:
    """TokenBucket.try_acquire(n) and time_until(n) work for amounts > 1."""
    clock = FakeClock()
    bucket = TokenBucket(100, clock=clock)  # capacity=100, refills 100/60 per sec

    # Bucket starts full — should serve a large request immediately.
    assert bucket.capacity == 100.0
    assert bucket.try_acquire(60) is True   # consume 60 → 40 remaining
    assert bucket.try_acquire(50) is False  # 40 < 50 → denied

    # time_until for the denied amount.
    wait = bucket.time_until(50)
    assert wait > 0
    # After advancing by the predicted wait, the amount should become available.
    clock.advance(wait)
    assert bucket.try_acquire(50) is True

    # After draining fully, time_until(1) should be > 0.
    assert bucket.try_acquire(1) is False
    assert bucket.time_until(1) > 0


async def test_token_budget_fails_over_when_provider_lacks_tpm_headroom() -> None:
    """A provider with a small TPM is skipped for a call whose needed tokens exceed its
    remaining token budget; the router fails over to a provider WITHOUT a tpm cap."""
    led = QuotaLedger(":memory:")
    clock = FakeClock()

    # groq: tpm=100, rpm=60 — the first call will drain the TPM bucket.
    groq = FakeProvider(_cap("groq", rpm=60, tpm=100), [_result("groq"), _result("groq")])
    # sambanova: no tpm cap (token-unlimited per-minute) — serves as the failover.
    sambanova = FakeProvider(_cap("sambanova", tags=("overflow",), rpm=60), [_result("sambanova")])
    router = LLMRouter(
        {"groq": groq, "sambanova": sambanova},
        led,
        clock=clock,
        sleep=FakeSleep(),
        rpm_safety=1.0,  # exact capacity so we can predict the bucket precisely
    )

    # First call: small enough to fit in groq's 100-token TPM bucket.
    # _USER has 1 message of "question" (6 chars + 4 overhead ≈ ~6 tokens), max_tokens=8
    # → needed ≈ 14 tokens, well within 100. groq serves it and debits ~14 from the bucket.
    first = await router.complete(_USER, task_type="reasoning", max_tokens=8)
    assert first.provider == "groq"

    # Second call: request max_tokens=90 so that needed = prompt_est(6) + 90 = 96.
    # groq's TPM bucket now has ~86 tokens left (100 - 14); 96 > 86 but 96 <= 100
    # (fits within capacity overall, but NOT within the remaining headroom right now).
    # → groq is skipped via the tok_wait > 0 branch, not the over-capacity branch.
    # sambanova has no TPM cap → it serves this call immediately, no 429 anywhere.
    second = await router.complete(_USER, task_type="reasoning", max_tokens=90)
    assert second.provider == "sambanova"
    assert groq.calls == 1   # groq was NOT called a second time (token headroom exhausted)
    # The skip is throttling, not a failure — groq's failure count stays at 0.
    assert led.usage_today("groq")["requests"] == 1
    assert led.usage_today("sambanova")["requests"] == 1


async def test_call_larger_than_tpm_capacity_skips_provider_permanently() -> None:
    """When a single call's needed tokens exceed the provider's whole TPM bucket capacity,
    that provider is skipped (cannot ever serve it), and if it's the only one the chain
    is exhausted rather than pacing forever."""
    led = QuotaLedger(":memory:")
    clock = FakeClock()

    # groq: tpm=50. We will request max_tokens=60 so that needed > capacity always.
    groq = FakeProvider(_cap("groq", rpm=60, tpm=50), [_result("groq")])
    router = LLMRouter(
        {"groq": groq},
        led,
        clock=clock,
        sleep=FakeSleep(),
        rpm_safety=1.0,
        max_retries=2,
    )

    # needed = prompt_est (~6) + max_tokens (60) = ~66 > tpm_capacity (50).
    # The router must skip groq permanently (over-capacity) rather than waiting forever,
    # and raise AllProvidersExhausted since there's no fallback.
    with pytest.raises(AllProvidersExhausted):
        await router.complete(_USER, task_type="reasoning", max_tokens=60)

    # groq was never actually called — the over-capacity check prevented it.
    assert groq.calls == 0
