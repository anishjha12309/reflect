"""Semantic dedup cache (CLAUDE.md §4, §9) — conserves free quota.

Keyed by a query embedding. Before a search or a page-summarize, callers check the
cache for a near-duplicate query; on a hit they reuse the stored payload and spend
NO provider quota. Backed by sqlite (under /tmp on HF Spaces).

Embedding source: Gemini `text-embedding-004` (free tier) when GEMINI_API_KEY is set,
otherwise a local sentence-transformers model (lazy-imported; optional dependency).
The cache must never crash the pipeline: if embedding fails it degrades to an
exact-text-match cache (and a miss), never an exception into the caller.
"""
from __future__ import annotations

import json
import math
import os
import sqlite3
import tempfile
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Sequence

import httpx
import structlog

log = structlog.get_logger(__name__)


class EmbeddingError(Exception):
    """Embedder could not produce a vector (network/parse/model error)."""


# --- embedders --------------------------------------------------------------


class Embedder(ABC):
    @abstractmethod
    async def embed(self, text: str) -> list[float]:
        raise NotImplementedError


class GeminiEmbedder(Embedder):
    base_url = "https://generativelanguage.googleapis.com/v1beta"
    model = "gemini-embedding-2"

    def __init__(self, api_key: str, *, client: httpx.AsyncClient | None = None) -> None:
        self._api_key = api_key
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(20.0))

    async def embed(self, text: str) -> list[float]:
        try:
            resp = await self._client.post(
                f"{self.base_url}/models/{self.model}:embedContent",
                params={"key": self._api_key},
                json={"model": f"models/{self.model}", "content": {"parts": [{"text": text}]}},
            )
        except httpx.HTTPError as exc:
            raise EmbeddingError(f"gemini embed transport error: {exc}") from exc
        if resp.status_code >= 400:
            raise EmbeddingError(f"gemini embed HTTP {resp.status_code}")
        try:
            return list(resp.json()["embedding"]["values"])
        except (KeyError, TypeError) as exc:
            raise EmbeddingError("gemini embed: unexpected response shape") from exc


class SentenceTransformerEmbedder(Embedder):
    """Local, zero-external-call embedder. Lazily loads the model on first use so
    the heavy dependency is only required when actually selected."""

    def __init__(self, model_name: str = "all-MiniLM-L6-v2") -> None:
        self._model_name = model_name
        self._model: Any | None = None

    def _ensure_model(self) -> Any:
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as exc:  # optional dependency
                raise EmbeddingError(
                    "sentence-transformers not installed; set GEMINI_API_KEY or "
                    "`pip install sentence-transformers`"
                ) from exc
            self._model = SentenceTransformer(self._model_name)
        return self._model

    async def embed(self, text: str) -> list[float]:
        model = self._ensure_model()
        return [float(x) for x in model.encode(text)]


def build_embedder_from_env() -> Embedder:
    key = os.environ.get("GEMINI_API_KEY")
    if key:
        return GeminiEmbedder(key)
    return SentenceTransformerEmbedder()


# --- cache ------------------------------------------------------------------


def _default_db_path() -> str:
    env = os.environ.get("CACHE_DB_PATH")
    if env:
        return env
    return str(Path(tempfile.gettempdir()) / "reflect_cache.sqlite")


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


class SemanticCache:
    def __init__(
        self,
        embedder: Embedder,
        *,
        db_path: str | None = None,
        threshold: float = 0.92,
    ) -> None:
        self._embedder = embedder
        self._threshold = threshold
        self._conn = sqlite3.connect(db_path or _default_db_path(), check_same_thread=False)
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                namespace TEXT NOT NULL,
                query TEXT NOT NULL,
                embedding TEXT NOT NULL,
                payload TEXT NOT NULL,
                ts REAL NOT NULL
            )
            """
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_entries_ns_query ON entries(namespace, query)"
        )
        self._conn.commit()

    async def get(self, namespace: str, query: str) -> Any | None:
        """Return a cached payload for an exact or near-duplicate query, else None."""
        # 1. Exact-text fast path — no embedding spend on a literal repeat.
        row = self._conn.execute(
            "SELECT payload FROM entries WHERE namespace = ? AND query = ? "
            "ORDER BY ts DESC LIMIT 1",
            (namespace, query),
        ).fetchone()
        if row is not None:
            return json.loads(row[0])

        # 2. Semantic near-duplicate scan.
        emb = await self._safe_embed(query)
        if emb is None:
            return None
        best_payload: str | None = None
        best_sim = -1.0
        for stored_emb, payload in self._conn.execute(
            "SELECT embedding, payload FROM entries WHERE namespace = ?", (namespace,)
        ):
            sim = _cosine(emb, json.loads(stored_emb))
            if sim > best_sim:
                best_sim, best_payload = sim, payload
        if best_payload is not None and best_sim >= self._threshold:
            log.info("cache_semantic_hit", namespace=namespace, similarity=round(best_sim, 4))
            return json.loads(best_payload)
        return None

    async def put(self, namespace: str, query: str, payload: Any) -> None:
        emb = await self._safe_embed(query)
        self._conn.execute(
            "INSERT INTO entries (namespace, query, embedding, payload, ts) "
            "VALUES (?, ?, ?, ?, ?)",
            (namespace, query, json.dumps(emb or []), json.dumps(payload), time.time()),
        )
        self._conn.commit()

    async def _safe_embed(self, text: str) -> list[float] | None:
        try:
            return await self._embedder.embed(text)
        except EmbeddingError as exc:
            log.warning("embed_failed_degrading_to_exact_match", error=str(exc))
            return None

    def close(self) -> None:
        self._conn.close()
