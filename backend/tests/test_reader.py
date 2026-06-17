"""Reader agent: extraction happy path + every skip mode returns None (never raises).

httpx is mocked so no real URL is fetched. Dedup uses a real SemanticCache wired to
a fake embedder (no embedding network call).
"""
import httpx

from agents.reader import Reader, SourceDocument
from core.cache import SemanticCache

_ARTICLE = """
<html><head><title>Solar Power Basics</title></head>
<body>
<article>
<h1>How Solar Panels Work</h1>
<p>Photovoltaic cells convert sunlight directly into electricity using semiconducting
materials. When photons strike the cell, they knock electrons loose, and this flow of
electrons generates a direct current that can power homes and businesses.</p>
<p>Modern panels reach efficiencies above twenty percent, and costs have fallen sharply
over the past decade, making solar one of the cheapest sources of new electricity in many
regions of the world today.</p>
</article>
</body></html>
"""


class FakeEmbedder:
    async def embed(self, text: str) -> list[float]:
        return [1.0, 0.0, 0.0]


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _html_response(body: str, status: int = 200) -> httpx.Response:
    return httpx.Response(status, text=body, headers={"content-type": "text/html; charset=utf-8"})


# --- happy path -------------------------------------------------------------


async def test_good_html_extracts_document() -> None:
    reader = Reader(client=_client(lambda r: _html_response(_ARTICLE)))
    doc = await reader.read("https://example.com/solar")

    assert isinstance(doc, SourceDocument)
    assert "Photovoltaic" in doc.text
    assert doc.tokens > 0
    assert doc.url == "https://example.com/solar"


async def test_long_document_is_truncated_to_budget() -> None:
    big = "<html><body><article>" + "<p>" + ("word " * 20000) + "</p></article></body></html>"
    reader = Reader(client=_client(lambda r: _html_response(big)), max_tokens=500)
    doc = await reader.read("https://example.com/long")

    assert doc is not None
    assert doc.tokens <= 500


# --- skip modes (all return None, none raise) -------------------------------


async def test_timeout_returns_none() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out", request=request)

    reader = Reader(client=_client(handler))
    assert await reader.read("https://slow.example.com") is None


async def test_403_paywall_returns_none() -> None:
    reader = Reader(client=_client(lambda r: _html_response("nope", status=403)))
    assert await reader.read("https://paywall.example.com") is None


async def test_non_html_content_type_returns_none() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"%PDF-1.7", headers={"content-type": "application/pdf"})

    reader = Reader(client=_client(handler))
    assert await reader.read("https://example.com/file.pdf") is None


async def test_empty_extraction_returns_none() -> None:
    reader = Reader(client=_client(lambda r: _html_response("<html><body></body></html>")))
    assert await reader.read("https://empty.example.com") is None


async def test_transport_error_returns_none() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    reader = Reader(client=_client(handler))
    assert await reader.read("https://down.example.com") is None


# --- duplicate-URL dedup ----------------------------------------------------


async def test_duplicate_url_served_from_cache_without_refetch() -> None:
    fetches = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        fetches["count"] += 1
        return _html_response(_ARTICLE)

    cache = SemanticCache(FakeEmbedder(), db_path=":memory:")
    reader = Reader(client=_client(handler), cache=cache)

    first = await reader.read("https://example.com/solar")
    second = await reader.read("https://example.com/solar")

    assert first is not None and second is not None
    assert first.text == second.text
    assert fetches["count"] == 1  # second read hit the cache — no network
