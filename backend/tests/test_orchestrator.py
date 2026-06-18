"""Orchestrator: happy path, one re-search loop, partial degradation, bounded concurrency.

All agents are mocked; no network, no real LLM.
"""
import asyncio

from agents.critic import CriticVerdict
from agents.planner import ResearchPlan, SubQuestion
from agents.reader import SourceDocument
from agents.summarizer import Note
from core.providers.base import AllProvidersExhausted, LLMResult, TokenUsage
from core.search import SearchResult, SearchUnavailable
from graph.orchestrator import Orchestrator
from tests.conftest import FakeRouter


# --- shared fakes -----------------------------------------------------------


class FakePlanner:
    def __init__(self, sub_questions: list[SubQuestion]) -> None:
        self._subs = sub_questions

    async def plan(self, topic: str) -> ResearchPlan:
        return ResearchPlan(topic=topic, sub_questions=self._subs)


class ConcurrencyTracker:
    def __init__(self) -> None:
        self.current = 0
        self.max_seen = 0

    async def span(self) -> None:
        self.current += 1
        self.max_seen = max(self.max_seen, self.current)
        await asyncio.sleep(0.01)
        self.current -= 1


class FakeSearch:
    def __init__(self, *, results=None, fail=False, tracker: ConcurrencyTracker | None = None) -> None:
        self._results = results if results is not None else [SearchResult(title="t", url="https://a.com", snippet="s", score=1.0)]
        self._fail = fail
        self._tracker = tracker
        self.calls = 0

    async def search(self, query: str, k: int) -> list[SearchResult]:
        self.calls += 1
        if self._tracker:
            await self._tracker.span()
        if self._fail:
            raise SearchUnavailable("all down")
        # unique URL per query so each task yields its own source
        return [SearchResult(title="t", url=f"https://src/{abs(hash(query)) % 10000}", snippet="s", score=1.0)]


class FakeReader:
    def __init__(self, *, tracker: ConcurrencyTracker | None = None) -> None:
        self._tracker = tracker
        self.calls = 0

    async def read(self, url: str) -> SourceDocument | None:
        self.calls += 1
        if self._tracker:
            await self._tracker.span()
        return SourceDocument(url=url, title="T", text=f"content of {url}", tokens=10)


class FakeSummarizer:
    async def summarize(self, doc: SourceDocument) -> list[Note]:
        return [Note(claim=f"claim from {doc.url}", evidence="ev", source_id=doc.url)]


class ScriptedCritic:
    def __init__(self, verdicts_by_round: dict[int, CriticVerdict]) -> None:
        self._by_round = verdicts_by_round
        self.calls: list[int] = []

    async def review(self, draft_report: str, notes, *, round: int) -> CriticVerdict:
        self.calls.append(round)
        return self._by_round[round]


class RaisingRouter:
    async def complete(self, messages, *, task_type, max_tokens=1024, json_mode=False, schema=None):
        raise AllProvidersExhausted("synthesis down")


def _approve() -> CriticVerdict:
    return CriticVerdict(coverage_score=0.95, contradictions=[], followup_questions=[], approved=True)


def _two_independent() -> list[SubQuestion]:
    return [
        SubQuestion(id="q1", question="first question", depends_on=[]),
        SubQuestion(id="q2", question="second question", depends_on=[]),
    ]


def _orchestrator(**overrides) -> Orchestrator:
    defaults = dict(
        planner=FakePlanner(_two_independent()),
        search=FakeSearch(),
        reader=FakeReader(),
        summarizer=FakeSummarizer(),
        critic=ScriptedCritic({0: _approve()}),
        router=FakeRouter(["# Report\n\nA finding [1]. Another [2]."]),
        max_concurrency=4,
    )
    defaults.update(overrides)
    return Orchestrator(**defaults)


# --- happy path -------------------------------------------------------------


async def test_happy_path_full_graph() -> None:
    state = await _orchestrator().run("renewable energy")

    assert state.partial is False
    assert len(state.notes) == 2  # one per sub-question source
    assert "# Report" in state.final_report
    assert "## Sources" in state.final_report
    assert state.critic_feedback is not None and state.critic_feedback.approved
    event_types = {e.type for e in state.events}
    assert {"plan_ready", "notes_ready", "draft_ready", "critic_verdict", "done"} <= event_types


# --- one bounded re-search loop ---------------------------------------------


async def test_one_research_loop_then_stops() -> None:
    needs_more = CriticVerdict(
        coverage_score=0.3, contradictions=[], followup_questions=["dig deeper into X"], approved=False
    )
    critic = ScriptedCritic({0: needs_more, 1: _approve()})
    state = await _orchestrator(critic=critic).run("topic")

    assert critic.calls == [0, 1]  # exactly one extra round, then stop
    assert state.round == 1
    assert state.critic_feedback is not None and state.critic_feedback.approved
    # the follow-up task was added and executed
    assert any(t.id.startswith("r1q") for t in state.tasks)


# --- partial degradation ----------------------------------------------------


async def test_search_unavailable_yields_partial_report() -> None:
    state = await _orchestrator(search=FakeSearch(fail=True)).run("topic")

    assert state.partial is True
    assert "PARTIAL" in state.final_report
    assert "search_unavailable" in state.partial_reasons
    assert state.notes == []  # nothing to summarize, but no crash


async def test_synthesis_exhausted_yields_partial_report() -> None:
    state = await _orchestrator(router=RaisingRouter()).run("topic")

    assert state.partial is True
    assert "PARTIAL" in state.final_report
    assert "synthesis_exhausted" in state.partial_reasons
    assert len(state.notes) == 2  # notes were gathered; only synthesis failed


# --- bounded concurrency ----------------------------------------------------


async def test_fan_out_respects_semaphore_cap() -> None:
    tracker = ConcurrencyTracker()
    subs = [SubQuestion(id=f"q{i}", question=f"question {i}", depends_on=[]) for i in range(8)]
    orch = _orchestrator(
        planner=FakePlanner(subs),
        search=FakeSearch(tracker=tracker),
        reader=FakeReader(tracker=tracker),
        read_k=1,
        max_concurrency=4,
    )
    await orch.run("topic")

    assert tracker.max_seen <= 4  # never exceeded the cap
    assert tracker.max_seen > 1  # but did run in parallel


# --- critic unavailable (providers exhausted) -----------------------------------


async def test_critic_unavailable_emits_distinct_event() -> None:
    # When the critic can't run (available=False), the orchestrator must emit
    # "critic_unavailable" rather than "critic_verdict", so the UI is not misled into
    # showing "coverage 0% — approved" as if the critic actually scored the report.
    unavailable_verdict = CriticVerdict(
        coverage_score=0.0, contradictions=[], followup_questions=[], approved=True, available=False
    )
    critic = ScriptedCritic({0: unavailable_verdict})
    state = await _orchestrator(critic=critic).run("topic")

    event_types = [e.type for e in state.events]
    assert "critic_unavailable" in event_types
    assert "critic_verdict" not in event_types
    # Pipeline still ends cleanly — partial is False, report is present.
    assert state.partial is False
    assert state.final_report
