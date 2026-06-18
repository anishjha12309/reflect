"""Search facade: fallback chain, normalization, cache short-circuit.

Facade-level tests use fake providers; concrete provider parsing is tested with
httpx mocked so NO real API is hit.
"""
import httpx
import pytest

from core.search import (
    OpenAlexProvider,
    SearchFacade,
    SearchProvider,
    SearchProviderError,
    SearchRateLimitError,
    SearchResult,
    SearchUnavailable,
    SearxngProvider,
    SerperProvider,
    TavilyProvider,
    build_search_from_env,
)


# --- test doubles -----------------------------------------------------------


class FakeProvider(SearchProvider):
    def __init__(self, name: str, behavior: object) -> None:
        self.name = name
        self._behavior = behavior
        self.calls = 0

    async def search(self, query: str, k: int) -> list[SearchResult]:
        self.calls += 1
        if isinstance(self._behavior, Exception):
            raise self._behavior
        assert isinstance(self._behavior, list)
        return self._behavior


def _hit(name: str) -> list[SearchResult]:
    return [SearchResult(title=name, url=f"https://{name}.com", snippet="s", score=1.0)]


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# --- fallback chain ---------------------------------------------------------


async def test_tavily_success_short_circuits_chain() -> None:
    tavily = FakeProvider("tavily", _hit("tavily"))
    serper = FakeProvider("serper", _hit("serper"))
    facade = SearchFacade([tavily, serper])

    results = await facade.search("q", k=5)

    assert results[0].title == "tavily"
    assert tavily.calls == 1 and serper.calls == 0  # second provider untouched


async def test_tavily_429_falls_over_to_serper() -> None:
    tavily = FakeProvider("tavily", SearchRateLimitError("429"))
    serper = FakeProvider("serper", _hit("serper"))
    facade = SearchFacade([tavily, serper])

    results = await facade.search("q")

    assert results[0].title == "serper"
    assert tavily.calls == 1 and serper.calls == 1


async def test_all_fail_falls_through_to_searxng() -> None:
    tavily = FakeProvider("tavily", SearchProviderError("down"))
    serper = FakeProvider("serper", SearchRateLimitError("429"))
    searxng = FakeProvider("searxng", _hit("searxng"))
    facade = SearchFacade([tavily, serper, searxng])

    results = await facade.search("q")

    assert results[0].title == "searxng"
    assert searxng.calls == 1


async def test_all_down_raises_search_unavailable() -> None:
    facade = SearchFacade(
        [
            FakeProvider("tavily", SearchProviderError("x")),
            FakeProvider("serper", SearchProviderError("x")),
            FakeProvider("searxng", SearchProviderError("x")),
        ]
    )
    with pytest.raises(SearchUnavailable):
        await facade.search("q")


# --- cache short-circuit ----------------------------------------------------


class FakeCache:
    """Minimal cache double: exact-key store, records hits/misses."""

    def __init__(self) -> None:
        self._store: dict[tuple[str, str], object] = {}

    async def get(self, namespace: str, query: str):
        return self._store.get((namespace, query))

    async def put(self, namespace: str, query: str, payload) -> None:
        self._store[(namespace, query)] = payload


async def test_cache_hit_prevents_second_provider_call() -> None:
    tavily = FakeProvider("tavily", _hit("tavily"))
    facade = SearchFacade([tavily], cache=FakeCache())

    first = await facade.search("same query")
    second = await facade.search("same query")

    assert tavily.calls == 1  # second call served from cache — no network
    assert first[0].url == second[0].url


async def test_cache_miss_stores_result() -> None:
    cache = FakeCache()
    tavily = FakeProvider("tavily", _hit("tavily"))
    facade = SearchFacade([tavily], cache=cache)

    await facade.search("novel query")

    assert await cache.get("search", "novel query") is not None


# --- concrete provider parsing (httpx mocked) -------------------------------


async def test_tavily_provider_parses_and_normalizes() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.tavily.com"
        return httpx.Response(
            200,
            json={"results": [{"title": "T", "url": "https://x.com", "content": "c", "score": 0.9}]},
        )

    provider = TavilyProvider("key", client=_client(handler))
    out = await provider.search("q", k=5)
    assert out == [SearchResult(title="T", url="https://x.com", snippet="c", score=0.9)]


async def test_tavily_provider_429_raises() -> None:
    provider = TavilyProvider("key", client=_client(lambda r: httpx.Response(429, json={})))
    with pytest.raises(SearchRateLimitError):
        await provider.search("q", k=5)


async def test_tavily_provider_skips_rows_without_url() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"results": [{"title": "no url"}, {"title": "ok", "url": "https://y.com"}]}
        )

    provider = TavilyProvider("key", client=_client(handler))
    out = await provider.search("q", k=5)
    assert [r.url for r in out] == ["https://y.com"]


async def test_serper_provider_maps_link_and_position_score() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["x-api-key"] == "key"
        return httpx.Response(
            200,
            json={"organic": [{"title": "A", "link": "https://a.com", "snippet": "s", "position": 2}]},
        )

    provider = SerperProvider("key", client=_client(handler))
    out = await provider.search("q", k=5)
    assert out[0].url == "https://a.com"
    assert out[0].score == 0.5  # 1 / position


async def test_searxng_provider_parses_json() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params.get("format") == "json"
        return httpx.Response(
            200, json={"results": [{"title": "S", "url": "https://s.com", "content": "c", "score": 0.3}]}
        )

    provider = SearxngProvider("http://localhost:8080", client=_client(handler))
    out = await provider.search("q", k=5)
    assert out[0].url == "https://s.com"
    assert out[0].score == 0.3


# --- OpenAlex (scholarly, abstract-as-content) ------------------------------


async def test_openalex_reconstructs_abstract_and_carries_content() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.openalex.org"
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "title": "Nuclear vs Solar",
                        "doi": "https://doi.org/10.1/abc",
                        "cited_by_count": 42,
                        # inverted index, intentionally out of order
                        "abstract_inverted_index": {"beats": [1], "Solar": [0], "coal": [2]},
                    }
                ]
            },
        )

    provider = OpenAlexProvider(client=_client(handler))
    out = await provider.search("q", k=5)
    assert len(out) == 1
    assert out[0].title == "Nuclear vs Solar"
    assert out[0].content == "Solar beats coal"  # rebuilt in position order
    assert out[0].url == "https://doi.org/10.1/abc"  # DOI used for citation
    assert out[0].score == 42.0  # cited_by_count


async def test_openalex_skips_works_without_abstract() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "results": [
                    {"title": "no abstract", "abstract_inverted_index": None, "doi": "https://doi.org/10.1/x"},
                    {"title": "ok", "abstract_inverted_index": {"hello": [0]}, "doi": "https://doi.org/10.1/y"},
                ]
            },
        )

    provider = OpenAlexProvider(client=_client(handler))
    out = await provider.search("q", k=5)
    assert [r.title for r in out] == ["ok"]  # the abstract-less work is dropped


async def test_openalex_prefers_open_access_url() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "title": "OA paper",
                        "doi": "https://doi.org/10.1/z",
                        "open_access": {"oa_url": "https://repo.edu/paper.pdf"},
                        "abstract_inverted_index": {"open": [0]},
                    }
                ]
            },
        )

    provider = OpenAlexProvider(client=_client(handler))
    out = await provider.search("q", k=5)
    assert out[0].url == "https://repo.edu/paper.pdf"  # OA url beats DOI


def test_build_search_puts_openalex_first() -> None:
    facade = build_search_from_env()
    assert facade._providers[0].name == "openalex"
