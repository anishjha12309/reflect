"""Provider HTTP/parse layer, with httpx mocked so NO real API is ever hit."""
import json

import httpx
import pytest

from core.providers.base import (
    MalformedResponseError,
    Message,
    RateLimitError,
    ServerError,
)
from core.providers.cerebras import CerebrasProvider
from core.providers.gemini import GeminiProvider


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# --- OpenAI-compatible providers (Cerebras/Groq/OpenRouter share this code) ---


async def test_openai_compat_happy_path() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/chat/completions")
        assert request.headers["authorization"] == "Bearer key"
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "hello"}}],
                "usage": {"prompt_tokens": 11, "completion_tokens": 2},
            },
        )

    provider = CerebrasProvider("key", client=_client(handler))
    result = await provider.complete([Message(role="user", content="hi")], max_tokens=16)
    assert result.text == "hello"
    assert result.provider == "cerebras"
    assert result.usage.prompt_tokens == 11
    assert result.usage.total_tokens == 13


async def test_openai_compat_429_raises_ratelimit() -> None:
    provider = CerebrasProvider(
        "key", client=_client(lambda r: httpx.Response(429, json={}))
    )
    with pytest.raises(RateLimitError):
        await provider.complete([Message(role="user", content="hi")], max_tokens=16)


async def test_openai_compat_5xx_raises_servererror() -> None:
    provider = CerebrasProvider(
        "key", client=_client(lambda r: httpx.Response(503, json={}))
    )
    with pytest.raises(ServerError):
        await provider.complete([Message(role="user", content="hi")], max_tokens=16)


async def test_openai_compat_missing_choices_is_malformed() -> None:
    provider = CerebrasProvider(
        "key", client=_client(lambda r: httpx.Response(200, json={"usage": {}}))
    )
    with pytest.raises(MalformedResponseError):
        await provider.complete([Message(role="user", content="hi")], max_tokens=16)


async def test_openai_compat_json_mode_sets_response_format() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})

    provider = CerebrasProvider("key", client=_client(handler))
    await provider.complete(
        [Message(role="user", content="hi")], max_tokens=16, json_mode=True
    )
    assert captured["body"]["response_format"] == {"type": "json_object"}  # type: ignore[index]


# --- Gemini (different REST shape) ---


async def test_gemini_happy_path_maps_system_instruction() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert "generateContent" in request.url.path
        assert request.url.params.get("key") == "key"
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "candidates": [{"content": {"parts": [{"text": "answer"}]}}],
                "usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 3},
            },
        )

    provider = GeminiProvider("key", client=_client(handler))
    result = await provider.complete(
        [Message(role="system", content="be brief"), Message(role="user", content="q")],
        max_tokens=32,
    )
    assert result.text == "answer"
    assert result.provider == "gemini"
    assert result.usage.completion_tokens == 3
    body = captured["body"]
    assert body["systemInstruction"]["parts"][0]["text"] == "be brief"  # type: ignore[index]
    # system message must NOT leak into contents as a turn
    assert all(c["role"] != "system" for c in body["contents"])  # type: ignore[index]


async def test_gemini_429_raises_ratelimit() -> None:
    provider = GeminiProvider(
        "key", client=_client(lambda r: httpx.Response(429, json={}))
    )
    with pytest.raises(RateLimitError):
        await provider.complete([Message(role="user", content="q")], max_tokens=16)


async def test_gemini_json_mode_sets_mime_type() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200, json={"candidates": [{"content": {"parts": [{"text": "{}"}]}}]}
        )

    provider = GeminiProvider("key", client=_client(handler))
    await provider.complete(
        [Message(role="user", content="q")], max_tokens=16, json_mode=True
    )
    cfg = captured["body"]["generationConfig"]  # type: ignore[index]
    assert cfg["responseMimeType"] == "application/json"
