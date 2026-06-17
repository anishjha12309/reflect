"""Gemini (AI Studio) — the only ~1M-context provider; reserved for final synthesis.

Not OpenAI-compatible: uses the generateContent REST endpoint with a different
request/response shape (CLAUDE.md §4). Lowest RPD, so the router routes only
long_synthesis here.
"""
from __future__ import annotations

from typing import Any, ClassVar, Sequence

import httpx

from .base import (
    LLMProvider,
    LLMResult,
    MalformedResponseError,
    Message,
    ProviderCapabilities,
    ProviderError,
    RateLimitError,
    ServerError,
    TokenUsage,
)

_DEFAULT_TIMEOUT = httpx.Timeout(60.0, connect=10.0)


class GeminiProvider(LLMProvider):
    base_url: ClassVar[str] = "https://generativelanguage.googleapis.com/v1beta"
    model: ClassVar[str] = "gemini-2.5-flash"
    capabilities = ProviderCapabilities(
        name="gemini",
        max_context=1_000_000,
        tags=("long_synthesis",),
        rpd=250,  # Flash free tier
        tpm=250_000,
    )

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
        contents, system_instruction = self._to_gemini(messages)
        gen_config: dict[str, Any] = {"maxOutputTokens": max_tokens}
        if json_mode:
            gen_config["responseMimeType"] = "application/json"
        payload: dict[str, Any] = {"contents": contents, "generationConfig": gen_config}
        if system_instruction:
            payload["systemInstruction"] = {"parts": [{"text": system_instruction}]}

        try:
            resp = await self._client.post(
                f"{self.base_url}/models/{self._model}:generateContent",
                params={"key": self._api_key},
                json=payload,
            )
        except httpx.HTTPError as exc:
            raise ServerError(f"gemini: transport error: {exc}") from exc

        if resp.status_code == 429:
            raise RateLimitError("gemini: 429 rate limited")
        if resp.status_code >= 500:
            raise ServerError(f"gemini: {resp.status_code} server error")
        if resp.status_code >= 400:
            raise ProviderError(f"gemini: {resp.status_code} client error")

        return self._parse(resp.json())

    @staticmethod
    def _to_gemini(messages: Sequence[Message]) -> tuple[list[dict[str, Any]], str | None]:
        """Map OpenAI-style messages → Gemini contents + a merged systemInstruction."""
        contents: list[dict[str, Any]] = []
        system_parts: list[str] = []
        for m in messages:
            if m.role == "system":
                system_parts.append(m.content)
                continue
            role = "user" if m.role == "user" else "model"
            contents.append({"role": role, "parts": [{"text": m.content}]})
        system = "\n".join(system_parts) if system_parts else None
        return contents, system

    def _parse(self, data: dict[str, Any]) -> LLMResult:
        try:
            text = data["candidates"][0]["content"]["parts"][0]["text"]
            usage = data.get("usageMetadata") or {}
            return LLMResult(
                text=text,
                usage=TokenUsage(
                    prompt_tokens=usage.get("promptTokenCount", 0),
                    completion_tokens=usage.get("candidatesTokenCount", 0),
                ),
                provider=self.capabilities.name,
                model=self._model,
            )
        except (KeyError, IndexError, TypeError) as exc:
            raise MalformedResponseError("gemini: unexpected response shape") from exc
