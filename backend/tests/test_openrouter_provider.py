"""Unit tests for OpenRouterProvider.

Verifies that:
- The required OpenRouter identification headers are sent on every request.
- The Authorization header is correctly set.
- Extra headers do NOT appear on other OpenAI-compat providers (regression guard).
- A non-429 4xx error includes the response body in the message (diagnosable model id).

All tests use httpx.MockTransport — no real network calls.
"""
from __future__ import annotations

import httpx
import pytest

from core.providers.base import Message, ProviderError, RateLimitError
from core.providers.cerebras import CerebrasProvider
from core.providers.openrouter import OpenRouterProvider


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# ---------------------------------------------------------------------------
# Header assertions
# ---------------------------------------------------------------------------


async def test_openrouter_sends_identification_headers() -> None:
    """HTTP-Referer and X-Title must be present on every OpenRouter request."""
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["referer"] = request.headers.get("http-referer", "")
        captured["title"] = request.headers.get("x-title", "")
        captured["auth"] = request.headers.get("authorization", "")
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "ok"}}], "usage": {}},
        )

    provider = OpenRouterProvider("or-key", client=_client(handler))
    result = await provider.complete(
        [Message(role="user", content="hello")], max_tokens=16
    )
    assert result.text == "ok"
    assert captured["referer"] == "https://github.com/anishjha/reflect"
    assert captured["title"] == "Reflect"
    assert captured["auth"] == "Bearer or-key"


async def test_openrouter_authorization_header_correct() -> None:
    """Authorization header must be 'Bearer <key>' regardless of extra headers."""
    captured_auth: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_auth.append(request.headers.get("authorization", ""))
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "y"}}]},
        )

    provider = OpenRouterProvider("my-secret-key", client=_client(handler))
    await provider.complete([Message(role="user", content="q")], max_tokens=8)
    assert captured_auth == ["Bearer my-secret-key"]


# ---------------------------------------------------------------------------
# Regression: other providers must NOT get OpenRouter's extra headers
# ---------------------------------------------------------------------------


async def test_cerebras_does_not_send_openrouter_headers() -> None:
    """extra_headers default is empty — CerebrasProvider must not send HTTP-Referer."""
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["referer"] = request.headers.get("http-referer", "NOT_PRESENT")
        captured["title"] = request.headers.get("x-title", "NOT_PRESENT")
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "pong"}}]},
        )

    provider = CerebrasProvider("cb-key", client=_client(handler))
    await provider.complete([Message(role="user", content="ping")], max_tokens=8)
    assert captured["referer"] == "NOT_PRESENT"
    assert captured["title"] == "NOT_PRESENT"


# ---------------------------------------------------------------------------
# Error observability: 4xx body included in ProviderError
# ---------------------------------------------------------------------------


async def test_openrouter_4xx_includes_body_in_error() -> None:
    """A 404 (e.g. stale model id) must surface the response body in the exception."""
    error_body = '{"error": {"message": "model not found", "code": 404}}'

    provider = OpenRouterProvider(
        "key",
        client=_client(
            lambda r: httpx.Response(404, text=error_body)
        ),
    )
    with pytest.raises(ProviderError) as exc_info:
        await provider.complete([Message(role="user", content="hi")], max_tokens=16)

    msg = str(exc_info.value)
    assert "404" in msg
    assert "model not found" in msg


async def test_openrouter_4xx_body_truncated_to_300_chars() -> None:
    """Body snippet in ProviderError must not exceed 300 characters."""
    long_body = "x" * 500

    provider = OpenRouterProvider(
        "key",
        client=_client(lambda r: httpx.Response(400, text=long_body)),
    )
    with pytest.raises(ProviderError) as exc_info:
        await provider.complete([Message(role="user", content="hi")], max_tokens=16)

    # The error message has provider prefix + status + snippet; snippet <= 300 chars
    msg = str(exc_info.value)
    # The raw snippet extracted from the error message body portion
    # Strip everything before the first "x" to isolate the snippet
    snippet_start = msg.index("x")
    snippet = msg[snippet_start:]
    assert len(snippet) <= 300


async def test_openrouter_429_still_raises_ratelimit_not_provider_error() -> None:
    """429 must still raise RateLimitError (not ProviderError) after the body change."""
    provider = OpenRouterProvider(
        "key",
        client=_client(lambda r: httpx.Response(429, text='{"error": "rate limited"}')),
    )
    with pytest.raises(RateLimitError):
        await provider.complete([Message(role="user", content="hi")], max_tokens=16)
