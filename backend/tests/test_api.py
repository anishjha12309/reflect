"""/research SSE endpoint: ordered events, disconnect cancellation, clean error frame."""
import asyncio
import json

from fastapi.testclient import TestClient

from app import app, get_orchestrator, research_event_stream
from graph.state import Event


# --- fakes ------------------------------------------------------------------


class ScriptedOrchestrator:
    def __init__(self, events: list[Event]) -> None:
        self._events = events
        self.cleaned = False

    async def astream(self, topic: str):
        try:
            for ev in self._events:
                yield ev
        finally:
            self.cleaned = True


class InfiniteOrchestrator:
    def __init__(self) -> None:
        self.cleaned = False
        self.emitted = 0

    async def astream(self, topic: str):
        try:
            while True:
                self.emitted += 1
                yield Event(type="task_done", data={"i": self.emitted})
                await asyncio.sleep(0)
        finally:
            self.cleaned = True


class BoomOrchestrator:
    async def astream(self, topic: str):
        yield Event(type="plan_ready", data={})
        raise RuntimeError("SECRET internal stacktrace detail")


_DONE = Event(type="done", data={"partial": False, "report": "# Report\n\nBody [1].", "quota": [{"provider": "groq"}]})


# --- happy path via HTTP ----------------------------------------------------


def _event_types(sse_body: str) -> list[str]:
    return [line.split("event:", 1)[1].strip() for line in sse_body.splitlines() if line.startswith("event:")]


def test_research_streams_events_in_order() -> None:
    events = [
        Event(type="plan_ready", data={"tasks": 2}),
        Event(type="notes_ready", data={"total": 2}),
        Event(type="draft_ready", data={"partial": False}),
        Event(type="critic_verdict", data={"approved": True}),
        _DONE,
    ]
    app.dependency_overrides[get_orchestrator] = lambda: ScriptedOrchestrator(events)
    try:
        client = TestClient(app)
        resp = client.post("/research", json={"topic": "renewable energy"})
        assert resp.status_code == 200
        types = _event_types(resp.text)
        assert types[0] == "plan_ready"
        assert types.index("draft_ready") < types.index("quota_update")
        assert "report_chunk" in types
        assert types[-1] == "done"  # terminal event last
    finally:
        app.dependency_overrides.clear()


def test_research_rejects_empty_topic() -> None:
    client = TestClient(app)
    resp = client.post("/research", json={"topic": "   "})
    assert resp.status_code == 422


def test_health_still_ok() -> None:
    assert TestClient(app).get("/health").json() == {"status": "ok"}


def test_metrics_returns_provider_snapshot_and_series(tmp_path, monkeypatch) -> None:
    from core.quota import QuotaLedger

    monkeypatch.setenv("QUOTA_DB_PATH", str(tmp_path / "q.sqlite"))
    led = QuotaLedger()  # same env-configured path the endpoint will read
    led.record("groq", "reasoning", 100, 50, success=True)
    led.record("cerebras", "short", 10, 0, success=False)
    led.close()

    data = TestClient(app).get("/metrics").json()

    names = {p["provider"] for p in data["providers"]}
    assert {"cerebras", "groq", "gemini", "sambanova", "mistral"} <= names
    groq = next(p for p in data["providers"] if p["provider"] == "groq")
    assert groq["tokens_used"] == 150
    assert len(data["series"]) == 2
    assert data["series"][0]["provider"] == "groq"  # chronological order


# --- disconnect cancels in-flight work --------------------------------------


async def test_disconnect_breaks_stream_and_cleans_up() -> None:
    orch = InfiniteOrchestrator()
    calls = {"n": 0}

    async def is_disconnected() -> bool:
        calls["n"] += 1
        return calls["n"] > 2  # disconnect after a couple of events

    frames = [f async for f in research_event_stream(orch, "topic", is_disconnected)]

    assert len(frames) == 2  # stopped early
    assert orch.cleaned is True  # generator closed → in-flight work cancelled


# --- error path emits a clean error event -----------------------------------


async def test_error_path_emits_clean_error_event() -> None:
    async def never_disconnected() -> bool:
        return False

    frames = [f async for f in research_event_stream(BoomOrchestrator(), "topic", never_disconnected)]

    assert frames[0]["event"] == "plan_ready"
    assert frames[-1]["event"] == "error"
    payload = json.loads(frames[-1]["data"])
    assert "SECRET" not in payload["message"]  # no stack trace / internals leaked
