"""OpenRouter — breadth / last-resort overflow via the smart free router. OpenAI-compatible."""
from __future__ import annotations

from .base import ProviderCapabilities
from .openai_compat import OpenAICompatProvider


class OpenRouterProvider(OpenAICompatProvider):
    base_url = "https://openrouter.ai/api/v1"
    # OpenRouter is last-resort overflow only (rpd=50). Its free roster is unstable and,
    # as of 2026-06, no longer includes a clean general-purpose *non-reasoning* instruct
    # model — the previous pick was removed (see history below) and the remaining `:free`
    # models are reasoning/coding/agentic. We accept that here because:
    #   1. it's the last provider in every chain (only reached when all others are out),
    #   2. our json_mode calls validate against a schema, so an empty/garbled reasoning
    #      reply fails validation and the router fails over cleanly instead of returning
    #      junk (see llm_router._call + MalformedResponseError).
    #
    # Model history:
    #   "meta-llama/llama-3.3-70b-instruct:free" — REMOVED from /api/v1/models (verified
    #   2026-06-18). A stale id makes OpenRouter 4xx, which (before we logged the body)
    #   looked like a generic failure — a contributor to the "no response" report.
    #
    # Current pick (verified present in /api/v1/models 2026-06-18) — NOTE: OpenRouter
    # tags this as a reasoning/orchestration model, so on tight max_tokens budgets it may
    # spend the budget thinking and return empty `content`; the schema-failover above is
    # the safety net. Re-verify the free roster periodically:
    #   curl https://openrouter.ai/api/v1/models | jq '[.data[].id|select(endswith(":free"))]'
    model = "nvidia/nemotron-3-ultra-550b-a55b:free"
    # OpenRouter recommends these headers for free-tier routing and model availability.
    # They identify the calling app, which can affect access to gated `:free` models.
    extra_headers = {
        "HTTP-Referer": "https://github.com/anishjha/reflect",
        "X-Title": "Reflect",
    }
    capabilities = ProviderCapabilities(
        name="openrouter",
        max_context=131_072,  # conservative cap for routing; model supports 1M
        tags=("overflow",),
        rpm=20,
        rpd=50,
    )
