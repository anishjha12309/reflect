"""Page reader agent (CLAUDE.md §9, Search/reading).

Fetch a URL, extract its main text with trafilatura, truncate to a token budget,
and return a typed SourceDocument. Every failure mode — timeout, paywall, non-HTML,
empty extraction — returns None with a logged skip reason. It NEVER raises into the
caller: one bad URL must not stall the orchestration graph.

Duplicate URLs are short-circuited via the semantic cache so we don't refetch.
"""
from __future__ import annotations

import httpx
import structlog
import trafilatura
from pydantic import BaseModel

from core.cache import SemanticCache
from core.providers.base import estimate_tokens

log = structlog.get_logger(__name__)

_CACHE_NS = "reader"
# Paywall / auth walls — treated as a skip, not a retryable error.
_PAYWALL_STATUSES = {401, 402, 403}


class SourceDocument(BaseModel):
    url: str
    title: str | None = None
    text: str
    tokens: int


class Reader:
    def __init__(
        self,
        *,
        client: httpx.AsyncClient | None = None,
        cache: SemanticCache | None = None,
        timeout: float = 10.0,
        max_tokens: int = 4000,
    ) -> None:
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(timeout))
        self._cache = cache
        self._timeout = timeout
        self._max_tokens = max_tokens

    async def read(self, url: str) -> SourceDocument | None:
        # Dedup: a URL we've already read is served from cache (no refetch, no quota).
        if self._cache is not None:
            cached = await self._cache.get(_CACHE_NS, url)
            if cached is not None:
                log.info("reader_cache_hit", url=url)
                return SourceDocument(**cached)

        html = await self._fetch(url)
        if html is None:
            return None

        extracted = trafilatura.extract(html, include_comments=False, include_tables=False)
        if not extracted or not extracted.strip():
            log.info("reader_skip", url=url, reason="empty_extraction")
            return None

        text = self._truncate(extracted)
        doc = SourceDocument(
            url=url,
            title=_extract_title(html),
            text=text,
            tokens=estimate_tokens(text),
        )
        if self._cache is not None:
            await self._cache.put(_CACHE_NS, url, doc.model_dump())
        return doc

    async def _fetch(self, url: str) -> str | None:
        try:
            resp = await self._client.get(
                url, timeout=self._timeout, follow_redirects=True
            )
        except httpx.TimeoutException:
            log.info("reader_skip", url=url, reason="timeout")
            return None
        except httpx.HTTPError as exc:
            log.info("reader_skip", url=url, reason="transport_error", error=str(exc))
            return None

        if resp.status_code in _PAYWALL_STATUSES:
            log.info("reader_skip", url=url, reason="paywall", status=resp.status_code)
            return None
        if resp.status_code >= 400:
            log.info("reader_skip", url=url, reason="http_error", status=resp.status_code)
            return None

        content_type = resp.headers.get("content-type", "").lower()
        if "html" not in content_type:
            log.info("reader_skip", url=url, reason="non_html", content_type=content_type)
            return None

        return resp.text

    def _truncate(self, text: str) -> str:
        if estimate_tokens(text) <= self._max_tokens:
            return text
        # ~4 chars/token (matches the router's pre-flight estimate).
        return text[: self._max_tokens * 4]


def _extract_title(html: str) -> str | None:
    """Best-effort title from page metadata; never fail the read over a missing title."""
    try:
        meta = trafilatura.extract_metadata(html)
    except Exception:  # noqa: BLE001 - metadata is optional, must not break the read
        return None
    return getattr(meta, "title", None) if meta is not None else None
