"""SemanticCache: exact-match, semantic near-duplicate hits, and graceful
degradation when the embedder fails."""
import pytest

from core.cache import EmbeddingError, GeminiEmbedder, SemanticCache


class FakeEmbedder:
    def __init__(self, mapping: dict[str, list[float]] | None = None) -> None:
        self._mapping = mapping or {}
        self.calls = 0

    async def embed(self, text: str) -> list[float]:
        self.calls += 1
        return self._mapping.get(text, [1.0, 0.0, 0.0])


class BrokenEmbedder:
    def __init__(self) -> None:
        self.calls = 0

    async def embed(self, text: str) -> list[float]:
        self.calls += 1
        raise EmbeddingError("boom")


def _cache(embedder, **kw) -> SemanticCache:
    return SemanticCache(embedder, db_path=":memory:", **kw)


async def test_miss_returns_none() -> None:
    cache = _cache(FakeEmbedder())
    assert await cache.get("search", "anything") is None


async def test_exact_match_hit_skips_embedding() -> None:
    embedder = FakeEmbedder()
    cache = _cache(embedder)
    await cache.put("search", "query one", [{"url": "https://a.com"}])
    embedder.calls = 0  # reset after the put

    hit = await cache.get("search", "query one")

    assert hit == [{"url": "https://a.com"}]
    assert embedder.calls == 0  # exact-text fast path, no embedding spent


async def test_semantic_near_duplicate_hit() -> None:
    # Two different strings mapped to the same vector → cosine 1.0 ≥ threshold.
    same_vec = [0.0, 1.0, 0.0]
    embedder = FakeEmbedder({"climate change effects": same_vec, "effects of climate change": same_vec})
    cache = _cache(embedder, threshold=0.9)
    await cache.put("search", "climate change effects", [{"url": "https://c.com"}])

    hit = await cache.get("search", "effects of climate change")

    assert hit == [{"url": "https://c.com"}]


async def test_below_threshold_is_miss() -> None:
    embedder = FakeEmbedder({"a": [1.0, 0.0], "b": [0.0, 1.0]})  # orthogonal → cosine 0
    cache = _cache(embedder, threshold=0.5)
    await cache.put("search", "a", [{"url": "https://a.com"}])

    assert await cache.get("search", "b") is None


async def test_namespaces_are_isolated() -> None:
    embedder = FakeEmbedder()
    cache = _cache(embedder)
    await cache.put("search", "q", [{"url": "https://a.com"}])

    assert await cache.get("summarize", "q") is None


async def test_embedder_failure_degrades_to_exact_match_not_crash() -> None:
    embedder = BrokenEmbedder()
    cache = _cache(embedder)
    # put still works (stores with empty embedding), get falls back to exact text
    await cache.put("search", "q", [{"url": "https://a.com"}])
    assert await cache.get("search", "q") == [{"url": "https://a.com"}]
    # a different query can't match semantically (embedder broken) → clean miss
    assert await cache.get("search", "different") is None


# --- Gemini embedder HTTP layer (mocked) ------------------------------------


async def test_gemini_embedder_parses_values() -> None:
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        assert "embedContent" in request.url.path
        assert request.url.params.get("key") == "key"
        return httpx.Response(200, json={"embedding": {"values": [0.1, 0.2, 0.3]}})

    embedder = GeminiEmbedder("key", client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    assert await embedder.embed("text") == [0.1, 0.2, 0.3]


async def test_gemini_embedder_http_error_raises_embeddingerror() -> None:
    import httpx

    embedder = GeminiEmbedder(
        "key",
        client=httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(429, json={}))),
    )
    with pytest.raises(EmbeddingError):
        await embedder.embed("text")
