"""Groq — mid reasoning tier (planner, critic). Open-weight models, OpenAI-compatible."""
from __future__ import annotations

from .base import ProviderCapabilities
from .openai_compat import OpenAICompatProvider


class GroqProvider(OpenAICompatProvider):
    base_url = "https://api.groq.com/openai/v1"
    model = "llama-3.3-70b-versatile"
    capabilities = ProviderCapabilities(
        name="groq",
        max_context=32_768,
        tags=("reasoning",),
        rpm=30,
        rpd=14_400,
        tpm=6_000,
    )
