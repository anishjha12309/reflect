"""QuotaLedger: recording, daily usage rollup, and exhaustion gating."""
from core.providers.base import ProviderCapabilities
from core.quota import QuotaLedger


def _cap(name: str, **kw: object) -> ProviderCapabilities:
    base: dict[str, object] = {"max_context": 8192, "tags": ("short",)}
    base.update(kw)
    return ProviderCapabilities(name=name, **base)  # type: ignore[arg-type]


def test_record_and_usage_today() -> None:
    led = QuotaLedger(":memory:")
    led.record("groq", "reasoning", 100, 50, success=True)
    usage = led.usage_today("groq")
    assert usage["requests"] == 1
    assert usage["tokens"] == 150


def test_failed_call_counts_request_but_not_tokens() -> None:
    led = QuotaLedger(":memory:")
    led.record("groq", "reasoning", 100, 0, success=False)
    usage = led.usage_today("groq")
    assert usage["requests"] == 1  # a 429 still consumed a request slot
    assert usage["tokens"] == 0  # but produced no tokens


def test_is_exhausted_by_rpd() -> None:
    cap = _cap("mistral", rpd=2, tags=("overflow",))
    led = QuotaLedger(":memory:")
    assert not led.is_exhausted(cap)
    led.record("mistral", "overflow", 1, 1, success=True)
    led.record("mistral", "overflow", 1, 1, success=False)
    assert led.is_exhausted(cap)


def test_is_exhausted_by_tpd() -> None:
    cap = _cap("cerebras", tpd=100)
    led = QuotaLedger(":memory:")
    led.record("cerebras", "short", 60, 50, success=True)  # 110 >= 100
    assert led.is_exhausted(cap)


def test_remaining_snapshot() -> None:
    cap = _cap("cerebras", tpd=1000)
    led = QuotaLedger(":memory:")
    led.record("cerebras", "short", 100, 100, success=True)
    snap = led.remaining(cap)
    assert snap["tokens_used"] == 200
    assert snap["tokens_remaining"] == 800
    assert snap["exhausted"] is False
