"""Cerebras — fastest throughput, but an 8,192-token context cap on the free tier.

That cap is the signature edge case (CLAUDE.md §4, §9): the router must never send
oversize prompts here. Best for short, high-volume tasks (query-gen, per-source notes).
"""
from __future__ import annotations

from .base import ProviderCapabilities
from .openai_compat import OpenAICompatProvider


class CerebrasProvider(OpenAICompatProvider):
    base_url = "https://api.cerebras.ai/v1"
    model = "gpt-oss-120b"
    capabilities = ProviderCapabilities(
        name="cerebras",
        max_context=65_536,  # GPT-OSS-120B free tier context cap
        tags=("short",),
        rpm=30,
        tpd=1_000_000,
    )
