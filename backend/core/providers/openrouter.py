"""OpenRouter — breadth / last-resort overflow via the smart free router. OpenAI-compatible."""
from __future__ import annotations

from .base import ProviderCapabilities
from .openai_compat import OpenAICompatProvider


class OpenRouterProvider(OpenAICompatProvider):
    base_url = "https://openrouter.ai/api/v1"
    # A non-reasoning instruct model: returns the answer directly in `content`.
    # (The auto "openrouter/free" route lands on a reasoning model whose content is
    #  empty when reasoning eats the token budget — see provider notes.)
    model = "meta-llama/llama-3.3-70b-instruct:free"
    capabilities = ProviderCapabilities(
        name="openrouter",
        max_context=131_072,
        tags=("overflow",),
        rpm=20,
        rpd=50,
    )
