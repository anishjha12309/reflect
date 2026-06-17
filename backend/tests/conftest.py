"""Shared test doubles for agent tests (the router is always mocked — no real LLM)."""
from __future__ import annotations

from typing import Callable, Sequence

from core.providers.base import LLMResult, Message, TokenUsage


class FakeRouter:
    """Stands in for LLMRouter. Replays scripted response texts and records every
    call so tests can assert task_type and inspect the exact prompt sent."""

    def __init__(self, responses: Sequence[str | Callable[[Sequence[Message]], str]]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, object]] = []

    async def complete(
        self,
        messages: Sequence[Message],
        *,
        task_type: str,
        max_tokens: int = 1024,
        json_mode: bool = False,
        schema: object | None = None,
    ) -> LLMResult:
        self.calls.append(
            {
                "messages": list(messages),
                "task_type": task_type,
                "json_mode": json_mode,
            }
        )
        idx = min(len(self.calls) - 1, len(self._responses) - 1)
        text = self._responses[idx]
        if callable(text):
            text = text(messages)
        return LLMResult(text=text, usage=TokenUsage(), provider="fake", model="fake")
