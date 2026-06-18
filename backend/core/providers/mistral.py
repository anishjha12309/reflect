"""Mistral La Plateforme, free no-card tier, clean instruct JSON (verified live 2026-06-18)."""
from __future__ import annotations

from .base import ProviderCapabilities
from .openai_compat import OpenAICompatProvider


class MistralProvider(OpenAICompatProvider):
    base_url = "https://api.mistral.ai/v1"
    model = "mistral-small-latest"
    capabilities = ProviderCapabilities(
        name="mistral",
        max_context=32_768,
        tags=("short", "reasoning", "overflow"),
        rpm=30,
    )
