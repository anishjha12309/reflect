"""SambaNova Cloud, free no-card tier, fast OpenAI-compatible.

Llama-3.3-70B reliably emits JSON (verified live 2026-06-18) → reliable-JSON fallback
for the summarizer + reasoning tasks.
"""
from __future__ import annotations

from .base import ProviderCapabilities
from .openai_compat import OpenAICompatProvider


class SambaNovaProvider(OpenAICompatProvider):
    base_url = "https://api.sambanova.ai/v1"
    model = "Meta-Llama-3.3-70B-Instruct"
    capabilities = ProviderCapabilities(
        name="sambanova",
        max_context=16_384,
        tags=("short", "reasoning"),
        rpm=20,
    )
