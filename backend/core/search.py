"""Search facade with a provider fallback chain (CLAUDE.md §4, §9).

Tavily → Serper → self-hosted SearXNG. On any provider quota/HTTP/transport error
we fall through to the next; if every provider fails, raise SearchUnavailable so the
caller can degrade to "partial report" rather than crash. All three response shapes
are normalized to one SearchResult.

An optional SemanticCache (core/cache.py) short-circuits the chain on a near-duplicate
query so we never burn search quota twice for the same question.
"""
from __future__ import annotations

import os
from abc import ABC, abstractmethod
from typing import Any, Sequence

import httpx
import structlog
from pydantic import BaseModel

from .cache import SemanticCache

log = structlog.get_logger(__name__)

_TIMEOUT = httpx.Timeout(15.0, connect=10.0)


class SearchResult(BaseModel):
    title: str
    url: str
    snippet: str
    score: float = 0.0


# --- error taxonomy (mirrors the provider layer) ---------------------------


class SearchError(Exception):
    """Base for all search errors."""


class SearchProviderError(SearchError):
    """Recoverable single-provider failure — fall through to the next provider."""


class SearchRateLimitError(SearchProviderError):
    """HTTP 429 from a search provider."""


class SearchUnavailable(SearchError):
    """Every provider in the chain failed. Caller degrades to existing sources."""


def _result(item: dict[str, Any], *, url_key: str, snippet_key: str, score: float) -> SearchResult | None:
    """Build a SearchResult, dropping items with no URL (never stall on a bad row)."""
    url = item.get(url_key)
    if not url:
        return None
    return SearchResult(
        title=item.get("title", "") or "",
        url=url,
        snippet=item.get(snippet_key, "") or "",
        score=score,
    )


def _raise_for_status(provider: str, resp: httpx.Response) -> None:
    if resp.status_code == 429:
        raise SearchRateLimitError(f"{provider}: 429 rate limited")
    if resp.status_code >= 400:
        raise SearchProviderError(f"{provider}: HTTP {resp.status_code}")


# --- providers --------------------------------------------------------------


class SearchProvider(ABC):
    name: str

    @abstractmethod
    async def search(self, query: str, k: int) -> list[SearchResult]:
        raise NotImplementedError


class TavilyProvider(SearchProvider):
    name = "tavily"
    url = "https://api.tavily.com/search"

    def __init__(self, api_key: str, *, client: httpx.AsyncClient | None = None) -> None:
        self._api_key = api_key
        self._client = client or httpx.AsyncClient(timeout=_TIMEOUT)

    async def search(self, query: str, k: int) -> list[SearchResult]:
        try:
            resp = await self._client.post(
                self.url,
                json={"api_key": self._api_key, "query": query, "max_results": k},
            )
        except httpx.HTTPError as exc:
            raise SearchProviderError(f"tavily: transport error: {exc}") from exc
        _raise_for_status("tavily", resp)
        items = resp.json().get("results", []) or []
        results = [_result(i, url_key="url", snippet_key="content", score=i.get("score", 0.0) or 0.0) for i in items]
        return [r for r in results if r is not None][:k]


class SerperProvider(SearchProvider):
    name = "serper"
    url = "https://google.serper.dev/search"

    def __init__(self, api_key: str, *, client: httpx.AsyncClient | None = None) -> None:
        self._api_key = api_key
        self._client = client or httpx.AsyncClient(timeout=_TIMEOUT)

    async def search(self, query: str, k: int) -> list[SearchResult]:
        try:
            resp = await self._client.post(
                self.url,
                headers={"X-API-KEY": self._api_key},
                json={"q": query, "num": k},
            )
        except httpx.HTTPError as exc:
            raise SearchProviderError(f"serper: transport error: {exc}") from exc
        _raise_for_status("serper", resp)
        items = resp.json().get("organic", []) or []
        out: list[SearchResult] = []
        for idx, item in enumerate(items, start=1):
            position = item.get("position", idx) or idx
            # rank → score in (0, 1]; position 1 is best
            r = _result(item, url_key="link", snippet_key="snippet", score=1.0 / position)
            if r is not None:
                out.append(r)
        return out[:k]


class SearxngProvider(SearchProvider):
    name = "searxng"

    def __init__(self, base_url: str, *, client: httpx.AsyncClient | None = None) -> None:
        self._base_url = base_url.rstrip("/")
        self._client = client or httpx.AsyncClient(timeout=_TIMEOUT)

    async def search(self, query: str, k: int) -> list[SearchResult]:
        try:
            resp = await self._client.get(
                f"{self._base_url}/search", params={"q": query, "format": "json"}
            )
        except httpx.HTTPError as exc:
            raise SearchProviderError(f"searxng: transport error: {exc}") from exc
        _raise_for_status("searxng", resp)
        items = resp.json().get("results", []) or []
        results = [_result(i, url_key="url", snippet_key="content", score=i.get("score", 0.0) or 0.0) for i in items]
        return [r for r in results if r is not None][:k]


# --- facade -----------------------------------------------------------------


class SearchFacade:
    def __init__(
        self,
        providers: Sequence[SearchProvider],
        *,
        cache: SemanticCache | None = None,
    ) -> None:
        self._providers = list(providers)
        self._cache = cache

    async def search(self, query: str, k: int = 5) -> list[SearchResult]:
        if self._cache is not None:
            cached = await self._cache.get("search", query)
            if cached is not None:
                log.info("search_cache_hit", query=query)
                return [SearchResult(**row) for row in cached]

        results = await self._run_chain(query, k)

        if self._cache is not None:
            await self._cache.put("search", query, [r.model_dump() for r in results])
        return results

    async def _run_chain(self, query: str, k: int) -> list[SearchResult]:
        last_error: Exception | None = None
        for provider in self._providers:
            try:
                return await provider.search(query, k)
            except SearchProviderError as exc:
                last_error = exc
                log.warning("search_provider_failed", provider=provider.name, error=str(exc))
                continue
        raise SearchUnavailable(
            f"all search providers failed for query={query!r}"
        ) from last_error


def build_search_from_env(*, cache: SemanticCache | None = None) -> SearchFacade:
    """Build the chain from whatever providers are configured, in policy order."""
    providers: list[SearchProvider] = []
    if (key := os.environ.get("TAVILY_API_KEY")):
        providers.append(TavilyProvider(key))
    if (key := os.environ.get("SERPER_API_KEY")):
        providers.append(SerperProvider(key))
    if (url := os.environ.get("SEARXNG_URL")):
        providers.append(SearxngProvider(url))
    if not providers:
        log.warning("no_search_providers_configured")
    return SearchFacade(providers, cache=cache)
