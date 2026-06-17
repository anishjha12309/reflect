"""Reflect FastAPI entrypoint (CLAUDE.md §5, §9).

- GET  /health   liveness probe for the HF Spaces keep-alive cron.
- POST /research  runs the orchestrator and streams typed SSE events live.

The research stream emits typed events as the graph progresses, chunks the final
report, surfaces per-provider quota, sends heartbeats so proxies don't drop the
connection, cancels in-flight work on client disconnect, and never leaks a stack
trace to the client.
"""
from __future__ import annotations

import io
import json
import os
import sys
from pathlib import Path
from typing import Any, AsyncIterator, Awaitable, Callable, Protocol

# Load .env before anything reads os.environ (standalone dev without Docker).
from dotenv import load_dotenv

_env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(_env_path)

# Fix Windows cp1252 console: force structlog output to UTF-8 so non-ASCII
# characters in tracebacks don't crash the logger.
if sys.platform == "win32" and not isinstance(sys.stderr, io.TextIOWrapper):
    pass  # already wrapped
elif sys.platform == "win32":
    sys.stderr = io.TextIOWrapper(
        sys.stderr.buffer, encoding="utf-8", errors="replace"
    )
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace"
    )

import structlog
from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator
from sse_starlette.sse import EventSourceResponse

from graph.orchestrator import build_orchestrator_from_env
from graph.state import Event
from settings import allowed_origins

log = structlog.get_logger(__name__)

HEARTBEAT_SECONDS = 15
_REPORT_CHUNK_CHARS = 600

app = FastAPI(title="Reflect", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins(),
    # Any localhost/127.0.0.1 port is allowed in dev (Next picks 3001 if 3000 is taken),
    # while production still relies on the explicit ALLOWED_ORIGINS allowlist above.
    allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ResearchRequest(BaseModel):
    topic: str = Field(min_length=1, max_length=2000)

    @field_validator("topic")
    @classmethod
    def _clean(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("topic must not be empty")
        return v


class _Orchestrator(Protocol):
    def astream(self, topic: str) -> AsyncIterator[Event]: ...


def get_orchestrator() -> _Orchestrator:
    """Dependency seam — overridden in tests with a fake orchestrator."""
    return build_orchestrator_from_env()


@app.get("/health")
async def health() -> dict[str, str]:
    """Liveness probe. Cron pings this ~every 5 min so the Space never sleeps."""
    return {"status": "ok"}


@app.get("/metrics")
async def metrics() -> dict[str, Any]:
    """Quota dashboard data: per-provider remaining quota + today's call time series.

    Reads the shared sqlite ledger directly (no API keys needed), so it works even
    when no research is running.
    """
    from core.providers import all_capabilities
    from core.quota import QuotaLedger

    ledger = QuotaLedger()
    try:
        return {
            "providers": [ledger.remaining(cap) for cap in all_capabilities()],
            "series": ledger.recent_calls(),
        }
    finally:
        ledger.close()


@app.post("/research")
async def research(
    req: ResearchRequest,
    request: Request,
    orchestrator: _Orchestrator = Depends(get_orchestrator),
) -> EventSourceResponse:
    return EventSourceResponse(
        research_event_stream(orchestrator, req.topic, request.is_disconnected),
        ping=HEARTBEAT_SECONDS,
    )


def _chunk_report(report: str) -> list[str]:
    return [report[i : i + _REPORT_CHUNK_CHARS] for i in range(0, len(report), _REPORT_CHUNK_CHARS)] or [""]


async def research_event_stream(
    orchestrator: _Orchestrator,
    topic: str,
    is_disconnected: Callable[[], Awaitable[bool]],
) -> AsyncIterator[dict[str, Any]]:
    """Translate orchestrator Events into SSE frames.

    On the terminal `done` event, surface quota then stream the report in chunks.
    Any backend error becomes a single clean `error` frame (no stack trace). On
    disconnect the underlying generator is closed, cancelling in-flight work.
    """
    agen = orchestrator.astream(topic)
    try:
        async for ev in agen:
            if await is_disconnected():
                log.info("client_disconnected_cancelling")
                break
            if ev.type == "done":
                yield {"event": "quota_update", "data": json.dumps(ev.data.get("quota", []))}
                for chunk in _chunk_report(ev.data.get("report", "")):
                    yield {"event": "report_chunk", "data": json.dumps({"text": chunk})}
                yield {"event": "done", "data": json.dumps({"partial": ev.data.get("partial", False)})}
            else:
                yield {"event": ev.type, "data": json.dumps(ev.data)}
    except Exception:  # noqa: BLE001 - convert any backend failure to a clean client error
        log.exception("research_stream_failed")
        yield {"event": "error", "data": json.dumps({"message": "internal error during research"})}
    finally:
        await agen.aclose()
