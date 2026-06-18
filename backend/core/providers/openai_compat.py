"""Shared base for OpenAI-API-compatible providers (Cerebras/Groq/OpenRouter).

All three speak the same `POST {base}/chat/completions` shape, so the HTTP and
parsing logic lives here once; concrete classes only set base_url, model, and
capabilities (CLAUDE.md §4).
"""
from __future__ import annotations

from typing import Any, ClassVar, Mapping, Sequence

import httpx

from .base import (
    LLMProvider,
    LLMResult,
    MalformedResponseError,
    Message,
    ProviderError,
    RateLimitError,
    ServerError,
    TokenUsage,
)

_DEFAULT_TIMEOUT = httpx.Timeout(30.0, connect=10.0)


class OpenAICompatProvider(LLMProvider):
    base_url: ClassVar[str]
    model: ClassVar[str]
    # Subclasses may override to inject extra headers (e.g. OpenRouter ranking headers).
    # Default is empty so Cerebras/Groq behaviour is unchanged.
    extra_headers: ClassVar[Mapping[str, str]] = {}

    def __init__(
        self,
        api_key: str,
        *,
        client: httpx.AsyncClient | None = None,
        model: str | None = None,
    ) -> None:
        self._api_key = api_key
        self._model = model or self.model
        self._client = client or httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT)

    async def complete(
        self,
        messages: Sequence[Message],
        *,
        max_tokens: int,
        json_mode: bool = False,
    ) -> LLMResult:
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [m.model_dump() for m in messages],
            "max_tokens": max_tokens,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        headers: dict[str, str] = {
            "Authorization": f"Bearer {self._api_key}",
            **self.extra_headers,
        }
        try:
            resp = await self._client.post(
                f"{self.base_url}/chat/completions",
                headers=headers,
                json=payload,
            )
        except httpx.HTTPError as exc:  # timeouts, connection errors → recoverable
            raise ServerError(f"{self.capabilities.name}: transport error: {exc}") from exc

        _raise_for_status(self.capabilities.name, resp)
        return self._parse(resp.json())

    def _parse(self, data: dict[str, Any]) -> LLMResult:
        try:
            text = data["choices"][0]["message"]["content"]
            if text is None:
                raise MalformedResponseError(
                    f"{self.capabilities.name}: response content is null"
                )
            usage = data.get("usage") or {}
            return LLMResult(
                text=text,
                usage=TokenUsage(
                    prompt_tokens=usage.get("prompt_tokens", 0),
                    completion_tokens=usage.get("completion_tokens", 0),
                ),
                provider=self.capabilities.name,
                model=self._model,
            )
        except (KeyError, IndexError, TypeError) as exc:
            raise MalformedResponseError(
                f"{self.capabilities.name}: unexpected response shape"
            ) from exc


def _raise_for_status(provider: str, resp: httpx.Response) -> None:
    if resp.status_code == 429:
        raise RateLimitError(f"{provider}: 429 rate limited")
    if resp.status_code >= 500:
        raise ServerError(f"{provider}: {resp.status_code} server error")
    if resp.status_code >= 400:
        # 4xx other than 429 (bad key, bad model id, bad request) — still recoverable
        # via failover. Include truncated body so a stale/unknown model id is diagnosable
        # from logs without having to re-run with debug mode.
        body_snippet = resp.text[:300]
        raise ProviderError(
            f"{provider}: {resp.status_code} client error — {body_snippet}"
        )
