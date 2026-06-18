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
    # Full source text already in hand (e.g. an OpenAlex abstract). When present the
    # reader uses it directly and skips the HTTP fetch — dodging paywalls and PDFs.
    content: str = ""


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


class OpenAlexProvider(SearchProvider):
    """Scholarly search over OpenAlex (https://openalex.org) — free, no API key.

    Returns peer-reviewed / academic works and carries each work's ABSTRACT as
    `content`, so the reader uses it directly and skips the HTTP fetch. That both
    dodges paywalls/PDFs (the usual reason good academic sources get dropped) and
    biases the corpus toward research instead of SEO / marketing pages. Placed FIRST
    in the chain so scholarly sources win; Tavily/Serper remain as open-web fallbacks.
    """

    name = "openalex"
    url = "https://api.openalex.org/works"

    def __init__(
        self,
        *,
        mailto: str | None = None,
        api_key: str | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._mailto = mailto  # "polite pool" (faster, more reliable) — recommended
        self._api_key = api_key  # optional OpenAlex premium key
        self._client = client or httpx.AsyncClient(timeout=_TIMEOUT)

    async def search(self, query: str, k: int) -> list[SearchResult]:
        # OpenAlex treats ? and * as wildcards and returns HTTP 400 on them in its
        # default (stemmed) search. Our queries are natural-language questions, so strip
        # those characters before sending.
        safe_query = query.replace("?", " ").replace("*", " ").strip()
        params: dict[str, Any] = {
            "search": safe_query,
            "per_page": min(max(k * 2, k), 25),  # over-fetch; many works lack abstracts
            "sort": "relevance_score:desc",
        }
        if self._mailto:
            params["mailto"] = self._mailto
        if self._api_key:
            params["api_key"] = self._api_key
        try:
            resp = await self._client.get(self.url, params=params)
        except httpx.HTTPError as exc:
            raise SearchProviderError(f"openalex: transport error: {exc}") from exc
        _raise_for_status("openalex", resp)

        out: list[SearchResult] = []
        for work in resp.json().get("results", []) or []:
            abstract = _reconstruct_abstract(work.get("abstract_inverted_index"))
            if not abstract:
                continue  # no abstract → no usable content for us; skip
            out.append(
                SearchResult(
                    title=work.get("title") or work.get("display_name") or "",
                    url=_openalex_url(work),
                    snippet=abstract[:300],
                    content=abstract,
                    score=float(work.get("cited_by_count", 0) or 0),
                )
            )
            if len(out) >= k:
                break
        return out


def _reconstruct_abstract(inverted_index: dict[str, list[int]] | None) -> str:
    """OpenAlex stores abstracts as an inverted index {word: [positions]}; rebuild text."""
    if not inverted_index:
        return ""
    positioned = sorted(
        (pos, word) for word, positions in inverted_index.items() for pos in positions
    )
    return " ".join(word for _, word in positioned)


def _openalex_url(work: dict[str, Any]) -> str:
    """Best citation URL: open-access landing/PDF → DOI → OpenAlex work id."""
    oa = work.get("open_access") or {}
    if oa.get("oa_url"):
        return oa["oa_url"]
    if work.get("doi"):
        return work["doi"]  # OpenAlex returns a full https://doi.org/... URL
    return work.get("id", "")


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
    """Build the chain from whatever providers are configured, in policy order.

    OpenAlex (scholarly, keyless) leads so research papers win over SEO pages; Tavily
    → Serper → SearXNG follow as open-web fallbacks. OPENALEX_MAILTO (your email) opts
    into OpenAlex's faster "polite pool"; OPENALEX_API_KEY is optional (premium).
    """
    providers: list[SearchProvider] = [
        OpenAlexProvider(
            mailto=os.environ.get("OPENALEX_MAILTO"),
            api_key=os.environ.get("OPENALEX_API_KEY"),
        )
    ]
    if (key := os.environ.get("TAVILY_API_KEY")):
        providers.append(TavilyProvider(key))
    if (key := os.environ.get("SERPER_API_KEY")):
        providers.append(SerperProvider(key))
    if (url := os.environ.get("SEARXNG_URL")):
        providers.append(SearxngProvider(url))
    return SearchFacade(providers, cache=cache)
